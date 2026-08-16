from __future__ import annotations

import argparse
from pathlib import Path
from types import TracebackType
from typing import Any, Mapping

from .agent import DigitalEmployee
from .artifacts import write_json
from .integrations.feishu import (
    ApprovalReceipt,
    FeishuAPIError,
    FeishuBitableClient,
    FeishuConfig,
    build_bitable_payloads,
    build_bitable_records,
)
from .models import RiskCard
from .workflow import WorkflowAction


PUBLIC_RISK_OUTPUT = "outputs/risk_card.json"
PUBLIC_FEISHU_PREVIEW_OUTPUT = "outputs/feishu_records_preview.json"
PRIVATE_LIVE_RISK_OUTPUT = ".local/live/risk_card.json"
PRIVATE_LIVE_FEISHU_OUTPUT = ".local/live/feishu_records_preview.json"


def _resolve_output_paths(
    feishu_mode: str,
    *,
    output_path: str | None,
    feishu_preview_path: str | None,
) -> tuple[str, str]:
    """Keep live identities and remote IDs out of public generated artifacts."""

    if feishu_mode == "live":
        return (
            output_path if output_path is not None else PRIVATE_LIVE_RISK_OUTPUT,
            (
                feishu_preview_path
                if feishu_preview_path is not None
                else PRIVATE_LIVE_FEISHU_OUTPUT
            ),
        )
    return (
        output_path if output_path is not None else PUBLIC_RISK_OUTPUT,
        (
            feishu_preview_path
            if feishu_preview_path is not None
            else PUBLIC_FEISHU_PREVIEW_OUTPUT
        ),
    )


def _sync_summary(result: Mapping[str, Any]) -> dict[str, Any]:
    """Keep only reconciliation-safe metadata in the persisted risk card."""

    optional_keys = (
        "workflow_status",
        "workflow_committed",
        "external_write_status",
        "error",
    )
    reconciliation_required = result.get(
        "reconciliation_required",
        result.get("manual_reconciliation_required", False),
    )
    return {
        "schema_version": "1.0",
        "mode": result.get("mode", "live"),
        "card_id": result.get("card_id"),
        "sync_status": result.get("sync_status"),
        "failure_stage": result.get("failure_stage"),
        "request_ids": result.get("request_ids", {}),
        "remote_ids": result.get("remote_ids", {"risk": [], "tasks": []}),
        "reconciliation_required": reconciliation_required,
        "automatic_retry_safe": result.get("automatic_retry_safe", False),
        **{key: result[key] for key in optional_keys if key in result},
    }


def _attach_sync_summary(card: RiskCard, result: Mapping[str, Any]) -> None:
    if not isinstance(card.workflow, dict):
        card.workflow = {}
    card.workflow["external_sync"] = _sync_summary(result)


def _failed_sync_result(
    card: RiskCard, exc: Exception, *, default_stage: str
) -> dict[str, Any]:
    existing = getattr(exc, "sync_result", None)
    if isinstance(existing, dict):
        return existing
    return {
        "mode": "live",
        "card_id": card.card_id,
        "sync_status": "not_attempted",
        "failure_stage": getattr(exc, "failure_stage", "") or default_stage,
        "request_ids": {},
        "remote_ids": {"risk": [], "tasks": []},
        "manual_reconciliation_required": False,
        "automatic_retry_safe": False,
        "error": {"type": type(exc).__name__, "message": str(exc)},
    }


def _verified_remote_ids(result: Mapping[str, Any], card: RiskCard) -> bool:
    remote_ids = result.get("remote_ids")
    request_ids = result.get("request_ids")
    risk_records = result.get("risk_records")
    task_records = result.get("task_records")
    if not isinstance(remote_ids, dict):
        return False
    risk_ids = remote_ids.get("risk")
    task_ids = remote_ids.get("tasks")
    if not (
        isinstance(risk_ids, list)
        and len(risk_ids) == 1
        and all(isinstance(item, str) and item.strip() for item in risk_ids)
        and isinstance(task_ids, list)
        and len(task_ids) == len(card.recommended_actions)
        and all(isinstance(item, str) and item.strip() for item in task_ids)
        and len(set(task_ids)) == len(task_ids)
        and isinstance(request_ids, dict)
        and all(
            isinstance(request_ids.get(stage), str)
            and bool(request_ids[stage].strip())
            for stage in ("risk_create", "task_create")
        )
        and isinstance(risk_records, list)
        and len(risk_records) == 1
        and isinstance(task_records, list)
        and len(task_records) == len(card.recommended_actions)
    ):
        return False

    def record_ids(records: list[Any]) -> list[str] | None:
        values: list[str] = []
        for record in records:
            if not isinstance(record, dict):
                return None
            value = record.get("record_id")
            if not isinstance(value, str) or not value.strip():
                return None
            values.append(value.strip())
        return values

    return record_ids(risk_records) == risk_ids and record_ids(task_records) == task_ids


def publish_live_after_workflow_approval(
    employee: DigitalEmployee,
    card: RiskCard,
    client: FeishuBitableClient,
    *,
    approved_by: str,
    approval_note: str,
) -> dict:
    """Record approval locally, publish, then mark tasks created on success."""

    workflow = employee.last_workflow
    if workflow is None:
        raise RuntimeError("风险卡缺少可用的审批工作流")
    approval_event = workflow.transition(
        WorkflowAction.APPROVE,
        actor=approved_by,
        note=approval_note,
    )
    receipt = ApprovalReceipt.from_transition(card.card_id, approval_event)
    try:
        result = client.publish_after_approval(
            card,
            approved_by=approved_by,
            approval_note=approval_note,
            approval_receipt=receipt,
        )
    except Exception as exc:
        sync_result = _failed_sync_result(card, exc, default_stage="live_publish")
        _attach_sync_summary(card, sync_result)
        raise

    if not isinstance(result, dict) or result.get("sync_status") != "succeeded":
        unsafe_result = dict(result) if isinstance(result, dict) else {}
        unsafe_result.update(
            {
                "mode": "live",
                "card_id": card.card_id,
                "sync_status": (
                    unsafe_result.get("sync_status")
                    if unsafe_result.get("sync_status") in {"partial", "failed"}
                    else "failed"
                ),
                "failure_stage": unsafe_result.get("failure_stage")
                or "response_reconciliation",
                "manual_reconciliation_required": True,
                "automatic_retry_safe": False,
            }
        )
        error = FeishuAPIError(
            "飞书客户端未返回完整成功状态，禁止推进到 tasks_created",
            failure_stage=unsafe_result["failure_stage"],
            sync_result=unsafe_result,
        )
        _attach_sync_summary(card, unsafe_result)
        raise error
    if not _verified_remote_ids(result, card):
        unsafe_result = dict(result)
        unsafe_result.update(
            {
                "sync_status": "partial",
                "external_write_status": "unverified",
                "failure_stage": "response_reconciliation",
                "manual_reconciliation_required": True,
            }
        )
        error = FeishuAPIError(
            "飞书成功结果中的远端 ID 不完整，禁止推进到 tasks_created",
            failure_stage="response_reconciliation",
            sync_result=unsafe_result,
        )
        _attach_sync_summary(card, unsafe_result)
        raise error

    try:
        workflow.transition(
            WorkflowAction.CREATE_TASKS,
            actor=approved_by,
            task_ids=[action.action_id for action in card.recommended_actions],
        )
    except Exception as exc:
        unsafe_result = dict(result)
        unsafe_result.update(
            {
                "sync_status": "partial",
                "external_write_status": "succeeded",
                "failure_stage": "local_workflow_commit",
                "manual_reconciliation_required": True,
                "workflow_committed": False,
                "error": {"type": type(exc).__name__, "message": str(exc)},
            }
        )
        error = FeishuAPIError(
            "飞书记录已创建，但本地工作流提交失败，必须人工对账",
            failure_stage="local_workflow_commit",
            sync_result=unsafe_result,
        )
        _attach_sync_summary(card, unsafe_result)
        raise error from exc

    completed_result = dict(result)
    completed_result.update(
        {
            "workflow_status": card.status,
            "workflow_committed": True,
            "external_write_status": "succeeded",
        }
    )
    _attach_sync_summary(card, completed_result)
    return completed_result


def _approval_event(card: RiskCard) -> dict[str, Any] | None:
    if not isinstance(card.workflow, dict):
        return None
    events = card.workflow.get("events")
    if not isinstance(events, list):
        return None
    return next(
        (
            event
            for event in reversed(events)
            if isinstance(event, dict) and event.get("action") == "approve"
        ),
        None,
    )


def _build_preview_records(
    card: RiskCard, *, feishu_mode: str, live_result: Mapping[str, Any] | None
) -> list[dict[str, Any]]:
    approval = _approval_event(card)
    if feishu_mode != "live" or approval is None:
        return build_bitable_records(card)
    sync_status = (
        live_result.get("sync_status")
        if isinstance(live_result, Mapping)
        else "not_attempted"
    )
    if sync_status not in {"not_attempted", "partial", "succeeded", "failed"}:
        sync_status = "failed"
    if sync_status == "succeeded" and card.status != "tasks_created":
        # External writes without the local state commit are not an end-to-end success.
        sync_status = "partial"
    payloads = build_bitable_payloads(
        card,
        approved_by=str(approval.get("actor", "")).strip(),
        approval_note=str(approval.get("note", "")).strip(),
        preview_sync_status=sync_status,
    )
    return [payloads["risk_record"], *payloads["task_records"]]


def _persist_outputs(
    card: RiskCard,
    *,
    output_path: str,
    feishu_preview_path: str,
    feishu_mode: str,
    live_result: Mapping[str, Any] | None,
) -> tuple[Path, Path]:
    output = Path(output_path)
    write_json(output, card.to_dict())

    feishu_output = Path(feishu_preview_path)
    preview_records = _build_preview_records(
        card, feishu_mode=feishu_mode, live_result=live_result
    )
    write_json(feishu_output, preview_records)
    return output, feishu_output


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the TorqueGuard competition prototype")
    parser.add_argument("--input", default="data/tightening_events_demo.csv")
    parser.add_argument("--knowledge", default="knowledge")
    parser.add_argument("--point", default="P03")
    parser.add_argument(
        "--output",
        default=None,
        help=(
            "risk-card output; defaults to outputs/ in preview mode and the "
            "git-ignored .local/live/ directory in live mode"
        ),
    )
    parser.add_argument(
        "--feishu-preview",
        default=None,
        help=(
            "Feishu payload preview; defaults to outputs/ in preview mode and the "
            "git-ignored .local/live/ directory in live mode"
        ),
    )
    parser.add_argument(
        "--feishu-mode",
        choices=("preview", "live"),
        default="preview",
        help="preview only writes local JSON; live requires FEISHU_* environment variables",
    )
    parser.add_argument("--approved-by", default="", help="required approver identity in live mode")
    parser.add_argument("--approval-note", default="", help="required approval basis in live mode")
    args = parser.parse_args()
    output_path, feishu_preview_path = _resolve_output_paths(
        args.feishu_mode,
        output_path=args.output,
        feishu_preview_path=args.feishu_preview,
    )

    employee = DigitalEmployee(args.knowledge)
    card = employee.run(args.input, args.point)
    live_result: dict[str, Any] | None = None
    live_error: Exception | None = None
    live_traceback: TracebackType | None = None
    try:
        if args.feishu_mode == "live":
            client = FeishuBitableClient(FeishuConfig.from_env())
            live_result = publish_live_after_workflow_approval(
                employee,
                card,
                client,
                approved_by=args.approved_by,
                approval_note=args.approval_note,
            )
    except Exception as exc:
        live_error = exc
        live_traceback = exc.__traceback__
        live_result = _failed_sync_result(card, exc, default_stage="configuration")
        _attach_sync_summary(card, live_result)

    try:
        output, feishu_output = _persist_outputs(
            card,
            output_path=output_path,
            feishu_preview_path=feishu_preview_path,
            feishu_mode=args.feishu_mode,
            live_result=live_result,
        )
    except Exception as persistence_error:
        if live_error is None:
            raise
        setattr(live_error, "persistence_error", persistence_error)
        if hasattr(live_error, "add_note"):
            live_error.add_note(
                "保存本地风险卡/飞书预览也失败："
                f"{type(persistence_error).__name__}: {persistence_error}"
            )

    if live_error is not None:
        raise live_error.with_traceback(live_traceback)

    if live_result is not None and live_result.get("sync_status") == "succeeded":
        print(
            f"Feishu live sync succeeded: risk={len(live_result['remote_ids']['risk'])}, "
            f"tasks={len(live_result['remote_ids']['tasks'])}"
        )
    else:
        print(f"Feishu preview only -> {feishu_output}")
    print(f"{card.card_id}: {card.risk_level} ({card.risk_score}) -> {output}")


if __name__ == "__main__":
    main()
