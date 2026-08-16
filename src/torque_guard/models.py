from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, TypedDict


SHA256_REVISION = re.compile(r"^sha256:[0-9a-f]{64}$")
CARD_ID = re.compile(r"^TG-[0-9A-F]{32}$")
UTC_RFC3339 = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$"
)


class AnalysisStratum(TypedDict):
    station_id: str
    tool_id: str
    model_code: str
    program_id: str
    fastening_point: str


class MetricAvailability(TypedDict):
    available: bool
    baseline_sample_count: int
    baseline_required_count: int
    recent_sample_count: int
    recent_required_count: int
    reason: str | None


class TimestampPolicy(TypedDict):
    canonical_timezone: str
    naive_input_timezone: str
    event_ordering: str
    duplicate_timestamp_policy: str


class AnalysisProvenance(TypedDict):
    generated_by: str
    risk_policy_version: str
    knowledge_schema: str
    knowledge_revision: str
    input_window_revision: str
    card_identity_revision: str
    normalized_window_schema: str
    baseline_count: int
    recent_count: int
    analysis_stratum: AnalysisStratum
    timestamp_policy: TimestampPolicy
    metric_availability: dict[str, MetricAvailability]
    attribution_required: bool
    analysis_disposition: str
    trigger_reasons: list[str]


@dataclass(frozen=True)
class Evidence:
    evidence_id: str
    category: str
    title: str
    observation: str
    source: str
    locator: str
    strength: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CandidateCause:
    cause: str
    confidence: str
    basis: list[str]
    verification: str
    evidence_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Action:
    action_id: str
    title: str
    owner_role: str
    due_minutes: int
    approval_required: bool
    acceptance_criteria: str
    why: str
    evidence_ids: list[str]
    candidate_causes: list[str]


@dataclass
class RiskCard:
    card_id: str
    created_at: str
    station_id: str
    tool_id: str
    fastening_point: str
    risk_level: str
    risk_score: int
    status: str
    observed_facts: list[str]
    inference: str
    uncertainty: str
    affected_scope: dict[str, Any]
    score_breakdown: dict[str, int]
    evidence: list[Evidence]
    candidate_causes: list[CandidateCause]
    recommended_actions: list[Action]
    analysis_provenance: AnalysisProvenance
    schema_version: str = "1.0"
    agent_trace: list[dict[str, Any]] = field(default_factory=list)
    reasoning: dict[str, Any] = field(default_factory=dict)
    workflow: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """Fail closed when the card's score, citations or provenance disagree.

        The object remains mutable because reasoning and the workflow are attached
        after the deterministic score is created.  Validation therefore runs at
        construction time and again whenever the card is serialized.
        """

        if self.schema_version != "1.0":
            raise ValueError("risk card schema_version 必须为 1.0")
        if not CARD_ID.fullmatch(self.card_id):
            raise ValueError("card_id 必须是 TG- 加 32 位大写十六进制摘要")
        self._require_trimmed("station_id", self.station_id)
        self._require_trimmed("tool_id", self.tool_id)
        self._require_trimmed("fastening_point", self.fastening_point)
        self._validate_created_at()
        self._validate_score()
        evidence_ids = self._validate_evidence()
        self._validate_provenance()
        candidate_causes = self._validate_candidate_citations(evidence_ids)
        self._validate_actions(evidence_ids, candidate_causes)
        self._validate_reasoning(evidence_ids)
        self._validate_workflow(evidence_ids)

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)

    @staticmethod
    def _require_trimmed(label: str, value: Any) -> None:
        if not isinstance(value, str) or not value or value != value.strip():
            raise ValueError(f"{label} 必须是非空且已去除首尾空白的字符串")

    def _validate_created_at(self) -> None:
        if not isinstance(self.created_at, str) or not UTC_RFC3339.fullmatch(self.created_at):
            raise ValueError("created_at 必须是 UTC RFC3339 时间")
        try:
            parsed = datetime.fromisoformat(self.created_at[:-1] + "+00:00")
        except ValueError as exc:
            raise ValueError("created_at 必须是 UTC RFC3339 时间") from exc
        if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
            raise ValueError("created_at 必须是 UTC RFC3339 时间")

    def _validate_score(self) -> None:
        expected_keys = {
            "process_stability",
            "equipment_health",
            "quality_impact",
            "context",
        }
        if set(self.score_breakdown) != expected_keys:
            raise ValueError("score_breakdown 的评分维度不完整")
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in self.score_breakdown.values()
        ):
            raise ValueError("score_breakdown 必须由非负整数构成")
        if isinstance(self.risk_score, bool) or not isinstance(self.risk_score, int):
            raise ValueError("risk_score 必须是整数")
        if not 0 <= self.risk_score <= 100:
            raise ValueError("risk_score 必须位于 0..100")
        if self.risk_score != sum(self.score_breakdown.values()):
            raise ValueError("risk_score 必须等于 score_breakdown 各项之和")
        expected_level = (
            "high" if self.risk_score >= 75 else "medium" if self.risk_score >= 45 else "low"
        )
        if self.risk_level != expected_level:
            raise ValueError("risk_level 与 risk_score 阈值不一致")

    def _validate_evidence(self) -> set[str]:
        identifiers: list[str] = []
        for item in self.evidence:
            self._require_trimmed("evidence_id", item.evidence_id)
            self._require_trimmed("evidence.category", item.category)
            self._require_trimmed("evidence.title", item.title)
            self._require_trimmed("evidence.source", item.source)
            self._require_trimmed("evidence.locator", item.locator)
            self._require_trimmed("evidence.strength", item.strength)
            identifiers.append(item.evidence_id)
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("evidence_id 必须唯一")
        return set(identifiers)

    def _validate_actions(
        self,
        evidence_ids: set[str],
        candidate_causes: set[str],
    ) -> None:
        identifiers: list[str] = []
        for item in self.recommended_actions:
            self._require_trimmed("action_id", item.action_id)
            self._require_trimmed("action.title", item.title)
            self._require_trimmed("action.owner_role", item.owner_role)
            self._require_trimmed("action.acceptance_criteria", item.acceptance_criteria)
            self._require_trimmed("action.why", item.why)
            if isinstance(item.due_minutes, bool) or not isinstance(item.due_minutes, int):
                raise ValueError("action.due_minutes 必须是整数")
            if item.due_minutes < 1:
                raise ValueError("action.due_minutes 必须是正整数")
            if not isinstance(item.approval_required, bool):
                raise ValueError("action.approval_required 必须是布尔值")
            if item.approval_required is not True:
                raise ValueError("所有处置任务必须保留人工审批")

            action_evidence = self._validate_reference_list(
                "action.evidence_ids", item.evidence_ids
            )
            unknown_evidence = set(action_evidence) - evidence_ids
            if unknown_evidence:
                raise ValueError(
                    "action 引用了未知 evidence_id："
                    + ", ".join(sorted(unknown_evidence))
                )

            action_causes = self._validate_reference_list(
                "action.candidate_causes", item.candidate_causes
            )
            unknown_causes = set(action_causes) - candidate_causes
            if unknown_causes:
                raise ValueError(
                    "action 引用了未知候选原因：" + ", ".join(sorted(unknown_causes))
                )
            identifiers.append(item.action_id)
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("action_id 必须唯一")

    @classmethod
    def _validate_reference_list(cls, label: str, values: Any) -> list[str]:
        if not isinstance(values, list) or not values:
            raise ValueError(f"{label} 必须是至少包含一项的字符串列表")
        for value in values:
            cls._require_trimmed(label, value)
        if len(values) != len(set(values)):
            raise ValueError(f"{label} 不得重复")
        return values

    def _validate_candidate_citations(self, evidence_ids: set[str]) -> set[str]:
        candidate_names: list[str] = []
        for cause in self.candidate_causes:
            self._require_trimmed("candidate cause.cause", cause.cause)
            unknown = set(cause.evidence_ids) - evidence_ids
            if unknown:
                raise ValueError(
                    "candidate cause 引用了未知 evidence_id：" + ", ".join(sorted(unknown))
                )
            if len(cause.evidence_ids) != len(set(cause.evidence_ids)):
                raise ValueError("candidate cause 的 evidence_id 不得重复")
            candidate_names.append(cause.cause)
        if len(candidate_names) != len(set(candidate_names)):
            raise ValueError("candidate cause 名称必须唯一")
        return set(candidate_names)

    def _validate_provenance(self) -> None:
        provenance = self.analysis_provenance
        required = {
            "generated_by",
            "risk_policy_version",
            "knowledge_schema",
            "knowledge_revision",
            "input_window_revision",
            "card_identity_revision",
            "normalized_window_schema",
            "baseline_count",
            "recent_count",
            "analysis_stratum",
            "timestamp_policy",
            "metric_availability",
            "attribution_required",
            "analysis_disposition",
            "trigger_reasons",
        }
        missing = required - set(provenance)
        if missing:
            raise ValueError("analysis_provenance 缺少字段：" + ", ".join(sorted(missing)))
        for key in ("knowledge_revision", "input_window_revision", "card_identity_revision"):
            value = provenance[key]
            if not isinstance(value, str) or not SHA256_REVISION.fullmatch(value):
                raise ValueError(f"analysis_provenance.{key} 不是有效 SHA-256 revision")
        for key in ("generated_by", "risk_policy_version", "knowledge_schema", "normalized_window_schema"):
            self._require_trimmed(f"analysis_provenance.{key}", provenance[key])
        for key in ("baseline_count", "recent_count"):
            value = provenance[key]
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"analysis_provenance.{key} 必须是正整数")

        stratum = provenance["analysis_stratum"]
        expected_stratum_keys = {
            "station_id",
            "tool_id",
            "model_code",
            "program_id",
            "fastening_point",
        }
        if not isinstance(stratum, dict) or set(stratum) != expected_stratum_keys:
            raise ValueError("analysis_provenance.analysis_stratum 不完整")
        for key, value in stratum.items():
            self._require_trimmed(f"analysis_stratum.{key}", value)
        if (
            stratum["station_id"] != self.station_id
            or stratum["tool_id"] != self.tool_id
            or stratum["fastening_point"] != self.fastening_point
        ):
            raise ValueError("analysis_stratum 与风险卡主标识不一致")

        expected_models = [stratum["model_code"]]
        expected_programs = [stratum["program_id"]]
        if self.affected_scope.get("model_code") != expected_models:
            raise ValueError("affected_scope.model_code 与 analysis_stratum 不一致")
        if self.affected_scope.get("program_id") != expected_programs:
            raise ValueError("affected_scope.program_id 与 analysis_stratum 不一致")
        if self.affected_scope.get("event_count") != provenance["recent_count"]:
            raise ValueError("affected_scope.event_count 与 recent_count 不一致")
        if self.affected_scope.get("window_end") != self.created_at:
            raise ValueError("affected_scope.window_end 必须等于 created_at")
        for key in ("window_start", "window_end"):
            value = self.affected_scope.get(key)
            if not isinstance(value, str) or not UTC_RFC3339.fullmatch(value):
                raise ValueError(f"affected_scope.{key} 必须是 UTC RFC3339 时间")

        timestamp_policy = provenance["timestamp_policy"]
        expected_timestamp_keys = {
            "canonical_timezone",
            "naive_input_timezone",
            "event_ordering",
            "duplicate_timestamp_policy",
        }
        if not isinstance(timestamp_policy, dict) or set(timestamp_policy) != expected_timestamp_keys:
            raise ValueError("analysis_provenance.timestamp_policy 不完整")
        if timestamp_policy["canonical_timezone"] != "UTC":
            raise ValueError("timestamp_policy.canonical_timezone 必须为 UTC")
        for key, value in timestamp_policy.items():
            self._require_trimmed(f"timestamp_policy.{key}", value)

        availability = provenance["metric_availability"]
        if not isinstance(availability, dict) or set(availability) != {"current_a", "cycle_time_s"}:
            raise ValueError("metric_availability 必须记录 current_a 与 cycle_time_s")
        for metric, record in availability.items():
            self._validate_metric_availability(metric, record, provenance)

        attribution_required = provenance["attribution_required"]
        disposition = provenance["analysis_disposition"]
        trigger_reasons = provenance["trigger_reasons"]
        if not isinstance(attribution_required, bool):
            raise ValueError("analysis_provenance.attribution_required 必须是布尔值")
        if not isinstance(trigger_reasons, list) or any(
            not isinstance(item, str) or not item.strip() for item in trigger_reasons
        ):
            raise ValueError("analysis_provenance.trigger_reasons 必须是非空字符串列表")
        if len(trigger_reasons) != len(set(trigger_reasons)):
            raise ValueError("analysis_provenance.trigger_reasons 不得重复")
        if attribution_required:
            if disposition != "investigation_required" or not trigger_reasons:
                raise ValueError("需要归因时必须记录 investigation_required 及触发原因")
            if self.status == "monitoring_only":
                raise ValueError("需要归因的风险卡不得处于 monitoring_only")
        else:
            if disposition != "stable_monitoring" or trigger_reasons:
                raise ValueError("稳定窗口必须使用 stable_monitoring 且不得记录触发原因")
            if self.risk_level != "low":
                raise ValueError("仅低风险卡可以判定为无需归因")
            if self.candidate_causes or self.recommended_actions:
                raise ValueError("无需归因的风险卡不得生成候选原因或处置任务")
            if self.status != "monitoring_only":
                raise ValueError("无需归因的风险卡必须处于 monitoring_only")

        identity_payload = {
            "risk_card_schema": self.schema_version,
            "normalized_window_schema": provenance["normalized_window_schema"],
            "analysis_stratum": stratum,
            "baseline_count": provenance["baseline_count"],
            "recent_count": provenance["recent_count"],
            "input_window_revision": provenance["input_window_revision"],
            "risk_policy_version": provenance["risk_policy_version"],
            "knowledge_revision": provenance["knowledge_revision"],
        }
        expected_identity_revision = "sha256:" + hashlib.sha256(
            json.dumps(
                identity_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
        if provenance["card_identity_revision"] != expected_identity_revision:
            raise ValueError("card_identity_revision 与分析分层、窗口或策略版本不一致")
        expected_card_id = "TG-" + expected_identity_revision.removeprefix("sha256:")[:32].upper()
        if self.card_id != expected_card_id:
            raise ValueError("card_id 与 card_identity_revision 不一致")

    @staticmethod
    def _validate_metric_availability(
        metric: str,
        record: Any,
        provenance: AnalysisProvenance,
    ) -> None:
        expected = {
            "available",
            "baseline_sample_count",
            "baseline_required_count",
            "recent_sample_count",
            "recent_required_count",
            "reason",
        }
        if not isinstance(record, dict) or set(record) != expected:
            raise ValueError(f"metric_availability.{metric} 结构不完整")
        if not isinstance(record["available"], bool):
            raise ValueError(f"metric_availability.{metric}.available 必须是布尔值")
        for key in (
            "baseline_sample_count",
            "baseline_required_count",
            "recent_sample_count",
            "recent_required_count",
        ):
            value = record[key]
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"metric_availability.{metric}.{key} 必须是非负整数")
        if record["baseline_required_count"] != provenance["baseline_count"]:
            raise ValueError(f"metric_availability.{metric} 的基线需求数量不一致")
        if record["recent_required_count"] != provenance["recent_count"]:
            raise ValueError(f"metric_availability.{metric} 的当前窗口需求数量不一致")
        complete = (
            record["baseline_sample_count"] == record["baseline_required_count"]
            and record["recent_sample_count"] == record["recent_required_count"]
        )
        if record["available"] != complete:
            raise ValueError(f"metric_availability.{metric}.available 与样本数量不一致")
        if complete and record["reason"] is not None:
            raise ValueError(f"metric_availability.{metric} 可用时 reason 必须为 null")
        if not complete and not isinstance(record["reason"], str):
            raise ValueError(f"metric_availability.{metric} 不可用时必须给出 reason")

    def _validate_reasoning(self, evidence_ids: set[str]) -> None:
        if not self.reasoning:
            return
        decision = self.reasoning.get("decision")
        if decision not in {"supported", "refused"}:
            raise ValueError("reasoning.decision 非法")
        cited: list[str] = []
        conclusion = self.reasoning.get("conclusion")
        attribution_required = self.analysis_provenance["attribution_required"]
        if not attribution_required:
            if (
                decision != "refused"
                or self.reasoning.get("disposition") != "no_attribution_required"
                or conclusion is not None
                or self.candidate_causes
            ):
                raise ValueError("稳定窗口必须输出 no_attribution_required 拒绝归因契约")
            safety = self.reasoning.get("safety")
            if (
                not isinstance(safety, dict)
                or safety.get("requires_human_approval") is not False
                or safety.get("automatic_action_allowed") is not False
            ):
                raise ValueError("稳定窗口的推理契约不得要求异常审批")
            return
        safety = self.reasoning.get("safety")
        if (
            not isinstance(safety, dict)
            or safety.get("requires_human_approval") is not True
            or safety.get("automatic_action_allowed") is not False
        ):
            raise ValueError("异常研判必须保留人工审批且禁止自动动作")
        if decision == "supported":
            if self.reasoning.get("disposition") != "attribution_hypotheses_supported":
                raise ValueError("supported reasoning 的 disposition 不一致")
            if not isinstance(conclusion, dict):
                raise ValueError("supported reasoning 缺少 conclusion")
            cited.extend(self._citation_list(conclusion.get("evidence_ids"), "conclusion"))
            hypotheses = self.reasoning.get("hypotheses")
            if not isinstance(hypotheses, list) or not hypotheses:
                raise ValueError("supported reasoning 缺少 hypotheses")
            for index, hypothesis in enumerate(hypotheses, start=1):
                if not isinstance(hypothesis, dict):
                    raise ValueError("reasoning hypothesis 必须是对象")
                cited.extend(
                    self._citation_list(
                        hypothesis.get("evidence_ids"), f"hypotheses[{index}]"
                    )
                )
        elif (
            self.reasoning.get("disposition") != "insufficient_evidence"
            or conclusion is not None
            or self.candidate_causes
        ):
            raise ValueError("refused reasoning 必须标记证据不足且不得保留结论或候选原因")
        unknown = set(cited) - evidence_ids
        if unknown:
            raise ValueError("reasoning 引用了未知 evidence_id：" + ", ".join(sorted(unknown)))

    @staticmethod
    def _citation_list(value: Any, label: str) -> list[str]:
        if not isinstance(value, list) or not value:
            raise ValueError(f"reasoning {label} 必须引用至少一个 evidence_id")
        if any(not isinstance(item, str) or not item.strip() for item in value):
            raise ValueError(f"reasoning {label} 的 evidence_id 非法")
        if len(value) != len(set(value)):
            raise ValueError(f"reasoning {label} 的 evidence_id 不得重复")
        return value

    def _validate_workflow(self, evidence_ids: set[str]) -> None:
        from .workflow import (
            CaseStatus,
            NOTE_REQUIRED_ACTIONS,
            TRANSITIONS,
            WORKFLOW_EVENT_ID,
            WorkflowAction,
            allowed_actions_for_status,
        )

        attribution_required = self.analysis_provenance["attribution_required"]
        if not self.workflow:
            expected_status = (
                "awaiting_engineer_review" if attribution_required else "monitoring_only"
            )
            if self.status != expected_status:
                raise ValueError(f"缺少 workflow 时风险卡必须处于 {expected_status}")
            return
        required_workflow_keys = {
            "schema_version",
            "case_id",
            "status",
            "allowed_actions",
            "human_approval_required",
            "automatic_stop_line_allowed",
            "events",
        }
        allowed_workflow_keys = required_workflow_keys | {"external_sync"}
        if set(self.workflow) - allowed_workflow_keys:
            raise ValueError("workflow 包含未知字段")
        missing_workflow_keys = required_workflow_keys - set(self.workflow)
        if missing_workflow_keys:
            raise ValueError(
                "workflow 缺少字段：" + ", ".join(sorted(missing_workflow_keys))
            )
        if self.workflow.get("schema_version") != "1.0":
            raise ValueError("workflow.schema_version 必须为 1.0")
        if self.workflow.get("case_id") != self.card_id:
            raise ValueError("workflow.case_id 与 card_id 不一致")
        if self.workflow.get("status") != self.status:
            raise ValueError("workflow.status 与风险卡 status 不一致")
        if self.workflow.get("automatic_stop_line_allowed") is not False:
            raise ValueError("workflow 不得允许自动停线")
        events = self.workflow.get("events")
        if not isinstance(events, list):
            raise ValueError("workflow.events 必须是列表")
        if not attribution_required:
            if self.workflow.get("human_approval_required") is not False:
                raise ValueError("稳定监控 workflow 不得要求异常审批")
            if self.workflow.get("allowed_actions") != [] or events:
                raise ValueError("稳定监控 workflow 不得包含处置动作或状态事件")
            if "external_sync" in self.workflow:
                raise ValueError("稳定监控 workflow 不得包含 external_sync")
            return
        if self.workflow.get("human_approval_required") is not True:
            raise ValueError("异常处置 workflow 必须保留人工审批")
        try:
            final_status = CaseStatus(self.status)
        except ValueError as exc:
            raise ValueError(f"workflow.status 非法：{self.status}") from exc
        expected_allowed_actions = list(allowed_actions_for_status(final_status))
        if self.workflow.get("allowed_actions") != expected_allowed_actions:
            raise ValueError("workflow.allowed_actions 与当前状态不一致")

        expected_status = CaseStatus.AWAITING_ENGINEER_REVIEW
        event_ids: list[str] = []
        previous_time: datetime | None = None
        action_ids = {item.action_id for item in self.recommended_actions}
        for event in events:
            if not isinstance(event, dict):
                raise ValueError("workflow event 必须是对象")
            expected_event_keys = {
                "event_id",
                "occurred_at",
                "action",
                "from_status",
                "to_status",
                "actor",
                "note",
                "evidence_ids",
                "task_ids",
            }
            if set(event) != expected_event_keys:
                raise ValueError("workflow event 字段不完整或包含未知字段")
            event_id = event.get("event_id")
            self._require_trimmed("workflow.event_id", event_id)
            if not WORKFLOW_EVENT_ID.fullmatch(event_id):
                raise ValueError("workflow event_id 必须是 WF- 加 12 位大写十六进制")
            event_ids.append(event_id)

            occurred_at = event.get("occurred_at")
            if not isinstance(occurred_at, str) or not UTC_RFC3339.fullmatch(occurred_at):
                raise ValueError("workflow event.occurred_at 必须是完整 UTC RFC3339 时间")
            try:
                parsed_time = datetime.fromisoformat(occurred_at[:-1] + "+00:00")
            except ValueError as exc:
                raise ValueError("workflow event.occurred_at 不是有效时间") from exc
            if previous_time is not None and parsed_time < previous_time:
                raise ValueError("workflow event.occurred_at 必须按时间非递减排列")
            previous_time = parsed_time

            actor = event.get("actor")
            self._require_trimmed("workflow event.actor", actor)
            note = event.get("note")
            if not isinstance(note, str) or note != note.strip():
                raise ValueError("workflow event.note 必须是已去除首尾空白的字符串")
            try:
                action = WorkflowAction(event.get("action"))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"workflow action 非法：{event.get('action')!r}") from exc
            if action in NOTE_REQUIRED_ACTIONS and not note:
                raise ValueError(f"workflow 动作 {action.value} 必须填写依据或说明")

            event_evidence = self._workflow_id_list(
                event.get("evidence_ids"), "workflow event.evidence_ids"
            )
            unknown_evidence = set(event_evidence) - evidence_ids
            if unknown_evidence:
                raise ValueError(
                    "workflow event 引用了未知 evidence_id："
                    + ", ".join(sorted(unknown_evidence))
                )
            if action == WorkflowAction.PASS_VERIFICATION and not event_evidence:
                raise ValueError("pass_verification 必须引用现场验证 evidence_id")

            task_ids = self._workflow_id_list(
                event.get("task_ids"), "workflow event.task_ids"
            )
            if action == WorkflowAction.CREATE_TASKS:
                if not task_ids:
                    raise ValueError("create_tasks 必须记录至少一个 task_id")
                if set(task_ids) != action_ids:
                    raise ValueError("create_tasks.task_ids 必须完整对应推荐处置任务")
            elif task_ids:
                raise ValueError("仅 create_tasks 动作可以记录 task_id")

            if event.get("from_status") != expected_status.value:
                raise ValueError("workflow 事件状态链不连续")
            target = TRANSITIONS.get((expected_status, action))
            if target is None or event.get("to_status") != target.value:
                raise ValueError("workflow 包含非法状态转换")
            expected_status = target
        if len(event_ids) != len(set(event_ids)):
            raise ValueError("workflow event_id 必须唯一")
        if expected_status != final_status:
            raise ValueError("workflow 事件链终态与风险卡 status 不一致")
        if "external_sync" in self.workflow:
            self._validate_external_sync(self.workflow["external_sync"])

    @classmethod
    def _workflow_id_list(cls, value: Any, label: str) -> list[str]:
        if not isinstance(value, list):
            raise ValueError(f"{label} 必须是列表")
        for item in value:
            cls._require_trimmed(label, item)
        if len(value) != len(set(value)):
            raise ValueError(f"{label} 不得重复")
        return value

    def _validate_external_sync(self, sync: Any) -> None:
        if not isinstance(sync, dict):
            raise ValueError("workflow.external_sync 必须是对象")
        required = {
            "schema_version",
            "mode",
            "card_id",
            "sync_status",
            "failure_stage",
            "request_ids",
            "remote_ids",
            "reconciliation_required",
            "automatic_retry_safe",
        }
        optional = {
            "workflow_status",
            "workflow_committed",
            "external_write_status",
            "error",
        }
        if required - set(sync) or set(sync) - (required | optional):
            raise ValueError("workflow.external_sync 字段不完整或包含未知字段")
        if sync["schema_version"] != "1.0" or sync["mode"] != "live":
            raise ValueError("workflow.external_sync schema_version/mode 非法")
        if sync["card_id"] != self.card_id:
            raise ValueError("workflow.external_sync.card_id 与风险卡不一致")
        status = sync["sync_status"]
        if status not in {"not_attempted", "partial", "succeeded", "failed"}:
            raise ValueError("workflow.external_sync.sync_status 非法")
        failure_stage = sync["failure_stage"]
        if failure_stage is not None:
            self._require_trimmed("workflow.external_sync.failure_stage", failure_stage)
            if re.fullmatch(r"[a-z][a-z0-9_]{0,63}", failure_stage) is None:
                raise ValueError("workflow.external_sync.failure_stage 格式非法")
        if not isinstance(sync["reconciliation_required"], bool):
            raise ValueError("workflow.external_sync.reconciliation_required 必须是布尔值")
        if not isinstance(sync["automatic_retry_safe"], bool):
            raise ValueError("workflow.external_sync.automatic_retry_safe 必须是布尔值")

        request_ids = sync["request_ids"]
        if not isinstance(request_ids, dict):
            raise ValueError("workflow.external_sync.request_ids 必须是对象")
        request_values: list[str] = []
        for stage, request_id in request_ids.items():
            if not isinstance(stage, str) or re.fullmatch(r"[a-z][a-z0-9_]{0,63}", stage) is None:
                raise ValueError("workflow.external_sync.request_ids 阶段名非法")
            self._require_trimmed("workflow.external_sync.request_id", request_id)
            request_values.append(request_id)
        if len(request_values) != len(set(request_values)):
            raise ValueError("workflow.external_sync.request_id 不得重复")

        remote_ids = sync["remote_ids"]
        if not isinstance(remote_ids, dict) or set(remote_ids) != {"risk", "tasks"}:
            raise ValueError("workflow.external_sync.remote_ids 必须包含 risk/tasks")
        risk_ids = self._workflow_id_list(
            remote_ids["risk"], "workflow.external_sync.remote_ids.risk"
        )
        task_ids = self._workflow_id_list(
            remote_ids["tasks"], "workflow.external_sync.remote_ids.tasks"
        )
        if len(risk_ids) > 1 or len(task_ids) > len(self.recommended_actions):
            raise ValueError("workflow.external_sync.remote_ids 数量超出请求范围")

        if "workflow_status" in sync:
            self._require_trimmed(
                "workflow.external_sync.workflow_status", sync["workflow_status"]
            )
        if "workflow_committed" in sync and not isinstance(sync["workflow_committed"], bool):
            raise ValueError("workflow.external_sync.workflow_committed 必须是布尔值")
        if "external_write_status" in sync:
            if sync["external_write_status"] not in {
                "not_attempted",
                "unverified",
                "partial",
                "succeeded",
                "failed",
            }:
                raise ValueError("workflow.external_sync.external_write_status 非法")
        if "error" in sync:
            error = sync["error"]
            if not isinstance(error, dict) or set(error) != {"type", "message"}:
                raise ValueError("workflow.external_sync.error 结构非法")
            self._require_trimmed("workflow.external_sync.error.type", error["type"])
            self._require_trimmed("workflow.external_sync.error.message", error["message"])

        tasks_exist_states = {
            "tasks_created",
            "verification_in_progress",
            "verified",
            "closed",
        }
        if status == "succeeded":
            if (
                failure_stage is not None
                or sync["reconciliation_required"]
                or sync["automatic_retry_safe"]
                or len(risk_ids) != 1
                or len(task_ids) != len(self.recommended_actions)
                or not {"risk_create", "task_create"} <= set(request_ids)
                or sync.get("workflow_status") != "tasks_created"
                or sync.get("workflow_committed") is not True
                or sync.get("external_write_status") != "succeeded"
                or self.status not in tasks_exist_states
                or "error" in sync
            ):
                raise ValueError("succeeded external_sync 与远端回执或工作流状态不一致")
            return

        if failure_stage is None:
            raise ValueError("未成功 external_sync 必须记录 failure_stage")
        if self.status in tasks_exist_states:
            raise ValueError("partial/failed/not_attempted external_sync 不得伪称 tasks_created")
        if sync.get("workflow_committed") is True:
            raise ValueError("未成功 external_sync 不得标记 workflow_committed=true")
        if sync.get("workflow_status") in tasks_exist_states:
            raise ValueError("未成功 external_sync 不得记录已创建任务状态")
        if status == "partial" and not sync["reconciliation_required"]:
            raise ValueError("partial external_sync 必须要求人工对账")
        if status == "partial" and not request_ids:
            raise ValueError("partial external_sync 必须保留至少一个 request_id")
        if status == "failed" and (risk_ids or task_ids):
            raise ValueError("failed external_sync 不得包含已确认远端 ID")
        if (
            status == "failed"
            and not sync["reconciliation_required"]
            and failure_stage
            not in {"configuration", "approval_validation", "payload_build"}
        ):
            raise ValueError("可能已发起外部请求的 failed external_sync 必须要求人工对账")
        if status == "not_attempted" and (risk_ids or task_ids):
            raise ValueError("not_attempted external_sync 不得包含远端 ID")
        if sync["automatic_retry_safe"] and (
            status not in {"not_attempted", "failed"} or risk_ids or task_ids
        ):
            raise ValueError("仅确认未写入远端的失败才可标记 automatic_retry_safe")
        if sync.get("external_write_status") == "succeeded" and status != "partial":
            raise ValueError("仅本地提交失败的 partial 可记录外部写入 succeeded")
