from __future__ import annotations

import csv
import hashlib
from pathlib import Path
from statistics import fmean
from typing import Any

from .knowledge import KnowledgeBundle, KnowledgeBase
from .models import Action, CandidateCause, Evidence, RiskCard
from .spc import RuleHit, capability_snapshot, safe_mean, safe_std, western_electric_rules


NUMERIC_FIELDS = {
    "torque_nm",
    "angle_deg",
    "current_a",
    "cycle_time_s",
    "retry_count",
    "calibration_days_remaining",
}


def read_events(path: str | Path) -> list[dict[str, Any]]:
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        for key in NUMERIC_FIELDS:
            row[key] = float(row[key]) if row.get(key, "") != "" else None
    return rows


class RiskAnalyzer:
    def __init__(self, knowledge_root: str | Path, baseline_count: int = 100, recent_count: int = 24):
        self.knowledge = KnowledgeBase(knowledge_root)
        self.baseline_count = baseline_count
        self.recent_count = recent_count

    def analyze(self, events: list[dict[str, Any]], fastening_point: str) -> RiskCard:
        point_events = [row for row in events if row["fastening_point"] == fastening_point]
        if len(point_events) < self.baseline_count + self.recent_count:
            raise ValueError(f"{fastening_point} 至少需要 {self.baseline_count + self.recent_count} 条记录")

        bundle = self.knowledge.retrieve(fastening_point)
        baseline = point_events[: self.baseline_count]
        recent = point_events[-self.recent_count :]
        torque_all = [float(row["torque_nm"]) for row in point_events if row["torque_nm"] is not None]
        baseline_torque = [float(row["torque_nm"]) for row in baseline if row["torque_nm"] is not None]
        recent_torque = [float(row["torque_nm"]) for row in recent if row["torque_nm"] is not None]
        baseline_angle = [float(row["angle_deg"]) for row in baseline if row["angle_deg"] is not None]
        recent_angle = [float(row["angle_deg"]) for row in recent if row["angle_deg"] is not None]

        center = safe_mean(baseline_torque)
        sigma = safe_std(baseline_torque)
        rule_hits = western_electric_rules(torque_all, center, sigma)
        mean_shift_sigma = abs(safe_mean(recent_torque) - center) / sigma if sigma > 0 else 0.0
        if mean_shift_sigma >= 1.2:
            rule_hits.append(
                RuleHit(
                    "MW-24",
                    "最近 24 点均值偏移超过 1.2σ",
                    "用于识别规格内的缓慢过程漂移",
                    list(range(len(torque_all) - len(recent_torque), len(torque_all))),
                )
            )
        angle_ratio = safe_std(recent_angle) / max(safe_std(baseline_angle), 0.01)
        retry_baseline = safe_mean(float(row["retry_count"] or 0) for row in baseline)
        retry_recent = safe_mean(float(row["retry_count"] or 0) for row in recent)
        retry_delta = retry_recent - retry_baseline
        calibration_days = int(recent[-1]["calibration_days_remaining"] or 0)

        lower = float(bundle.control_plan["torque_lsl_nm"])
        upper = float(bundle.control_plan["torque_usl_nm"])
        capability = capability_snapshot(recent_torque, lower, upper)
        in_spec_rate = sum(lower <= value <= upper for value in recent_torque) / len(recent_torque)

        evidence = self._build_evidence(
            bundle,
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
            in_spec_rate,
        )

        process_score = min(35, 10 + len(rule_hits) * 7 + round(min(mean_shift_sigma, 3.0) * 3))
        equipment_score = min(25, round(max(angle_ratio - 1, 0) * 9 + max(retry_delta, 0) * 45))
        quality_score = min(25, round(float(bundle.pfmea["severity"]) / 10 * 25))
        context_score = min(15, (8 if calibration_days <= 14 else 2) + (5 if bundle.historical_cases else 0))
        score_breakdown = {
            "process_stability": process_score,
            "equipment_health": equipment_score,
            "quality_impact": quality_score,
            "context": context_score,
        }
        risk_score = min(100, sum(score_breakdown.values()))
        risk_level = "high" if risk_score >= 75 else "medium" if risk_score >= 45 else "low"

        digest = hashlib.sha1(f"{fastening_point}:{recent[-1]['timestamp']}".encode()).hexdigest()[:8].upper()
        observed_facts = [
            f"最近 {len(recent)} 次拧紧均值为 {fmean(recent_torque):.2f} N·m，较基线偏移 {mean_shift_sigma:.2f}σ",
            f"拧紧角度离散度为基线的 {angle_ratio:.2f} 倍",
            f"重试均值由 {retry_baseline:.3f} 次/循环升至 {retry_recent:.3f} 次/循环",
            f"本窗口规格内比例为 {in_spec_rate:.1%}，当前问题属于趋势风险而非批量越限",
        ]
        candidate_causes = self._candidate_causes(bundle, angle_ratio, retry_delta, calibration_days)
        actions = self._actions(bundle)

        return RiskCard(
            card_id=f"TG-{digest}",
            created_at=f"{recent[-1]['timestamp']}+08:00",
            station_id=str(recent[-1]["station_id"]),
            tool_id=str(recent[-1]["tool_id"]),
            fastening_point=fastening_point,
            risk_level=risk_level,
            risk_score=risk_score,
            status="awaiting_engineer_review",
            observed_facts=observed_facts,
            inference=(
                "设备与过程信号出现同向变化，且与 PFMEA 中的预紧力不稳定失效链相连。"
                "系统将其列为需要优先验证的设备质量风险，但不直接确认根因。"
            ),
            uncertainty="当前结论来自合成数据与规则验证；套筒状态、工具标定和零件批次需要现场检查后才能排除。",
            affected_scope={
                "model_code": sorted({row["model_code"] for row in recent}),
                "program_id": sorted({row["program_id"] for row in recent}),
                "batch_ids": sorted({row["batch_id"] for row in recent}),
                "window_start": recent[0]["timestamp"],
                "window_end": recent[-1]["timestamp"],
                "event_count": len(recent),
            },
            score_breakdown=score_breakdown,
            evidence=evidence,
            candidate_causes=candidate_causes,
            recommended_actions=actions,
        )

    def _build_evidence(
        self,
        bundle: KnowledgeBundle,
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
        in_spec_rate: float,
    ) -> list[Evidence]:
        rule_names = "；".join(f"{hit.rule_id} {hit.name}" for hit in rule_hits) or "未触发趋势规则"
        return [
            Evidence(
                "E-SPC-01",
                "spc",
                "扭矩过程中心偏移",
                f"均值偏移 {mean_shift_sigma:.2f}σ；{rule_names}",
                "tightening_events_demo.csv",
                f"{recent[0]['event_id']}..{recent[-1]['event_id']}",
                "direct",
                {
                    "baseline_mean_nm": round(center, 3),
                    "baseline_sigma_nm": round(sigma, 3),
                    "recent_mean_nm": round(safe_mean(recent_torque), 3),
                    "recent_cpk": round(capability["cpk"], 3),
                    "in_spec_rate": round(in_spec_rate, 4),
                    "rule_ids": [hit.rule_id for hit in rule_hits],
                },
            ),
            Evidence(
                "E-EQP-02",
                "equipment",
                "角度离散与重试同步上升",
                f"角度标准差比值 {angle_ratio:.2f}；重试 {retry_baseline:.3f} → {retry_recent:.3f} 次/循环",
                "tightening_events_demo.csv",
                f"tool={recent[-1]['tool_id']}, point={recent[-1]['fastening_point']}",
                "direct",
                {"angle_std_ratio": round(angle_ratio, 3), "retry_recent": round(retry_recent, 3)},
            ),
            Evidence(
                "E-KNW-03",
                "pfmea",
                bundle.pfmea["failure_mode"],
                f"严重度 S={bundle.pfmea['severity']}；影响：{bundle.pfmea['effect']}",
                "pfmea_demo.csv",
                bundle.pfmea["failure_mode_id"],
                "document",
                {"cause_ids": bundle.pfmea["cause_ids"].split(";")},
            ),
            Evidence(
                "E-CTL-04",
                "control_plan",
                "控制计划要求",
                bundle.control_plan["reaction_plan"],
                "control_plan_demo.csv",
                bundle.control_plan["control_plan_id"],
                "document",
                {"calibration_days_remaining": calibration_days},
            ),
            Evidence(
                "E-HIS-05",
                "history",
                "相似历史案例",
                bundle.historical_cases[0]["summary"] if bundle.historical_cases else "无可用历史案例",
                "historical_cases.json",
                bundle.historical_cases[0]["case_id"] if bundle.historical_cases else "none",
                "analogy",
                {"similarity_basis": "shared_cause_id"},
            ),
        ]

    @staticmethod
    def _candidate_causes(
        bundle: KnowledgeBundle, angle_ratio: float, retry_delta: float, calibration_days: int
    ) -> list[CandidateCause]:
        causes = [
            CandidateCause(
                "套筒或批头磨损",
                "high" if angle_ratio >= 1.8 and retry_delta > 0.08 else "medium",
                ["角度离散增大", "重试率上升", "PFMEA C-SOCKET-WEAR"],
                "检查套筒磨损量和同心度，并以合格备件完成 10 次对比拧紧。",
            ),
            CandidateCause(
                "工具标定漂移",
                "medium-high" if calibration_days <= 14 else "medium",
                [f"距标定到期 {calibration_days} 天", "扭矩均值持续同向偏移", "PFMEA C-CAL-DRIFT"],
                "使用标准扭矩测试仪完成 5 点重复性检查，不在系统内自动修改工具参数。",
            ),
            CandidateCause(
                "连接件批次摩擦特性变化",
                "medium",
                ["角度与扭矩关系变化", "当前窗口涉及单一物料批次"],
                "对当前批次抽样复核表面状态、涂层和螺纹，并与上一合格批次做对照。",
            ),
        ]
        return causes

    @staticmethod
    def _actions(bundle: KnowledgeBundle) -> list[Action]:
        return [
            Action("A-01", "复核工具标定与套筒状态", "设备工程师", 30, True, "上传标定结果、套筒照片和对比拧紧记录"),
            Action("A-02", "对风险窗口车辆执行紧固点抽检", "质量工程师", 45, True, "记录抽检 VIN 脱敏编号、扭矩复核结果和判定"),
            Action("A-03", "追溯物料批次并与上一合格批次对比", "工艺质量工程师", 60, True, "完成批次差异表并排除或保留批次假设"),
            Action("A-04", "工程师评审后决定是否扩大隔离范围", "生产质量负责人", 90, True, bundle.control_plan["reaction_plan"]),
        ]
