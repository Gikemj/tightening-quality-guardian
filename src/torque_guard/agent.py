from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from .models import RiskCard
from .reasoning import (
    ExternalModelConfig,
    ReasoningRequest,
    StructuredModelClient,
    StructuredReasoner,
    apply_reasoning,
    build_reasoner,
)
from .risk import RiskAnalyzer, read_events
from .workflow import AuditTrail, RiskCaseWorkflow, RUNTIME_TRACE_MODE


def _stable_monitoring_snapshot(card: RiskCard) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "case_id": card.card_id,
        "status": "monitoring_only",
        "allowed_actions": [],
        "human_approval_required": False,
        "automatic_stop_line_allowed": False,
        "events": [],
    }


class DigitalEmployee:
    """Event-driven orchestrator with an auditable tool trace.

    It does not change PLC parameters or make an automatic stop-line decision.
    Anomalies route to an accountable engineer; stable windows remain in
    monitoring-only state without manufacturing an approval task.
    """

    def __init__(
        self,
        knowledge_root: str | Path,
        *,
        reasoner: StructuredReasoner | None = None,
        external_model: ExternalModelConfig | None = None,
        model_client: StructuredModelClient | None = None,
        environment: Mapping[str, str] | None = None,
        trace_mode: str = RUNTIME_TRACE_MODE,
        trace_scope: str = "agent",
    ):
        self.analyzer = RiskAnalyzer(knowledge_root)
        self.reasoner = reasoner or build_reasoner(
            external_model, client=model_client, env=environment
        )
        self.last_trace: list[dict[str, Any]] = []
        self.last_workflow: RiskCaseWorkflow | None = None
        self.trace_mode = trace_mode
        self.trace_scope = trace_scope

    def run(
        self,
        event_file: str | Path,
        fastening_point: str,
        *,
        source_label: str | None = None,
    ) -> RiskCard:
        event_path = Path(event_file)
        trace_source = source_label or (
            event_path.name if self.trace_mode != RUNTIME_TRACE_MODE else str(event_path)
        )
        evidence_source = source_label or event_path.name
        return self._run_with_loader(
            fastening_point,
            loader=lambda: read_events(event_path),
            loader_tool="read_events",
            loader_input={"file": trace_source, "fastening_point": fastening_point},
            loader_result=lambda rows: f"读取并解析 {len(rows)} 条拧紧记录",
            evidence_source=evidence_source,
        )

    def run_events(
        self,
        events: Iterable[Mapping[str, Any]],
        fastening_point: str,
        *,
        source_label: str = "in_memory_events",
    ) -> RiskCard:
        """Analyze already-loaded events through the same audited agent pipeline."""

        return self._run_with_loader(
            fastening_point,
            loader=lambda: [dict(row) for row in events],
            loader_tool="accept_event_records",
            loader_input={"source": source_label, "fastening_point": fastening_point},
            loader_result=lambda rows: f"接收 {len(rows)} 条已加载拧紧记录",
            evidence_source=source_label,
        )

    def _run_with_loader(
        self,
        fastening_point: str,
        *,
        loader: Callable[[], list[dict[str, Any]]],
        loader_tool: str,
        loader_input: Mapping[str, Any],
        loader_result: Callable[[list[dict[str, Any]]], str],
        evidence_source: str,
    ) -> RiskCard:
        trail = AuditTrail(trace_mode=self.trace_mode, trace_scope=self.trace_scope)
        try:
            events = trail.invoke(
                step="sense",
                tool=loader_tool,
                input_summary=loader_input,
                operation=loader,
                summarize_output=lambda rows: {
                    "event_count": len(rows),
                    "field_count": len(rows[0]) if rows else 0,
                    "fastening_points": sorted(
                        {str(row.get("fastening_point", "")) for row in rows}
                    ),
                },
                describe_result=loader_result,
            )
            card = trail.invoke(
                step="analyze",
                tool="risk_analyzer.analyze",
                input_summary={
                    "event_count": len(events),
                    "fastening_point": fastening_point,
                    "baseline_count": self.analyzer.baseline_count,
                    "recent_count": self.analyzer.recent_count,
                },
                operation=lambda: self.analyzer.analyze(
                    events,
                    fastening_point,
                    event_source=evidence_source,
                ),
                summarize_output=lambda result: {
                    "card_id": result.card_id,
                    "risk_level": result.risk_level,
                    "risk_score": result.risk_score,
                    "evidence_ids": [item.evidence_id for item in result.evidence],
                    "candidate_cause_count": len(result.candidate_causes),
                    "attribution_required": result.analysis_provenance[
                        "attribution_required"
                    ],
                },
                describe_result=lambda result: (
                    f"生成 {result.risk_level} 风险卡，评分 {result.risk_score}，"
                    f"关联 {len(result.evidence)} 条证据"
                ),
            )
            reasoning_request = ReasoningRequest.from_card(card)

            def reason_and_apply() -> RiskCard:
                result = self.reasoner.reason(reasoning_request)
                return apply_reasoning(card, result)

            card = trail.invoke(
                step="reason",
                tool="structured_reasoner.reason",
                input_summary={
                    "card_id": card.card_id,
                    "evidence_ids": [item.evidence_id for item in card.evidence],
                    "proposed_cause_count": len(card.candidate_causes),
                    "attribution_required": reasoning_request.attribution_required,
                    "prompt_contract": "1.0",
                },
                operation=reason_and_apply,
                summarize_output=lambda result: {
                    "decision": result.reasoning.get("decision"),
                    "disposition": result.reasoning.get("disposition"),
                    "reasoner_mode": result.reasoning.get("provenance", {}).get(
                        "reasoner_mode"
                    ),
                    "fallback_reason": result.reasoning.get("provenance", {}).get(
                        "fallback_reason"
                    ),
                    "cited_evidence_ids": sorted(
                        {
                            evidence_id
                            for cause in result.candidate_causes
                            for evidence_id in cause.evidence_ids
                        }
                    ),
                },
                describe_result=lambda result: (
                    "当前窗口稳定，无需启动根因归因"
                    if result.reasoning.get("disposition")
                    == "no_attribution_required"
                    else "证据不足，拒绝自动归因"
                    if result.reasoning.get("decision") == "refused"
                    else f"形成 {len(result.candidate_causes)} 个带证据引用的待验证假设"
                ),
            )
            if card.analysis_provenance["attribution_required"]:
                workflow = trail.invoke(
                    step="govern",
                    tool="risk_case_workflow.initialize",
                    input_summary={
                        "card_id": card.card_id,
                        "requested_status": card.status,
                        "recommended_action_ids": [
                            item.action_id for item in card.recommended_actions
                        ],
                    },
                    operation=lambda: RiskCaseWorkflow.for_card(card),
                    summarize_output=lambda result: result.snapshot(),
                    describe_result=lambda result: (
                        f"进入 {result.status.value}，等待具名工程师审批；未创建外部任务"
                    ),
                )
                workflow_snapshot = workflow.snapshot()
                self.last_workflow = workflow
            else:
                workflow_snapshot = trail.invoke(
                    step="govern",
                    tool="risk_monitoring.record_stable_window",
                    input_summary={
                        "card_id": card.card_id,
                        "requested_status": card.status,
                        "recommended_action_ids": [],
                    },
                    operation=lambda: _stable_monitoring_snapshot(card),
                    summarize_output=lambda result: result,
                    describe_result=lambda _result: (
                        "记录稳定窗口并保持常规监控；未创建异常处置任务"
                    ),
                )
                self.last_workflow = None
        except Exception as exc:
            self.last_trace = trail.to_dicts()
            # Preserve the original exception type for existing callers while
            # making the failed tool record available to debuggers.
            try:
                setattr(exc, "agent_trace", self.last_trace)
            except Exception:
                pass
            raise

        card.agent_trace = trail.to_dicts()
        self.last_trace = list(card.agent_trace)
        card.workflow = workflow_snapshot
        return card
