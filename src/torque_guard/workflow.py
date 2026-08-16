from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
import re
from time import perf_counter
from typing import Any, Callable, Iterable, Mapping, TypeVar
from uuid import uuid4

from .models import RiskCard


T = TypeVar("T")

RUNTIME_TRACE_MODE = "runtime"
PUBLIC_BUILD_TRACE_MODE = "deterministic_public_build"
PUBLIC_BUILD_TIMESTAMP = "2026-07-20T00:00:00Z"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _isoformat(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_value(value: Any, *, depth: int = 0) -> Any:
    """Return a compact JSON-compatible value suitable for an audit log.

    Tool traces intentionally store summaries, not full source records or
    secrets.  This helper also prevents an accidental object dump from making
    the risk-card payload unbounded.
    """

    if depth >= 3:
        return "<summary truncated>"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= 300 else f"{value[:297]}..."
    if isinstance(value, Mapping):
        items = list(value.items())[:30]
        return {str(key): _safe_value(item, depth=depth + 1) for key, item in items}
    if isinstance(value, (list, tuple, set, frozenset)):
        items = list(value)
        compact = [_safe_value(item, depth=depth + 1) for item in items[:30]]
        if len(items) > 30:
            compact.append(f"<{len(items) - 30} more items>")
        return compact
    return _safe_value(str(value), depth=depth + 1)


class ToolCallStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True)
class ToolCallRecord:
    sequence: int
    call_id: str
    step: str
    tool: str
    status: str
    trace_mode: str
    started_at: str
    completed_at: str
    duration_ms: float
    input_summary: dict[str, Any]
    output_summary: dict[str, Any]
    result: str
    error: dict[str, str] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AuditTrail:
    """Execute tools and record what actually happened at the call boundary."""

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] = _utc_now,
        trace_mode: str = RUNTIME_TRACE_MODE,
        trace_scope: str = "agent",
    ) -> None:
        if trace_mode not in {RUNTIME_TRACE_MODE, PUBLIC_BUILD_TRACE_MODE}:
            raise ValueError(f"未知 trace_mode：{trace_mode}")
        self._clock = clock
        self.trace_mode = trace_mode
        normalized_scope = "".join(
            character if character.isalnum() else "-" for character in trace_scope
        ).strip("-")
        self.trace_scope = (normalized_scope or "agent")[:24].upper()
        self._records: list[ToolCallRecord] = []

    @property
    def records(self) -> tuple[ToolCallRecord, ...]:
        return tuple(self._records)

    def to_dicts(self) -> list[dict[str, Any]]:
        return [record.to_dict() for record in self._records]

    def invoke(
        self,
        *,
        step: str,
        tool: str,
        input_summary: Mapping[str, Any],
        operation: Callable[[], T],
        summarize_output: Callable[[T], Mapping[str, Any]],
        describe_result: Callable[[T], str],
    ) -> T:
        """Run one real operation and append either a success or failure record."""

        sequence = len(self._records) + 1
        deterministic = self.trace_mode == PUBLIC_BUILD_TRACE_MODE
        call_id = (
            f"CALL-PUBLIC-{self.trace_scope}-{sequence:03d}"
            if deterministic
            else f"CALL-{uuid4().hex[:12].upper()}"
        )
        started = self._clock() if not deterministic else None
        started_at = PUBLIC_BUILD_TIMESTAMP if deterministic else _isoformat(started)
        timer = perf_counter()
        try:
            output = operation()
            output_summary = dict(summarize_output(output))
            result = describe_result(output)
        except Exception as exc:
            completed_at = (
                PUBLIC_BUILD_TIMESTAMP
                if deterministic
                else _isoformat(self._clock())
            )
            self._records.append(
                ToolCallRecord(
                    sequence=sequence,
                    call_id=call_id,
                    step=step,
                    tool=tool,
                    status=ToolCallStatus.FAILED.value,
                    trace_mode=self.trace_mode,
                    started_at=started_at,
                    completed_at=completed_at,
                    duration_ms=(
                        0.0 if deterministic else round((perf_counter() - timer) * 1000, 3)
                    ),
                    input_summary=_safe_value(dict(input_summary)),
                    output_summary={},
                    result=f"执行失败：{type(exc).__name__}",
                    error={"type": type(exc).__name__, "message": _safe_value(str(exc))},
                )
            )
            raise
        completed_at = (
            PUBLIC_BUILD_TIMESTAMP if deterministic else _isoformat(self._clock())
        )
        self._records.append(
            ToolCallRecord(
                sequence=sequence,
                call_id=call_id,
                step=step,
                tool=tool,
                status=ToolCallStatus.SUCCEEDED.value,
                trace_mode=self.trace_mode,
                started_at=started_at,
                completed_at=completed_at,
                duration_ms=(
                    0.0 if deterministic else round((perf_counter() - timer) * 1000, 3)
                ),
                input_summary=_safe_value(dict(input_summary)),
                output_summary=_safe_value(output_summary),
                result=_safe_value(result),
                error=None,
            )
        )
        return output


class CaseStatus(str, Enum):
    AWAITING_ENGINEER_REVIEW = "awaiting_engineer_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    TASKS_CREATED = "tasks_created"
    VERIFICATION_IN_PROGRESS = "verification_in_progress"
    VERIFIED = "verified"
    CLOSED = "closed"


class WorkflowAction(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"
    RESUBMIT = "resubmit"
    CREATE_TASKS = "create_tasks"
    START_VERIFICATION = "start_verification"
    PASS_VERIFICATION = "pass_verification"
    FAIL_VERIFICATION = "fail_verification"
    CLOSE = "close"
    REOPEN = "reopen"


TRANSITIONS: dict[tuple[CaseStatus, WorkflowAction], CaseStatus] = {
    (CaseStatus.AWAITING_ENGINEER_REVIEW, WorkflowAction.APPROVE): CaseStatus.APPROVED,
    (CaseStatus.AWAITING_ENGINEER_REVIEW, WorkflowAction.REJECT): CaseStatus.REJECTED,
    (CaseStatus.REJECTED, WorkflowAction.RESUBMIT): CaseStatus.AWAITING_ENGINEER_REVIEW,
    (CaseStatus.APPROVED, WorkflowAction.CREATE_TASKS): CaseStatus.TASKS_CREATED,
    (CaseStatus.TASKS_CREATED, WorkflowAction.START_VERIFICATION): CaseStatus.VERIFICATION_IN_PROGRESS,
    (CaseStatus.VERIFICATION_IN_PROGRESS, WorkflowAction.PASS_VERIFICATION): CaseStatus.VERIFIED,
    (CaseStatus.VERIFICATION_IN_PROGRESS, WorkflowAction.FAIL_VERIFICATION): CaseStatus.TASKS_CREATED,
    (CaseStatus.VERIFIED, WorkflowAction.CLOSE): CaseStatus.CLOSED,
    (CaseStatus.CLOSED, WorkflowAction.REOPEN): CaseStatus.AWAITING_ENGINEER_REVIEW,
}

NOTE_REQUIRED_ACTIONS = frozenset(
    {
        WorkflowAction.APPROVE,
        WorkflowAction.REJECT,
        WorkflowAction.RESUBMIT,
        WorkflowAction.FAIL_VERIFICATION,
        WorkflowAction.CLOSE,
        WorkflowAction.REOPEN,
    }
)
WORKFLOW_EVENT_ID = re.compile(r"^WF-[0-9A-F]{12}$")


def allowed_actions_for_status(status: CaseStatus | str) -> tuple[str, ...]:
    normalized = CaseStatus(status)
    return tuple(
        action.value
        for (source, action), _target in TRANSITIONS.items()
        if source == normalized
    )


class InvalidWorkflowTransition(ValueError):
    """Raised when a case attempts to bypass an approval or verification gate."""


@dataclass(frozen=True)
class WorkflowTransition:
    event_id: str
    occurred_at: str
    action: str
    from_status: str
    to_status: str
    actor: str
    note: str
    evidence_ids: list[str] = field(default_factory=list)
    task_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class RiskCaseWorkflow:
    """Human-gated lifecycle for approval, task execution and closure.

    The state machine is deliberately separate from external integrations: a
    successful state transition records business authorization, while an API
    adapter is responsible for transmitting the corresponding payload.
    """

    _NOTE_REQUIRED = NOTE_REQUIRED_ACTIONS

    def __init__(
        self,
        case_id: str,
        *,
        initial_status: CaseStatus | str = CaseStatus.AWAITING_ENGINEER_REVIEW,
        clock: Callable[[], datetime] = _utc_now,
        card: RiskCard | None = None,
    ) -> None:
        if not isinstance(case_id, str) or not case_id.strip():
            raise ValueError("case_id 不能为空")
        try:
            self.status = CaseStatus(initial_status)
        except ValueError as exc:
            raise ValueError(f"未知工单状态：{initial_status}") from exc
        self.case_id = case_id
        self._clock = clock
        self._events: list[WorkflowTransition] = []
        self._card = card
        self._sync_card()

    @classmethod
    def for_card(cls, card: RiskCard) -> "RiskCaseWorkflow":
        return cls(card.card_id, initial_status=card.status, card=card)

    @property
    def events(self) -> tuple[WorkflowTransition, ...]:
        return tuple(self._events)

    @property
    def allowed_actions(self) -> tuple[str, ...]:
        return allowed_actions_for_status(self.status)

    def transition(
        self,
        action: WorkflowAction | str,
        *,
        actor: str,
        note: str = "",
        evidence_ids: Iterable[str] = (),
        task_ids: Iterable[str] = (),
    ) -> WorkflowTransition:
        try:
            normalized_action = WorkflowAction(action)
        except ValueError as exc:
            raise InvalidWorkflowTransition(f"未知流程动作：{action}") from exc
        target = TRANSITIONS.get((self.status, normalized_action))
        if target is None:
            allowed = ", ".join(self.allowed_actions) or "无"
            raise InvalidWorkflowTransition(
                f"状态 {self.status.value} 不允许动作 {normalized_action.value}；允许动作：{allowed}"
            )

        if not isinstance(actor, str) or not isinstance(note, str):
            raise ValueError("流程责任人和说明必须是字符串")
        actor = actor.strip()
        note = note.strip()
        evidence = self._validated_ids(evidence_ids, "evidence_id")
        tasks = self._validated_ids(task_ids, "task_id")
        if not actor:
            raise ValueError("流程动作必须记录责任人")
        if normalized_action in self._NOTE_REQUIRED and not note:
            raise ValueError(f"动作 {normalized_action.value} 必须填写依据或说明")
        if normalized_action == WorkflowAction.CREATE_TASKS and not tasks:
            raise ValueError("创建任务时必须提供至少一个 task_id")
        if normalized_action != WorkflowAction.CREATE_TASKS and tasks:
            raise ValueError("仅 create_tasks 动作可以记录 task_id")
        if normalized_action == WorkflowAction.PASS_VERIFICATION and not evidence:
            raise ValueError("验证通过时必须提供现场验证 evidence_id")

        previous = self.status
        event = WorkflowTransition(
            event_id=f"WF-{uuid4().hex[:12].upper()}",
            occurred_at=_isoformat(self._clock()),
            action=normalized_action.value,
            from_status=previous.value,
            to_status=target.value,
            actor=actor,
            note=note,
            evidence_ids=evidence,
            task_ids=tasks,
        )
        self.status = target
        self._events.append(event)
        self._sync_card()
        return event

    @staticmethod
    def _validated_ids(values: Iterable[str], label: str) -> list[str]:
        if isinstance(values, (str, bytes)):
            raise ValueError(f"{label} 必须通过字符串列表传入")
        try:
            raw_values = list(values)
        except TypeError as exc:
            raise ValueError(f"{label} 必须通过字符串列表传入") from exc
        normalized: list[str] = []
        for value in raw_values:
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{label} 必须是非空字符串")
            normalized.append(value.strip())
        if len(normalized) != len(set(normalized)):
            raise ValueError(f"{label} 不得重复")
        return normalized

    def snapshot(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "case_id": self.case_id,
            "status": self.status.value,
            "allowed_actions": list(self.allowed_actions),
            "human_approval_required": True,
            "automatic_stop_line_allowed": False,
            "events": [event.to_dict() for event in self._events],
        }

    def _sync_card(self) -> None:
        if self._card is None:
            return
        existing_external_sync = (
            self._card.workflow.get("external_sync")
            if isinstance(self._card.workflow, dict)
            else None
        )
        self._card.status = self.status.value
        snapshot = self.snapshot()
        if existing_external_sync is not None:
            snapshot["external_sync"] = existing_external_sync
        self._card.workflow = snapshot
