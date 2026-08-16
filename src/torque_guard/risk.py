from __future__ import annotations

import csv
import hashlib
import json
import math
import re
import unicodedata
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import fmean
from typing import Any, Iterable

from .knowledge import KnowledgeBundle, KnowledgeBase
from .models import Action, CandidateCause, Evidence, MetricAvailability, RiskCard
from .spc import RuleHit, capability_snapshot, safe_mean, safe_std, western_electric_rules


FLOAT_FIELDS = {
    "torque_nm",
    "angle_deg",
    "current_a",
    "cycle_time_s",
}
INTEGER_FIELDS = {"retry_count", "calibration_days_remaining"}
NUMERIC_FIELDS = FLOAT_FIELDS | INTEGER_FIELDS
REQUIRED_IDENTIFIER_FIELDS = {
    "event_id",
    "station_id",
    "tool_id",
    "model_code",
    "program_id",
    "fastening_point",
    "batch_id",
}
REQUIRED_EVENT_FIELDS = REQUIRED_IDENTIFIER_FIELDS | {
    "timestamp",
    "torque_nm",
    "angle_deg",
    "retry_count",
    "calibration_days_remaining",
}

DEFAULT_EVENT_TIMEZONE = timezone(timedelta(hours=8))
RISK_POLICY_VERSION = "risk-policy-2.0"
NORMALIZED_WINDOW_SCHEMA = "torque-event-window-2.0"
FULL_ISO_DATETIME = re.compile(
    r"^\d{4}-\d{2}-\d{2}[Tt ]\d{2}:\d{2}:\d{2}"
    r"(?:\.\d{1,6})?(?:[zZ]|[+-]\d{2}:\d{2})?$"
)
ANALYSIS_HASH_FIELDS = (
    "event_id",
    "timestamp",
    "station_id",
    "tool_id",
    "model_code",
    "program_id",
    "fastening_point",
    "batch_id",
    "torque_nm",
    "angle_deg",
    "current_a",
    "cycle_time_s",
    "retry_count",
    "alarm_code",
    "calibration_days_remaining",
)


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _canonical_rfc3339(value: datetime) -> str:
    utc = value.astimezone(timezone.utc)
    timespec = "microseconds" if utc.microsecond else "seconds"
    return utc.isoformat(timespec=timespec).replace("+00:00", "Z")


def _parse_event_timestamp(value: Any, *, event_index: int | None = None) -> datetime:
    label = f"输入第 {event_index} 条事件" if event_index is not None else "输入事件"
    if not isinstance(value, str):
        raise ValueError(f"{label}的 timestamp 必须是 ISO 8601 字符串")
    raw = value.strip()
    if not FULL_ISO_DATETIME.fullmatch(raw):
        raise ValueError(
            f"{label}的 timestamp 必须是包含日期、时分秒的完整 ISO 8601 时间：{raw!r}"
        )
    normalized = raw[:-1] + "+00:00" if raw[-1:] in {"Z", "z"} else raw
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"{label}的 timestamp 不是有效 ISO 8601 时间：{raw!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=DEFAULT_EVENT_TIMEZONE)
    return parsed.astimezone(timezone.utc)


def _normalize_identifier(value: Any, *, event_index: int, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"输入第 {event_index} 条事件字段 {field!r} 必须是字符串")
    normalized = unicodedata.normalize("NFC", value.strip())
    if not normalized:
        raise ValueError(f"输入第 {event_index} 条事件字段 {field!r} 不能为空")
    if len(normalized) > 256:
        raise ValueError(f"输入第 {event_index} 条事件字段 {field!r} 长度不能超过 256")
    return normalized


def _normalize_float(
    value: Any,
    *,
    event_index: int,
    field: str,
    optional: bool = False,
) -> float | None:
    if optional and (value is None or (isinstance(value, str) and not value.strip())):
        return None
    if isinstance(value, bool):
        raise ValueError(f"输入第 {event_index} 条事件字段 {field!r} 不是有效数字")
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"输入第 {event_index} 条事件字段 {field!r} 不是有效数字：{value!r}"
        ) from exc
    if not math.isfinite(numeric):
        raise ValueError(f"输入第 {event_index} 条事件字段 {field!r} 必须是有限数字")
    ranges = {
        "torque_nm": (0.0, 10000.0, False),
        "angle_deg": (0.0, 36000.0, True),
        "current_a": (0.0, 10000.0, True),
        "cycle_time_s": (0.0, 86400.0, False),
    }
    lower, upper, lower_inclusive = ranges[field]
    lower_ok = numeric >= lower if lower_inclusive else numeric > lower
    if not lower_ok or numeric > upper:
        bracket = "[" if lower_inclusive else "("
        raise ValueError(
            f"输入第 {event_index} 条事件字段 {field!r} 超出合理范围 {bracket}{lower}, {upper}]"
        )
    return 0.0 if numeric == 0 else numeric


def _normalize_nonnegative_integer(
    value: Any,
    *,
    event_index: int,
    field: str,
) -> int:
    if isinstance(value, bool):
        raise ValueError(f"输入第 {event_index} 条事件字段 {field!r} 必须是非负整数")
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"输入第 {event_index} 条事件字段 {field!r} 必须是非负整数：{value!r}"
        ) from exc
    maximum = 10000 if field == "retry_count" else 36500
    if not math.isfinite(numeric) or not numeric.is_integer() or not 0 <= numeric <= maximum:
        raise ValueError(
            f"输入第 {event_index} 条事件字段 {field!r} 必须是 0..{maximum} 的整数"
        )
    return int(numeric)


def _normalize_event(row: dict[str, Any], *, event_index: int) -> dict[str, Any]:
    if not isinstance(row, dict):
        raise ValueError(f"输入第 {event_index} 条事件必须是对象")
    missing = sorted(REQUIRED_EVENT_FIELDS - set(row))
    if missing:
        raise ValueError(f"输入第 {event_index} 条事件缺少字段：{', '.join(missing)}")
    normalized = dict(row)
    for field in REQUIRED_IDENTIFIER_FIELDS:
        normalized[field] = _normalize_identifier(
            row.get(field), event_index=event_index, field=field
        )
    timestamp = _parse_event_timestamp(row.get("timestamp"), event_index=event_index)
    normalized["timestamp"] = _canonical_rfc3339(timestamp)
    normalized["_timestamp_utc"] = timestamp
    for field in FLOAT_FIELDS:
        normalized[field] = _normalize_float(
            row.get(field),
            event_index=event_index,
            field=field,
            optional=field in {"current_a", "cycle_time_s"},
        )
    for field in INTEGER_FIELDS:
        normalized[field] = _normalize_nonnegative_integer(
            row.get(field), event_index=event_index, field=field
        )
    alarm = row.get("alarm_code", "")
    if alarm is None:
        alarm = ""
    if not isinstance(alarm, str):
        raise ValueError(f"输入第 {event_index} 条事件字段 'alarm_code' 必须是字符串")
    normalized["alarm_code"] = unicodedata.normalize("NFC", alarm.strip())
    return normalized


def _metric_availability(
    baseline_values: list[float],
    recent_values: list[float],
    *,
    baseline_required: int,
    recent_required: int,
) -> MetricAvailability:
    available = (
        len(baseline_values) == baseline_required and len(recent_values) == recent_required
    )
    return {
        "available": available,
        "baseline_sample_count": len(baseline_values),
        "baseline_required_count": baseline_required,
        "recent_sample_count": len(recent_values),
        "recent_required_count": recent_required,
        "reason": None if available else "窗口存在缺失值，指标未参与评分与推理",
    }


def read_events(path: str | Path) -> list[dict[str, Any]]:
    event_path = Path(path)
    with event_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError(f"CSV {event_path} 缺少表头")
        if len(reader.fieldnames) != len(set(reader.fieldnames)):
            raise ValueError(f"CSV {event_path} 存在重复列名")
        rows = list(reader)
    for row_number, row in enumerate(rows, start=2):
        if None in row:
            raise ValueError(f"CSV 第 {row_number} 行列数多于表头")
        for key in NUMERIC_FIELDS:
            raw_value = row.get(key, "")
            if isinstance(raw_value, str):
                raw_value = raw_value.strip()
            if raw_value == "":
                row[key] = None
                continue
            try:
                value = float(raw_value)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"CSV 第 {row_number} 行字段 {key!r} 不是有效数字：{raw_value!r}"
                ) from exc
            if not math.isfinite(value):
                raise ValueError(f"CSV 第 {row_number} 行字段 {key!r} 必须是有限数字")
            row[key] = value
    return rows


class RiskAnalyzer:
    def __init__(self, knowledge_root: str | Path, baseline_count: int = 100, recent_count: int = 24):
        if isinstance(baseline_count, bool) or not isinstance(baseline_count, int) or baseline_count < 2:
            raise ValueError("baseline_count 必须是大于等于 2 的整数")
        if isinstance(recent_count, bool) or not isinstance(recent_count, int) or recent_count < 1:
            raise ValueError("recent_count 必须是大于等于 1 的整数")
        self.knowledge = KnowledgeBase(knowledge_root)
        self.baseline_count = baseline_count
        self.recent_count = recent_count

    def analyze(
        self,
        events: list[dict[str, Any]],
        fastening_point: str,
        *,
        event_source: str = "in_memory_events",
    ) -> RiskCard:
        if not events:
            raise ValueError("事件列表不能为空")
        if not isinstance(fastening_point, str) or not fastening_point.strip():
            raise ValueError("fastening_point 必须是非空字符串")
        point = unicodedata.normalize("NFC", fastening_point.strip())
        normalized_events = [
            _normalize_event(row, event_index=index)
            for index, row in enumerate(events, start=1)
        ]

        event_ids = [row["event_id"] for row in normalized_events]
        duplicate_ids = sorted(
            event_id for event_id, count in Counter(event_ids).items() if count > 1
        )
        if duplicate_ids:
            preview = ", ".join(duplicate_ids[:3])
            suffix = "……" if len(duplicate_ids) > 3 else ""
            raise ValueError(f"输入事件存在重复 event_id：{preview}{suffix}")

        point_events = sorted(
            (row for row in normalized_events if row["fastening_point"] == point),
            key=lambda row: (row["_timestamp_utc"], row["event_id"]),
        )
        if not point_events:
            raise ValueError(f"输入数据中不存在紧固点 {point!r}")

        # Select one complete product/program/tool/station population.  Equal
        # timestamps are allowed and deterministically ordered by event_id.
        latest = point_events[-1]
        strata = (
            latest["model_code"],
            latest["program_id"],
            latest["tool_id"],
            latest["station_id"],
        )
        point_events = [
            row
            for row in point_events
            if (row["model_code"], row["program_id"], row["tool_id"], row["station_id"])
            == strata
        ]
        if len(point_events) < self.baseline_count + self.recent_count:
            raise ValueError(
                f"{point} 分层 {strata} 至少需要 {self.baseline_count + self.recent_count} 条记录"
            )

        bundle = self.knowledge.retrieve(point)
        baseline = point_events[: self.baseline_count]
        recent = point_events[-self.recent_count :]
        baseline_torque = [float(row["torque_nm"]) for row in baseline]
        recent_torque = [float(row["torque_nm"]) for row in recent]
        # Only the declared baseline and recent windows may influence the
        # score.  Any records between those windows are outside the analysis
        # contract and therefore also outside its fingerprint.
        torque_all = [*baseline_torque, *recent_torque]
        baseline_angle = [float(row["angle_deg"]) for row in baseline]
        recent_angle = [float(row["angle_deg"]) for row in recent]
        baseline_current = [float(row["current_a"]) for row in baseline if row["current_a"] is not None]
        recent_current = [float(row["current_a"]) for row in recent if row["current_a"] is not None]
        baseline_cycle = [float(row["cycle_time_s"]) for row in baseline if row["cycle_time_s"] is not None]
        recent_cycle = [float(row["cycle_time_s"]) for row in recent if row["cycle_time_s"] is not None]

        current_availability = _metric_availability(
            baseline_current,
            recent_current,
            baseline_required=self.baseline_count,
            recent_required=self.recent_count,
        )
        cycle_availability = _metric_availability(
            baseline_cycle,
            recent_cycle,
            baseline_required=self.baseline_count,
            recent_required=self.recent_count,
        )
        metric_availability = {
            "current_a": current_availability,
            "cycle_time_s": cycle_availability,
        }

        center = safe_mean(baseline_torque)
        sigma = safe_std(baseline_torque)
        rule_hits = western_electric_rules(torque_all, center, sigma)
        mean_shift_sigma = abs(safe_mean(recent_torque) - center) / sigma if sigma > 0 else 0.0
        if mean_shift_sigma >= 1.2:
            rule_hits.append(
                RuleHit(
                    "MW-24",
                    f"最近 {self.recent_count} 点均值偏移超过 1.2σ",
                    "用于识别规格内的缓慢过程漂移",
                    list(range(len(torque_all) - len(recent_torque), len(torque_all))),
                )
            )
        angle_ratio = safe_std(recent_angle) / max(safe_std(baseline_angle), 0.01)
        current_shift_sigma = (
            abs(safe_mean(recent_current) - safe_mean(baseline_current))
            / max(safe_std(baseline_current), 0.01)
            if current_availability["available"]
            else None
        )
        cycle_shift_sigma = (
            abs(safe_mean(recent_cycle) - safe_mean(baseline_cycle))
            / max(safe_std(baseline_cycle), 0.01)
            if cycle_availability["available"]
            else None
        )
        retry_baseline = safe_mean(float(row["retry_count"]) for row in baseline)
        retry_recent = safe_mean(float(row["retry_count"]) for row in recent)
        retry_delta = retry_recent - retry_baseline
        calibration_days = int(recent[-1]["calibration_days_remaining"])
        alarm_counts = Counter(
            row["alarm_code"] for row in recent if row["alarm_code"]
        )
        repeated_alarm_count = sum(alarm_counts.values())

        lower = float(bundle.control_plan["torque_lsl_nm"])
        upper = float(bundle.control_plan["torque_usl_nm"])
        capability = capability_snapshot(recent_torque, lower, upper)
        baseline_in_spec_rate = sum(
            lower <= value <= upper for value in baseline_torque
        ) / len(baseline_torque)
        in_spec_rate = sum(lower <= value <= upper for value in recent_torque) / len(recent_torque)

        process_score = min(35, 10 + len(rule_hits) * 7 + round(min(mean_shift_sigma, 3.0) * 3))
        optional_equipment_score = 0.0
        if current_shift_sigma is not None:
            optional_equipment_score += max(current_shift_sigma - 1, 0) * 2
        if cycle_shift_sigma is not None:
            optional_equipment_score += max(cycle_shift_sigma - 1, 0) * 2
        equipment_score = min(
            25,
            round(
                max(angle_ratio - 1, 0) * 9
                + max(retry_delta, 0) * 45
                + optional_equipment_score
                + min(repeated_alarm_count, 4) * 2
            ),
        )
        quality_score = min(25, round(float(bundle.pfmea["severity"]) / 10 * 25))
        context_score = min(
            15,
            (8 if calibration_days <= 14 else 2)
            + (5 if bundle.historical_cases else 0),
        )
        score_breakdown = {
            "process_stability": process_score,
            "equipment_health": equipment_score,
            "quality_impact": quality_score,
            "context": context_score,
        }
        risk_score = sum(score_breakdown.values())
        risk_level = "high" if risk_score >= 75 else "medium" if risk_score >= 45 else "low"

        equipment_signal_reasons: list[str] = []
        if alarm_counts:
            equipment_signal_reasons.append(
                "equipment_alarm=" + ",".join(sorted(alarm_counts))
            )
        if angle_ratio >= 1.5 and retry_delta >= 0.05:
            equipment_signal_reasons.append("angle_dispersion_and_retry_increase")
        if (
            current_shift_sigma is not None
            and current_shift_sigma >= 1.5
            and (
                (cycle_shift_sigma is not None and cycle_shift_sigma >= 1.0)
                or retry_delta >= 0.05
            )
        ):
            equipment_signal_reasons.append("current_shift_with_secondary_signal")
        if calibration_days <= 14 and mean_shift_sigma >= 1.0:
            equipment_signal_reasons.append("calibration_due_with_torque_shift")

        trigger_reasons: list[str] = []
        if risk_level != "low":
            trigger_reasons.append(f"risk_level={risk_level}")
        trigger_reasons.extend(f"spc_rule={hit.rule_id}" for hit in rule_hits)
        trigger_reasons.extend(equipment_signal_reasons)
        attribution_required = bool(trigger_reasons)

        evidence = self._build_evidence(
            bundle,
            str(event_source).strip() or "in_memory_events",
            rule_hits,
            center,
            sigma,
            recent_torque,
            mean_shift_sigma,
            angle_ratio,
            retry_baseline,
            retry_recent,
            calibration_days,
            recent,
            capability,
            baseline_in_spec_rate,
            in_spec_rate,
            alarm_counts,
            current_shift_sigma,
            cycle_shift_sigma,
            metric_availability,
            attribution_required,
        )

        window_assessment = (
            f"本窗口规格内比例为 {in_spec_rate:.1%}，未触发 SPC 异常规则或设备组合异常信号，当前窗口稳定"
            if not attribution_required
            else f"本窗口规格内比例为 {in_spec_rate:.1%}，当前问题属于趋势风险而非批量越限"
            if in_spec_rate == 1.0
            else f"本窗口规格内比例为 {in_spec_rate:.1%}，存在趋势或规格越限风险"
        )

        observed_facts = [
            f"最近 {len(recent)} 次拧紧均值为 {fmean(recent_torque):.2f} N·m，较基线偏移 {mean_shift_sigma:.2f}σ",
            f"拧紧角度离散度为基线的 {angle_ratio:.2f} 倍",
            f"重试均值由 {retry_baseline:.3f} 次/循环升至 {retry_recent:.3f} 次/循环",
            self._optional_metric_fact(
                "电流", current_shift_sigma, current_availability
            )
            + "；"
            + self._optional_metric_fact("节拍", cycle_shift_sigma, cycle_availability),
            window_assessment,
        ]
        if alarm_counts:
            observed_facts.append(
                "当前窗口报警："
                + "、".join(f"{code}×{count}" for code, count in sorted(alarm_counts.items()))
            )
        candidate_causes = (
            self._candidate_causes(
                bundle, angle_ratio, retry_delta, calibration_days, alarm_counts
            )
            if attribution_required
            else []
        )
        actions = self._actions(bundle) if attribution_required else []

        analyzed_window = [*baseline, *recent]
        normalized_window = [
            {field: row.get(field) for field in ANALYSIS_HASH_FIELDS}
            for row in analyzed_window
        ]
        input_revision = "sha256:" + hashlib.sha256(
            _canonical_json_bytes(normalized_window)
        ).hexdigest()
        analysis_stratum = {
            "station_id": latest["station_id"],
            "tool_id": latest["tool_id"],
            "model_code": latest["model_code"],
            "program_id": latest["program_id"],
            "fastening_point": point,
        }
        identity_payload = {
            "risk_card_schema": "1.0",
            "normalized_window_schema": NORMALIZED_WINDOW_SCHEMA,
            "analysis_stratum": analysis_stratum,
            "baseline_count": self.baseline_count,
            "recent_count": self.recent_count,
            "input_window_revision": input_revision,
            "risk_policy_version": RISK_POLICY_VERSION,
            "knowledge_revision": self.knowledge.revision,
        }
        card_identity_revision = "sha256:" + hashlib.sha256(
            _canonical_json_bytes(identity_payload)
        ).hexdigest()
        card_id = "TG-" + card_identity_revision.removeprefix("sha256:")[:32].upper()

        return RiskCard(
            card_id=card_id,
            created_at=recent[-1]["timestamp"],
            station_id=latest["station_id"],
            tool_id=latest["tool_id"],
            fastening_point=point,
            risk_level=risk_level,
            risk_score=risk_score,
            status=(
                "awaiting_engineer_review" if attribution_required else "monitoring_only"
            ),
            observed_facts=observed_facts,
            inference=(
                "设备与过程信号出现同向变化，且与 PFMEA 中的预紧力不稳定失效链相连。"
                "系统将其列为需要优先验证的设备质量风险，但不直接确认根因。"
                if attribution_required
                else "当前窗口稳定且未触发异常信号，无需启动根因归因或异常处置。"
            ),
            uncertainty=(
                "当前结论来自合成数据与规则验证；套筒状态、工具标定和零件批次需要现场检查后才能排除。"
                if attribution_required
                else "稳定判定仅适用于当前分析分层与时间窗口；后续继续按常规频率监控。"
            ),
            affected_scope={
                "model_code": [latest["model_code"]],
                "program_id": [latest["program_id"]],
                "batch_ids": sorted({row["batch_id"] for row in recent}),
                "window_start": recent[0]["timestamp"],
                "window_end": recent[-1]["timestamp"],
                "event_count": len(recent),
            },
            score_breakdown=score_breakdown,
            evidence=evidence,
            candidate_causes=candidate_causes,
            recommended_actions=actions,
            analysis_provenance={
                "generated_by": "torque_guard.risk.RiskAnalyzer",
                "risk_policy_version": RISK_POLICY_VERSION,
                "knowledge_schema": str(self.knowledge.ontology.get("schema", "unknown")),
                "knowledge_revision": self.knowledge.revision,
                "input_window_revision": input_revision,
                "card_identity_revision": card_identity_revision,
                "normalized_window_schema": NORMALIZED_WINDOW_SCHEMA,
                "baseline_count": self.baseline_count,
                "recent_count": self.recent_count,
                "analysis_stratum": analysis_stratum,
                "timestamp_policy": {
                    "canonical_timezone": "UTC",
                    "naive_input_timezone": "UTC+08:00",
                    "event_ordering": "utc_timestamp_then_event_id",
                    "duplicate_timestamp_policy": "allowed_and_ordered_by_event_id",
                },
                "metric_availability": metric_availability,
                "attribution_required": attribution_required,
                "analysis_disposition": (
                    "investigation_required"
                    if attribution_required
                    else "stable_monitoring"
                ),
                "trigger_reasons": trigger_reasons,
            },
        )

    @staticmethod
    def _optional_metric_fact(
        label: str,
        shift_sigma: float | None,
        availability: MetricAvailability,
    ) -> str:
        if shift_sigma is not None:
            return f"{label}相对基线偏移 {shift_sigma:.2f}σ"
        return (
            f"{label}偏移不可用（基线 {availability['baseline_sample_count']}/"
            f"{availability['baseline_required_count']}，当前 "
            f"{availability['recent_sample_count']}/{availability['recent_required_count']}）"
        )

    def _build_evidence(
        self,
        bundle: KnowledgeBundle,
        event_source: str,
        rule_hits: list[Any],
        center: float,
        sigma: float,
        recent_torque: list[float],
        mean_shift_sigma: float,
        angle_ratio: float,
        retry_baseline: float,
        retry_recent: float,
        calibration_days: int,
        recent: list[dict[str, Any]],
        capability: dict[str, float],
        baseline_in_spec_rate: float,
        in_spec_rate: float,
        alarm_counts: Counter[str],
        current_shift_sigma: float | None,
        cycle_shift_sigma: float | None,
        metric_availability: dict[str, MetricAvailability],
        attribution_required: bool,
    ) -> list[Evidence]:
        rule_names = "；".join(f"{hit.rule_id} {hit.name}" for hit in rule_hits) or "未触发趋势规则"
        evidence = [
            Evidence(
                "E-SPC-01",
                "spc",
                "扭矩过程中心偏移" if attribution_required else "扭矩过程稳定性检查",
                f"均值偏移 {mean_shift_sigma:.2f}σ；{rule_names}",
                event_source,
                f"{recent[0]['event_id']}..{recent[-1]['event_id']}",
                "direct",
                {
                    "baseline_mean_nm": round(center, 3),
                    "baseline_sigma_nm": round(sigma, 3),
                    "recent_mean_nm": round(safe_mean(recent_torque), 3),
                    "mean_shift_sigma": round(mean_shift_sigma, 3),
                    "recent_cpk": round(capability["cpk"], 3),
                    "baseline_in_spec_rate": round(baseline_in_spec_rate, 4),
                    "in_spec_rate": round(in_spec_rate, 4),
                    "rule_ids": [hit.rule_id for hit in rule_hits],
                },
            ),
            Evidence(
                "E-EQP-02",
                "equipment",
                "角度离散与重试同步变化" if attribution_required else "设备辅助信号检查",
                f"角度标准差比值 {angle_ratio:.2f}；重试 {retry_baseline:.3f} → {retry_recent:.3f} 次/循环",
                event_source,
                f"tool={recent[-1]['tool_id']}, point={recent[-1]['fastening_point']}",
                "direct",
                {
                    "angle_std_ratio": round(angle_ratio, 3),
                    "retry_baseline": round(retry_baseline, 3),
                    "retry_recent": round(retry_recent, 3),
                    "current_shift_sigma": (
                        round(current_shift_sigma, 3) if current_shift_sigma is not None else None
                    ),
                    "cycle_time_shift_sigma": (
                        round(cycle_shift_sigma, 3) if cycle_shift_sigma is not None else None
                    ),
                    "metric_availability": metric_availability,
                },
            ),
            Evidence(
                "E-KNW-03",
                "pfmea",
                str(bundle.pfmea["failure_mode"]),
                f"严重度 S={bundle.pfmea['severity']}；影响：{bundle.pfmea['effect']}",
                "pfmea_demo.csv",
                str(bundle.pfmea["failure_mode_id"]),
                "document",
                {"cause_ids": str(bundle.pfmea["cause_ids"]).split(";")},
            ),
            Evidence(
                "E-CTL-04",
                "control_plan",
                "控制计划要求",
                str(bundle.control_plan["reaction_plan"]),
                "control_plan_demo.csv",
                str(bundle.control_plan["control_plan_id"]),
                "document",
                {"calibration_days_remaining": calibration_days},
            ),
            Evidence(
                "E-HIS-05",
                "history",
                "相似历史案例",
                str(bundle.historical_cases[0]["summary"])
                if bundle.historical_cases
                else "无可用历史案例",
                "historical_cases.json",
                str(bundle.historical_cases[0]["case_id"])
                if bundle.historical_cases
                else "none",
                "analogy",
                {"similarity_basis": "shared_cause_id"},
            ),
        ]
        if alarm_counts:
            alarm_details = []
            for code, count in sorted(alarm_counts.items()):
                definition = bundle.alarm_dictionary.get(code, {})
                alarm_details.append(
                    {
                        "alarm_code": code,
                        "count": count,
                        "meaning": definition.get("meaning", "未知报警，需人工核对"),
                        "recommended_check": definition.get(
                            "recommended_check", "核对控制器记录"
                        ),
                    }
                )
            evidence.append(
                Evidence(
                    "E-ALM-06",
                    "alarm",
                    "当前窗口重复报警",
                    "；".join(
                        f"{item['alarm_code']}×{item['count']} {item['meaning']}"
                        for item in alarm_details
                    ),
                    "alarm_dictionary_demo.csv",
                    f"{recent[0]['event_id']}..{recent[-1]['event_id']}",
                    "direct",
                    {"alarms": alarm_details},
                )
            )
        return evidence

    @staticmethod
    def _candidate_causes(
        bundle: KnowledgeBundle,
        angle_ratio: float,
        retry_delta: float,
        calibration_days: int,
        alarm_counts: Counter[str],
    ) -> list[CandidateCause]:
        causes = [
            CandidateCause(
                "套筒或批头磨损",
                "high"
                if (angle_ratio >= 1.8 and retry_delta > 0.08) or "ALM-507" in alarm_counts
                else "medium",
                ["角度离散增大", "重试率/重复报警上升", "PFMEA C-SOCKET-WEAR"],
                "检查套筒磨损量和同心度，并以合格备件完成 10 次对比拧紧。",
            ),
            CandidateCause(
                "工具标定漂移",
                "high"
                if "ALM-314" in alarm_counts
                else "medium-high"
                if calibration_days <= 14
                else "medium",
                [
                    f"距标定到期 {calibration_days} 天",
                    "扭矩均值持续同向偏移",
                    "ALM-314 传感器零点偏移"
                    if "ALM-314" in alarm_counts
                    else "PFMEA C-CAL-DRIFT",
                ],
                "使用标准扭矩测试仪完成 5 点重复性检查，不在系统内自动修改工具参数。",
            ),
            CandidateCause(
                "连接件批次摩擦特性变化",
                "medium",
                ["角度与扭矩关系变化", "当前窗口涉及单一物料批次"],
                "对当前批次抽样复核表面状态、涂层和螺纹，并与上一合格批次做对照。",
            ),
        ]
        confidence_rank = {"high": 3, "medium-high": 2, "medium": 1, "low": 0}
        return sorted(
            causes,
            key=lambda item: confidence_rank.get(item.confidence, 0),
            reverse=True,
        )

    @staticmethod
    def _actions(bundle: KnowledgeBundle) -> list[Action]:
        return [
            Action(
                action_id="A-01",
                title="复核工具标定与套筒状态",
                owner_role="设备工程师",
                due_minutes=30,
                approval_required=True,
                acceptance_criteria="上传标定结果、套筒照片和对比拧紧记录",
                why=(
                    "角度离散、重试和扭矩中心出现同向变化，需用现场点检区分"
                    "套筒磨损与工具标定漂移；本任务只用于验证候选原因。"
                ),
                evidence_ids=["E-EQP-02", "E-SPC-01", "E-CTL-04"],
                candidate_causes=["套筒或批头磨损", "工具标定漂移"],
            ),
            Action(
                action_id="A-02",
                title="对风险窗口车辆执行紧固点抽检",
                owner_role="质量工程师",
                due_minutes=45,
                approval_required=True,
                acceptance_criteria="记录抽检 VIN 脱敏编号、扭矩复核结果和判定",
                why=(
                    "当前窗口可能仍处于规格内，但过程中心变化关联关键连接质量影响，"
                    "需通过抽检确认影响范围，不能仅凭风险分数作出处置。"
                ),
                evidence_ids=["E-SPC-01", "E-KNW-03", "E-CTL-04"],
                candidate_causes=[
                    "套筒或批头磨损",
                    "工具标定漂移",
                    "连接件批次摩擦特性变化",
                ],
            ),
            Action(
                action_id="A-03",
                title="追溯物料批次并与上一合格批次对比",
                owner_role="工艺质量工程师",
                due_minutes=60,
                approval_required=True,
                acceptance_criteria="完成批次差异表并排除或保留批次假设",
                why=(
                    "风险窗口涉及待验证的批次摩擦特性假设，需以当前批次和上一合格"
                    "批次的实物与记录对比决定保留或排除该假设。"
                ),
                evidence_ids=["E-SPC-01", "E-KNW-03", "E-HIS-05"],
                candidate_causes=["连接件批次摩擦特性变化"],
            ),
            Action(
                action_id="A-04",
                title="工程师评审后决定是否扩大隔离范围",
                owner_role="生产质量负责人",
                due_minutes=90,
                approval_required=True,
                acceptance_criteria=str(bundle.control_plan["reaction_plan"]),
                why=(
                    "多源证据只支持待验证假设，必须由具名工程师综合点检、抽检和"
                    "批次追溯结果后决定处置范围，系统不得自动停线或确认根因。"
                ),
                evidence_ids=["E-SPC-01", "E-EQP-02", "E-KNW-03", "E-CTL-04"],
                candidate_causes=[
                    "套筒或批头磨损",
                    "工具标定漂移",
                    "连接件批次摩擦特性变化",
                ],
            ),
        ]
