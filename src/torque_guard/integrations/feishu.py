from __future__ import annotations

import json
import math
import os
import re
import socket
import time
from collections import Counter
from dataclasses import dataclass
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import urlsplit
from uuid import uuid4

from ..models import RiskCard
from ..workflow import WorkflowAction, WorkflowTransition


FEISHU_OPEN_API = "https://open.feishu.cn/open-apis"


class FeishuConfigurationError(ValueError):
    """Raised when live mode is requested without complete credentials."""


class FeishuAPIError(RuntimeError):
    """Raised when Feishu rejects a request or returns an invalid response.

    Live errors carry reconciliation metadata.  Callers can inspect
    ``request_id``, ``failure_stage`` and ``sync_result`` without parsing the
    human-readable message.
    """

    def __init__(
        self,
        message: str,
        *,
        request_id: str = "",
        failure_stage: str = "",
        sync_result: dict[str, Any] | None = None,
        confirmed_records: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        self.request_id = request_id
        self.failure_stage = failure_stage
        self.sync_result = sync_result
        self.confirmed_records = list(confirmed_records or [])


class FeishuTransportError(FeishuAPIError):
    """A transport failure whose delivery state may be unknown."""

    def __init__(self, message: str, *, definitely_not_delivered: bool = False) -> None:
        super().__init__(message)
        self.definitely_not_delivered = definitely_not_delivered


class FeishuApprovalError(ValueError):
    """Raised when a live publish has no verifiable workflow approval."""

    def __init__(self, message: str, *, sync_result: dict[str, Any]) -> None:
        super().__init__(message)
        self.sync_result = sync_result
        self.failure_stage = str(
            sync_result.get("failure_stage") or "approval_validation"
        )
        self.request_id = ""


@dataclass(frozen=True)
class ApprovalReceipt:
    """Strongly typed proof that the local workflow accepted an approval."""

    card_id: str
    event_id: str
    occurred_at: str
    actor: str
    note: str
    action: str = WorkflowAction.APPROVE.value
    from_status: str = "awaiting_engineer_review"
    to_status: str = "approved"

    @classmethod
    def from_transition(
        cls, card_id: str, transition: WorkflowTransition
    ) -> "ApprovalReceipt":
        if not isinstance(transition, WorkflowTransition):
            raise TypeError("approval receipt 必须来自 WorkflowTransition")
        if transition.action != WorkflowAction.APPROVE.value:
            raise ValueError("approval receipt 必须来自 approve 流程事件")
        return cls(
            card_id=card_id,
            event_id=transition.event_id,
            occurred_at=transition.occurred_at,
            actor=transition.actor,
            note=transition.note,
            action=transition.action,
            from_status=transition.from_status,
            to_status=transition.to_status,
        )


@dataclass(frozen=True)
class FeishuConfig:
    """Environment-backed configuration for a least-privilege Bitable app."""

    app_id: str
    app_secret: str
    app_token: str
    risk_table_id: str
    task_table_id: str
    case_table_id: str = ""
    api_base: str = FEISHU_OPEN_API
    test_mode: bool = False

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "FeishuConfig":
        values = os.environ if env is None else env
        config = cls(
            app_id=values.get("FEISHU_APP_ID", "").strip(),
            app_secret=values.get("FEISHU_APP_SECRET", "").strip(),
            app_token=values.get("FEISHU_BITABLE_APP_TOKEN", "").strip(),
            risk_table_id=values.get("FEISHU_RISK_TABLE_ID", "").strip(),
            task_table_id=values.get("FEISHU_TASK_TABLE_ID", "").strip(),
            case_table_id=values.get("FEISHU_CASE_TABLE_ID", "").strip(),
            api_base=values.get("FEISHU_API_BASE", FEISHU_OPEN_API).rstrip("/"),
        )
        config.validate()
        return config

    def validate(self) -> None:
        if not isinstance(self.api_base, str):
            raise FeishuConfigurationError("FEISHU_API_BASE 必须是字符串")
        if type(self.test_mode) is not bool:
            raise FeishuConfigurationError("test_mode 必须是布尔值")
        text_fields = {
            "FEISHU_APP_ID": self.app_id,
            "FEISHU_APP_SECRET": self.app_secret,
            "FEISHU_BITABLE_APP_TOKEN": self.app_token,
            "FEISHU_RISK_TABLE_ID": self.risk_table_id,
            "FEISHU_TASK_TABLE_ID": self.task_table_id,
        }
        wrong_types = [name for name, value in text_fields.items() if not isinstance(value, str)]
        if wrong_types:
            raise FeishuConfigurationError(
                f"飞书配置必须是字符串：{', '.join(wrong_types)}"
            )
        missing = [
            name
            for name, value in text_fields.items()
            if not value.strip()
        ]
        if missing:
            raise FeishuConfigurationError(f"飞书实时模式缺少配置：{', '.join(missing)}")
        untrimmed = [
            name for name, value in text_fields.items() if value != value.strip()
        ]
        if untrimmed:
            raise FeishuConfigurationError(
                f"飞书配置不得包含首尾空白：{', '.join(untrimmed)}"
            )
        parsed = urlsplit(self.api_base)
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise FeishuConfigurationError(
                "FEISHU_API_BASE 必须是无账号、查询参数和片段的 HTTPS 地址"
            )
        normalized_base = self.api_base.rstrip("/")
        if normalized_base != FEISHU_OPEN_API and not self.test_mode:
            raise FeishuConfigurationError(
                "生产模式 FEISHU_API_BASE 只允许 https://open.feishu.cn/open-apis"
            )
        identifier_fields = {
            "FEISHU_APP_ID": self.app_id,
            "FEISHU_BITABLE_APP_TOKEN": self.app_token,
            "FEISHU_RISK_TABLE_ID": self.risk_table_id,
            "FEISHU_TASK_TABLE_ID": self.task_table_id,
        }
        invalid = [
            name
            for name, value in identifier_fields.items()
            if re.fullmatch(r"[A-Za-z0-9_-]+", value) is None
        ]
        if invalid:
            raise FeishuConfigurationError(
                f"飞书标识符包含非法路径字符：{', '.join(invalid)}"
            )


Transport = Callable[[str, str, dict[str, Any] | None, dict[str, str], float], dict[str, Any]]


def _urllib_transport(
    method: str,
    url: str,
    payload: dict[str, Any] | None,
    headers: dict[str, str],
    timeout: float,
) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    request = Request(url, data=body, method=method, headers=headers)
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed/configured HTTPS API
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise FeishuAPIError(f"飞书 HTTP {exc.code}: {detail[:500]}") from exc
    except URLError as exc:
        definitely_not_delivered = isinstance(
            exc.reason, (ConnectionRefusedError, socket.gaierror)
        )
        raise FeishuTransportError(
            f"飞书网络请求失败：{exc.reason}",
            definitely_not_delivered=definitely_not_delivered,
        ) from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise FeishuAPIError("飞书返回了非 JSON 响应") from exc


class FeishuBitableClient:
    """Small auditable client for the approved risk/task Bitable workflow.

    The client never reads credentials from repository files.  It only enters
    live mode when a complete :class:`FeishuConfig` is provided by the caller.
    """

    def __init__(
        self,
        config: FeishuConfig,
        *,
        transport: Transport = _urllib_transport,
        timeout: float = 10.0,
        max_attempts: int = 3,
    ) -> None:
        config.validate()
        if (
            config.api_base.rstrip("/") != FEISHU_OPEN_API
            and (not config.test_mode or transport is _urllib_transport)
        ):
            raise FeishuConfigurationError(
                "自定义飞书端点仅允许 test_mode=True 且注入测试 transport"
            )
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or timeout <= 0
        ):
            raise ValueError("timeout 必须是有限正数")
        if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or max_attempts < 1:
            raise ValueError("max_attempts 必须是正整数")
        self.config = config
        self._api_base = config.api_base.rstrip("/")
        self.transport = transport
        self.timeout = timeout
        self.max_attempts = max_attempts
        self._tenant_token = ""
        self._last_auth_request_id = ""
        self._publish_history: dict[str, dict[str, Any]] = {}

    def _call(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None,
        *,
        authenticated: bool = True,
        failure_stage: str,
        retry_transport_errors: bool = False,
    ) -> tuple[dict[str, Any], str]:
        headers = {"Content-Type": "application/json; charset=utf-8"}
        if authenticated:
            headers["Authorization"] = f"Bearer {self._get_tenant_token()}"
        request_id = f"TG-{uuid4().hex.upper()}"
        headers["X-TorqueGuard-Request-ID"] = request_id
        last_error: FeishuAPIError | None = None
        attempts = self.max_attempts if retry_transport_errors else 1
        for attempt in range(1, attempts + 1):
            try:
                result = self.transport(
                    method,
                    f"{self._api_base}{path}",
                    payload,
                    headers,
                    self.timeout,
                )
                if not isinstance(result, dict) or type(result.get("code")) is not int:
                    raise FeishuAPIError("飞书接口返回缺少整数 code 的无效响应")
                if result["code"] != 0:
                    message = str(result.get("msg", ""))[:500]
                    raise FeishuAPIError(
                        f"飞书接口拒绝请求 code={result['code']} msg={message}"
                    )
                return result, request_id
            except FeishuAPIError as exc:
                if not exc.request_id:
                    exc.request_id = request_id
                if not exc.failure_stage:
                    exc.failure_stage = failure_stage
                last_error = exc
                should_retry = (
                    retry_transport_errors
                    and isinstance(exc, FeishuTransportError)
                    and attempt < attempts
                )
                if should_retry:
                    time.sleep(min(0.25 * 2 ** (attempt - 1), 1.0))
                    continue
                raise
        assert last_error is not None
        raise last_error

    def _get_tenant_token(self) -> str:
        if self._tenant_token:
            return self._tenant_token
        result, request_id = self._call(
            "POST",
            "/auth/v3/tenant_access_token/internal",
            {"app_id": self.config.app_id, "app_secret": self.config.app_secret},
            authenticated=False,
            failure_stage="authentication",
            retry_transport_errors=True,
        )
        token = result.get("tenant_access_token")
        if not isinstance(token, str) or not token.strip():
            raise FeishuAPIError(
                "飞书鉴权成功响应中缺少非空 tenant_access_token",
                request_id=request_id,
                failure_stage="authentication",
            )
        self._last_auth_request_id = request_id
        self._tenant_token = token.strip()
        return self._tenant_token

    @staticmethod
    def _confirmed_records(returned_records: Any) -> list[dict[str, Any]]:
        if not isinstance(returned_records, list):
            return []
        return [
            item
            for item in returned_records
            if isinstance(item, dict)
            and isinstance(item.get("record_id"), str)
            and bool(item["record_id"].strip())
        ]

    def _batch_create_records(
        self,
        table_id: str,
        records: list[dict[str, Any]],
        *,
        business_key: str | None,
        failure_stage: str,
    ) -> tuple[list[dict[str, Any]], str]:
        if not isinstance(table_id, str) or re.fullmatch(r"[A-Za-z0-9_-]+", table_id) is None:
            raise FeishuAPIError(
                "table_id 包含非法路径字符", failure_stage=failure_stage
            )
        if not isinstance(records, list):
            raise FeishuAPIError(
                "records 必须是列表", failure_stage=failure_stage
            )
        if not records:
            return [], ""

        expected_keys: list[str] = []
        if business_key:
            for record in records:
                fields = record.get("fields") if isinstance(record, dict) else None
                value = fields.get(business_key) if isinstance(fields, dict) else None
                if not isinstance(value, str) or not value.strip():
                    raise FeishuAPIError(
                        f"请求记录缺少业务唯一键：{business_key}",
                        failure_stage=failure_stage,
                    )
                expected_keys.append(value.strip())
            if len(set(expected_keys)) != len(expected_keys):
                raise FeishuAPIError(
                    f"请求记录含重复业务唯一键：{business_key}",
                    failure_stage=failure_stage,
                )
        path = (
            f"/bitable/v1/apps/{self.config.app_token}/tables/{table_id}"
            "/records/batch_create"
        )
        result, request_id = self._call(
            "POST",
            path,
            {"records": records},
            failure_stage=failure_stage,
            # A create may have reached Feishu even when its response was lost.
            # Without an API idempotency contract, replaying it can duplicate rows.
            retry_transport_errors=False,
        )
        data = result.get("data")
        returned_records = data.get("records") if isinstance(data, dict) else None
        confirmed = self._confirmed_records(returned_records)

        def invalid(message: str) -> None:
            raise FeishuAPIError(
                message,
                request_id=request_id,
                failure_stage=failure_stage,
                confirmed_records=confirmed,
            )

        if not isinstance(returned_records, list):
            invalid("飞书批量创建响应缺少 data.records 列表")
        if len(returned_records) != len(records):
            invalid(
                "飞书批量创建响应记录数不一致："
                f"请求 {len(records)}，返回 {len(returned_records)}"
            )
        malformed = [
            index
            for index, item in enumerate(returned_records)
            if not isinstance(item, dict)
            or not isinstance(item.get("record_id"), str)
            or not item["record_id"].strip()
        ]
        if malformed:
            invalid(f"飞书批量创建响应含无效 record_id，位置：{malformed}")
        record_ids = [item["record_id"].strip() for item in returned_records]
        if len(set(record_ids)) != len(record_ids):
            invalid("飞书批量创建响应含重复 record_id")

        if business_key:
            visible_keys: list[str] = []
            visible_count = 0
            for item in returned_records:
                fields = item.get("fields")
                if fields is None:
                    continue
                visible_count += 1
                if not isinstance(fields, dict):
                    invalid("飞书批量创建响应含畸形 fields")
                value = fields.get(business_key)
                if not isinstance(value, str) or not value.strip():
                    invalid(f"飞书批量创建响应缺少业务唯一键：{business_key}")
                visible_keys.append(value.strip())
            if visible_count not in {0, len(returned_records)}:
                invalid("飞书批量创建响应仅部分返回业务唯一键，无法安全对账")
            if visible_count and Counter(visible_keys) != Counter(expected_keys):
                invalid(f"飞书批量创建响应业务唯一键不匹配：{business_key}")
        return returned_records, request_id

    def batch_create_records(
        self,
        table_id: str,
        records: list[dict[str, Any]],
        *,
        business_key: str | None = None,
        failure_stage: str = "batch_create",
    ) -> list[dict[str, Any]]:
        returned_records, _request_id = self._batch_create_records(
            table_id,
            records,
            business_key=business_key,
            failure_stage=failure_stage,
        )
        return returned_records

    @staticmethod
    def _new_sync_result(card: RiskCard) -> dict[str, Any]:
        return {
            "mode": "live",
            "card_id": card.card_id,
            "sync_status": "not_attempted",
            "failure_stage": None,
            "request_ids": {},
            "remote_ids": {"risk": [], "tasks": []},
            "risk_records": [],
            "task_records": [],
            "manual_reconciliation_required": False,
            "automatic_retry_safe": False,
        }

    @staticmethod
    def _validate_approval(
        card: RiskCard,
        *,
        approved_by: str,
        approval_note: str,
        approval_receipt: ApprovalReceipt | None,
        sync_result: dict[str, Any],
    ) -> None:
        if not isinstance(approved_by, str) or not isinstance(approval_note, str):
            raise FeishuApprovalError(
                "批准人和批准依据必须是字符串", sync_result=sync_result
            )
        actor = approved_by.strip()
        note = approval_note.strip()
        if not actor or not note:
            raise FeishuApprovalError(
                "实时派单必须记录批准人和批准依据", sync_result=sync_result
            )
        if card.status != "approved":
            raise FeishuApprovalError(
                f"风险卡状态必须为 approved，当前为 {card.status}",
                sync_result=sync_result,
            )

        workflow = card.workflow if isinstance(card.workflow, dict) else {}
        if workflow.get("case_id") != card.card_id:
            raise FeishuApprovalError(
                "风险卡与审批工作流 case_id 不一致", sync_result=sync_result
            )
        events = workflow.get("events")
        if workflow.get("status") != "approved" or not isinstance(events, list):
            raise FeishuApprovalError(
                "风险卡工作流未记录 approved 状态", sync_result=sync_result
            )

        if approval_receipt is not None:
            if not isinstance(approval_receipt, ApprovalReceipt):
                raise FeishuApprovalError(
                    "approval_receipt 类型无效", sync_result=sync_result
                )
            receipt_text_values = (
                approval_receipt.card_id,
                approval_receipt.event_id,
                approval_receipt.occurred_at,
                approval_receipt.actor,
                approval_receipt.note,
                approval_receipt.action,
                approval_receipt.from_status,
                approval_receipt.to_status,
            )
            receipt_matches = (
                all(isinstance(value, str) for value in receipt_text_values)
                and approval_receipt.card_id == card.card_id
                and approval_receipt.action == WorkflowAction.APPROVE.value
                and approval_receipt.from_status == "awaiting_engineer_review"
                and approval_receipt.to_status == "approved"
                and bool(approval_receipt.event_id.strip())
                and bool(approval_receipt.occurred_at.strip())
                and approval_receipt.actor.strip() == actor
                and approval_receipt.note.strip() == note
            )
            if not receipt_matches:
                raise FeishuApprovalError(
                    "approval_receipt 与风险卡或批准信息不匹配",
                    sync_result=sync_result,
                )
            matching_receipt_events = [
                event
                for event in events
                if isinstance(event, dict)
                and event.get("event_id") == approval_receipt.event_id
                and event.get("occurred_at") == approval_receipt.occurred_at
                and event.get("action") == approval_receipt.action
                and event.get("from_status") == approval_receipt.from_status
                and event.get("to_status") == approval_receipt.to_status
                and event.get("actor") == approval_receipt.actor
                and event.get("note") == approval_receipt.note
            ]
            if len(matching_receipt_events) != 1:
                raise FeishuApprovalError(
                    "approval_receipt 没有对应且全字段一致的真实工作流事件",
                    sync_result=sync_result,
                )
            return

        matching_events = [
            event
            for event in events
            if isinstance(event, dict)
            and event.get("action") == WorkflowAction.APPROVE.value
            and event.get("from_status") == "awaiting_engineer_review"
            and event.get("to_status") == "approved"
            and isinstance(event.get("event_id"), str)
            and bool(event["event_id"].strip())
            and isinstance(event.get("occurred_at"), str)
            and bool(event["occurred_at"].strip())
            and isinstance(event.get("actor"), str)
            and event["actor"].strip() == actor
            and isinstance(event.get("note"), str)
            and event["note"].strip() == note
        ]
        if not matching_events:
            raise FeishuApprovalError(
                "风险卡工作流没有与批准人和依据相符的 approve 事件",
                sync_result=sync_result,
            )

    @staticmethod
    def _remote_ids(records: list[dict[str, Any]]) -> list[str]:
        return [
            item["record_id"].strip()
            for item in records
            if isinstance(item, dict)
            and isinstance(item.get("record_id"), str)
            and item["record_id"].strip()
        ]

    def publish_after_approval(
        self,
        card: RiskCard,
        *,
        approved_by: str,
        approval_note: str,
        approval_receipt: ApprovalReceipt | None = None,
    ) -> dict[str, Any]:
        """Publish a risk and its tasks only after an attributable approval."""

        sync_result = self._new_sync_result(card)
        try:
            card.validate()
        except Exception as exc:
            sync_result.update(
                {
                    "failure_stage": "approval_validation",
                    "error": {"type": type(exc).__name__, "message": str(exc)},
                }
            )
            raise FeishuApprovalError(
                f"风险卡完整性校验失败，禁止实时发布：{exc}",
                sync_result=sync_result,
            ) from exc
        previous = self._publish_history.get(card.card_id)
        if previous is not None:
            sync_result.update(
                {
                    "failure_stage": "duplicate_guard",
                    "manual_reconciliation_required": True,
                    "prior_sync_status": previous.get("sync_status"),
                    "prior_remote_ids": previous.get("remote_ids"),
                }
            )
            raise FeishuApprovalError(
                "同一客户端已尝试发布该风险卡；禁止自动重放，请先人工对账",
                sync_result=sync_result,
            )
        self._validate_approval(
            card,
            approved_by=approved_by,
            approval_note=approval_note,
            approval_receipt=approval_receipt,
            sync_result=sync_result,
        )
        if not card.recommended_actions:
            raise FeishuApprovalError(
                "风险卡没有可创建的任务，禁止实时发布", sync_result=sync_result
            )

        # Record the attempt before the first create call.  A lost response has
        # an unknown delivery outcome and therefore must not be replayed blindly.
        self._last_auth_request_id = ""
        self._publish_history[card.card_id] = sync_result
        try:
            payloads = build_bitable_payloads(
                card,
                approved_by=approved_by.strip(),
                approval_note=approval_note.strip(),
            )
        except Exception as exc:
            sync_result.update(
                {
                    "sync_status": "failed",
                    "failure_stage": "payload_build",
                    "error": {"type": type(exc).__name__, "message": str(exc)},
                }
            )
            self._publish_history[card.card_id] = dict(sync_result)
            raise FeishuAPIError(
                f"飞书发布载荷构建失败：{exc}",
                failure_stage="payload_build",
                sync_result=dict(sync_result),
            ) from exc
        try:
            risk_records, risk_request_id = self._batch_create_records(
                self.config.risk_table_id,
                [payloads["risk_record"]],
                business_key="风险卡编号",
                failure_stage="risk_create",
            )
            if self._last_auth_request_id:
                sync_result["request_ids"]["authentication"] = self._last_auth_request_id
            sync_result["request_ids"]["risk_create"] = risk_request_id
            sync_result["risk_records"] = risk_records
            sync_result["remote_ids"]["risk"] = self._remote_ids(risk_records)
            sync_result["sync_status"] = "partial"

            task_records, task_request_id = self._batch_create_records(
                self.config.task_table_id,
                payloads["task_records"],
                business_key="任务编号",
                failure_stage="task_create",
            )
            sync_result["request_ids"]["task_create"] = task_request_id
            sync_result["task_records"] = task_records
            sync_result["remote_ids"]["tasks"] = self._remote_ids(task_records)
        except FeishuAPIError as exc:
            stage = exc.failure_stage or "unknown"
            if exc.request_id:
                sync_result["request_ids"][stage] = exc.request_id
            if self._last_auth_request_id:
                sync_result["request_ids"].setdefault(
                    "authentication", self._last_auth_request_id
                )
            if exc.confirmed_records:
                target = "risk" if stage == "risk_create" else "tasks"
                record_key = "risk_records" if target == "risk" else "task_records"
                sync_result[record_key] = exc.confirmed_records
                sync_result["remote_ids"][target] = self._remote_ids(
                    exc.confirmed_records
                )
            has_confirmed_remote = any(sync_result["remote_ids"].values())
            sync_result.update(
                {
                    "sync_status": "partial" if has_confirmed_remote else "failed",
                    "failure_stage": stage,
                    "manual_reconciliation_required": True,
                    "error": {"type": type(exc).__name__, "message": str(exc)},
                }
            )
            self._publish_history[card.card_id] = dict(sync_result)
            raise FeishuAPIError(
                f"飞书实时同步在 {stage} 阶段失败：{exc}",
                request_id=exc.request_id,
                failure_stage=stage,
                sync_result=dict(sync_result),
                confirmed_records=exc.confirmed_records,
            ) from exc

        sync_result.update(
            {
                "sync_status": "succeeded",
                "failure_stage": None,
                "manual_reconciliation_required": False,
            }
        )
        self._publish_history[card.card_id] = dict(sync_result)
        return sync_result


def build_bitable_payloads(
    card: RiskCard,
    *,
    approved_by: str = "",
    approval_note: str = "",
    preview_sync_status: str | None = None,
) -> dict[str, Any]:
    """Build separate risk/task payloads for preview or live transmission."""

    if preview_sync_status not in {None, "not_attempted", "partial", "succeeded", "failed"}:
        raise ValueError(f"未知飞书同步状态：{preview_sync_status}")
    if not approved_by:
        risk_status, task_status = "待工程师确认", "待确认"
    elif preview_sync_status is None:
        # Payload submitted to Feishu: once this create response is confirmed,
        # the newly created task is ready for its human owner.
        risk_status, task_status = "已批准待执行", "待执行"
    elif preview_sync_status == "succeeded":
        risk_status, task_status = "已同步待执行", "待执行"
    elif preview_sync_status == "partial":
        risk_status, task_status = "部分同步，待人工对账", "创建不完整，禁止执行"
    elif preview_sync_status == "failed":
        risk_status, task_status = "同步失败，待人工对账", "创建未确认，禁止执行"
    else:
        risk_status, task_status = "已批准，尚未同步", "尚未创建"
    risk_record = {
        "fields": {
            "风险卡编号": card.card_id,
            "工位": card.station_id,
            "工具": card.tool_id,
            "紧固点": card.fastening_point,
            "风险等级": card.risk_level,
            "风险评分": card.risk_score,
            "状态": risk_status,
            "证据数量": len(card.evidence),
            "窗口开始": card.affected_scope["window_start"],
            "窗口结束": card.affected_scope["window_end"],
            "批准人": approved_by,
            "批准依据": approval_note,
        }
    }
    task_records = [
        {
            "fields": {
                "风险卡编号": card.card_id,
                "任务编号": action.action_id,
                "任务": action.title,
                "责任角色": action.owner_role,
                "时限（分钟）": action.due_minutes,
                "需人工审批": action.approval_required,
                "验收依据": action.acceptance_criteria,
                "生成依据": action.why,
                "关联证据": "、".join(action.evidence_ids),
                "候选原因": "；".join(action.candidate_causes),
                "状态": task_status,
                "批准人": approved_by,
            }
        }
        for action in card.recommended_actions
    ]
    return {"risk_record": risk_record, "task_records": task_records}


def build_bitable_records(card: RiskCard) -> list[dict[str, Any]]:
    """Backward-compatible offline preview; this function never transmits."""

    payloads = build_bitable_payloads(card)
    return [payloads["risk_record"], *payloads["task_records"]]
