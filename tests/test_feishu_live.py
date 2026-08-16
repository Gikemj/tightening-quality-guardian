import unittest
from pathlib import Path

from torque_guard.agent import DigitalEmployee
from torque_guard.integrations.feishu import (
    ApprovalReceipt,
    FeishuAPIError,
    FeishuApprovalError,
    FeishuBitableClient,
    FeishuConfig,
    FeishuConfigurationError,
    FeishuTransportError,
    build_bitable_payloads,
)
from torque_guard.workflow import WorkflowAction


ROOT = Path(__file__).resolve().parents[1]


class FakeTransport:
    def __init__(self, *, responder=None, token="test-tenant-token"):
        self.calls = []
        self.responder = responder
        self.token = token

    def __call__(self, method, url, payload, headers, timeout):
        call = {
            "method": method,
            "url": url,
            "payload": payload,
            "headers": headers,
            "timeout": timeout,
        }
        self.calls.append(call)
        if self.responder is not None:
            custom = self.responder(call)
            if custom is not None:
                if isinstance(custom, Exception):
                    raise custom
                return custom
        if url.endswith("/auth/v3/tenant_access_token/internal"):
            return {"code": 0, "tenant_access_token": self.token}
        records = payload["records"]
        table = "task" if "/tables/task/" in url else "risk"
        return {
            "code": 0,
            "data": {
                "records": [
                    {"record_id": f"rec-{table}-{index + 1}", **record}
                    for index, record in enumerate(records)
                ]
            },
        }


class FeishuLiveClientTest(unittest.TestCase):
    @staticmethod
    def _run_card():
        employee = DigitalEmployee(ROOT / "knowledge")
        card = employee.run(ROOT / "data" / "tightening_events_demo.csv", "P03")
        return employee, card

    def _approved_card(self, *, actor="quality.owner", note="已核对 E-SPC-01 与 E-EQP-02"):
        employee, card = self._run_card()
        event = employee.last_workflow.transition(
            WorkflowAction.APPROVE, actor=actor, note=note
        )
        return card, actor, note, ApprovalReceipt.from_transition(card.card_id, event)

    @staticmethod
    def _client(transport, *, max_attempts=3, config=None):
        return FeishuBitableClient(
            config or FeishuConfig("id", "secret", "app", "risk", "task"),
            transport=transport,
            max_attempts=max_attempts,
        )

    def test_missing_environment_configuration_fails_closed(self):
        with self.assertRaises(FeishuConfigurationError):
            FeishuConfig.from_env({})

    def test_live_configuration_rejects_insecure_endpoint_and_path_injection(self):
        with self.assertRaisesRegex(FeishuConfigurationError, "HTTPS"):
            FeishuConfig(
                "id", "secret", "app", "risk", "task", api_base="http://example.test"
            ).validate()
        with self.assertRaisesRegex(FeishuConfigurationError, "非法路径字符"):
            FeishuConfig("id", "secret", "app", "risk/other", "task").validate()

    def test_production_rejects_custom_api_base_and_tests_require_injected_transport(self):
        with self.assertRaisesRegex(FeishuConfigurationError, "只允许"):
            FeishuConfig(
                "id",
                "secret",
                "app",
                "risk",
                "task",
                api_base="https://example.test/open-apis",
            ).validate()

        test_config = FeishuConfig(
            "id",
            "secret",
            "app",
            "risk",
            "task",
            api_base="https://example.test/open-apis",
            test_mode=True,
        )
        with self.assertRaisesRegex(FeishuConfigurationError, "测试 transport"):
            FeishuBitableClient(test_config)
        self._client(FakeTransport(), config=test_config)

    def test_client_rejects_invalid_retry_and_timeout_settings(self):
        config = FeishuConfig("id", "secret", "app", "risk", "task")
        with self.assertRaisesRegex(ValueError, "timeout"):
            FeishuBitableClient(config, transport=FakeTransport(), timeout=0)
        with self.assertRaisesRegex(ValueError, "max_attempts"):
            FeishuBitableClient(config, transport=FakeTransport(), max_attempts=0)

    def test_preview_keeps_approval_fields_but_does_not_send(self):
        _employee, card = self._run_card()
        transport = FakeTransport()
        payloads = build_bitable_payloads(card)
        self.assertEqual(payloads["risk_record"]["fields"]["状态"], "待工程师确认")
        self.assertEqual(payloads["risk_record"]["fields"]["批准人"], "")
        self.assertTrue(
            all(item["fields"]["需人工审批"] for item in payloads["task_records"])
        )
        self.assertEqual(transport.calls, [])

    def test_live_publish_rejects_strings_without_workflow_approval(self):
        _employee, card = self._run_card()
        transport = FakeTransport()
        client = self._client(transport)

        with self.assertRaises(FeishuApprovalError) as caught:
            client.publish_after_approval(
                card,
                approved_by="quality.owner",
                approval_note="仅有字符串，不是流程事件",
            )

        self.assertEqual(caught.exception.sync_result["sync_status"], "not_attempted")
        self.assertEqual(transport.calls, [])

    def test_live_publish_rejects_mismatched_strong_receipt(self):
        card, actor, note, receipt = self._approved_card()
        wrong_receipt = ApprovalReceipt(
            card_id=receipt.card_id,
            event_id=receipt.event_id,
            occurred_at=receipt.occurred_at,
            actor="another.owner",
            note=receipt.note,
        )
        transport = FakeTransport()

        with self.assertRaises(FeishuApprovalError):
            self._client(transport).publish_after_approval(
                card,
                approved_by=actor,
                approval_note=note,
                approval_receipt=wrong_receipt,
            )

        self.assertEqual(transport.calls, [])

    def test_receipt_cannot_replace_a_missing_workflow_event(self):
        card, actor, note, receipt = self._approved_card()
        card.workflow["events"] = []
        transport = FakeTransport()

        with self.assertRaises(FeishuApprovalError) as caught:
            self._client(transport).publish_after_approval(
                card,
                approved_by=actor,
                approval_note=note,
                approval_receipt=receipt,
            )

        self.assertEqual(caught.exception.sync_result["sync_status"], "not_attempted")
        self.assertEqual(
            caught.exception.sync_result["failure_stage"], "approval_validation"
        )
        self.assertEqual(transport.calls, [])

    def test_tampered_card_is_rejected_before_any_network_call(self):
        card, actor, note, receipt = self._approved_card()
        card.risk_score += 1
        transport = FakeTransport()

        with self.assertRaisesRegex(FeishuApprovalError, "完整性校验失败") as caught:
            self._client(transport).publish_after_approval(
                card,
                approved_by=actor,
                approval_note=note,
                approval_receipt=receipt,
            )

        self.assertEqual(caught.exception.sync_result["sync_status"], "not_attempted")
        self.assertEqual(transport.calls, [])

    def test_live_publish_authenticates_and_writes_separate_tables(self):
        card, actor, note, receipt = self._approved_card()
        transport = FakeTransport()
        client = self._client(transport)

        result = client.publish_after_approval(
            card,
            approved_by=actor,
            approval_note=note,
            approval_receipt=receipt,
        )

        self.assertEqual(result["mode"], "live")
        self.assertEqual(result["sync_status"], "succeeded")
        self.assertEqual(result["failure_stage"], None)
        self.assertEqual(result["remote_ids"]["risk"], ["rec-risk-1"])
        self.assertEqual(
            len(result["remote_ids"]["tasks"]), len(card.recommended_actions)
        )
        self.assertEqual(len(transport.calls), 3)
        self.assertIn("/auth/v3/tenant_access_token/internal", transport.calls[0]["url"])
        self.assertIn("/tables/risk/records/batch_create", transport.calls[1]["url"])
        self.assertIn("/tables/task/records/batch_create", transport.calls[2]["url"])
        self.assertEqual(
            transport.calls[1]["headers"]["Authorization"], "Bearer test-tenant-token"
        )
        self.assertTrue(transport.calls[1]["headers"]["X-TorqueGuard-Request-ID"])
        self.assertEqual(
            result["request_ids"]["risk_create"],
            transport.calls[1]["headers"]["X-TorqueGuard-Request-ID"],
        )

    def test_token_must_be_a_non_empty_string(self):
        card, actor, note, receipt = self._approved_card()
        for invalid_token in (None, 123, "   "):
            with self.subTest(token=invalid_token):
                transport = FakeTransport(token=invalid_token)
                with self.assertRaises(FeishuAPIError) as caught:
                    self._client(transport, max_attempts=1).publish_after_approval(
                        card,
                        approved_by=actor,
                        approval_note=note,
                        approval_receipt=receipt,
                    )
                self.assertEqual(caught.exception.failure_stage, "authentication")
                self.assertEqual(caught.exception.sync_result["sync_status"], "failed")

    def test_batch_response_rejects_empty_short_and_malformed_records(self):
        cases = {
            "missing": {"code": 0, "data": {}},
            "empty": {"code": 0, "data": {"records": []}},
            "blank-id": {"code": 0, "data": {"records": [{"record_id": " "}]}},
            "not-object": {"code": 0, "data": {"records": ["rec-1"]}},
        }
        for label, response in cases.items():
            with self.subTest(case=label):
                def responder(call, response=response):
                    if "/tables/risk/" in call["url"]:
                        return response
                    return None

                card, actor, note, receipt = self._approved_card()
                with self.assertRaises(FeishuAPIError) as caught:
                    self._client(FakeTransport(responder=responder)).publish_after_approval(
                        card,
                        approved_by=actor,
                        approval_note=note,
                        approval_receipt=receipt,
                    )
                result = caught.exception.sync_result
                self.assertEqual(result["failure_stage"], "risk_create")
                self.assertIn(result["sync_status"], {"failed", "partial"})
                self.assertTrue(result["request_ids"]["risk_create"])

    def test_batch_response_rejects_business_key_mismatch(self):
        def responder(call):
            if "/tables/risk/" not in call["url"]:
                return None
            return {
                "code": 0,
                "data": {
                    "records": [
                        {
                            "record_id": "rec-risk-wrong",
                            "fields": {"风险卡编号": "TG-WRONG"},
                        }
                    ]
                },
            }

        card, actor, note, receipt = self._approved_card()
        with self.assertRaisesRegex(FeishuAPIError, "业务唯一键") as caught:
            self._client(FakeTransport(responder=responder)).publish_after_approval(
                card,
                approved_by=actor,
                approval_note=note,
                approval_receipt=receipt,
            )

        self.assertEqual(caught.exception.sync_result["sync_status"], "partial")
        self.assertEqual(
            caught.exception.sync_result["remote_ids"]["risk"], ["rec-risk-wrong"]
        )

    def test_task_short_response_preserves_partial_remote_ids(self):
        def responder(call):
            if "/tables/task/" not in call["url"]:
                return None
            first = call["payload"]["records"][0]
            return {
                "code": 0,
                "data": {"records": [{"record_id": "rec-task-1", **first}]},
            }

        card, actor, note, receipt = self._approved_card()
        with self.assertRaises(FeishuAPIError) as caught:
            self._client(FakeTransport(responder=responder)).publish_after_approval(
                card,
                approved_by=actor,
                approval_note=note,
                approval_receipt=receipt,
            )

        result = caught.exception.sync_result
        self.assertEqual(result["sync_status"], "partial")
        self.assertEqual(result["failure_stage"], "task_create")
        self.assertEqual(result["remote_ids"]["risk"], ["rec-risk-1"])
        self.assertEqual(result["remote_ids"]["tasks"], ["rec-task-1"])

    def test_business_create_transport_failure_is_never_replayed(self):
        def responder(call):
            if "/tables/risk/" in call["url"]:
                return FeishuTransportError(
                    "response lost; delivery unknown", definitely_not_delivered=False
                )
            return None

        card, actor, note, receipt = self._approved_card()
        transport = FakeTransport(responder=responder)
        with self.assertRaises(FeishuAPIError) as caught:
            self._client(transport, max_attempts=3).publish_after_approval(
                card,
                approved_by=actor,
                approval_note=note,
                approval_receipt=receipt,
            )

        risk_calls = [call for call in transport.calls if "/tables/risk/" in call["url"]]
        self.assertEqual(len(risk_calls), 1)
        self.assertEqual(caught.exception.sync_result["sync_status"], "failed")
        self.assertFalse(caught.exception.sync_result["automatic_retry_safe"])
        self.assertTrue(caught.exception.request_id)

    def test_authentication_transport_failure_can_retry_safely(self):
        auth_attempts = 0

        def responder(call):
            nonlocal auth_attempts
            if call["url"].endswith("/auth/v3/tenant_access_token/internal"):
                auth_attempts += 1
                if auth_attempts == 1:
                    return FeishuTransportError(
                        "connection refused", definitely_not_delivered=True
                    )
            return None

        card, actor, note, receipt = self._approved_card()
        result = self._client(FakeTransport(responder=responder), max_attempts=2).publish_after_approval(
            card,
            approved_by=actor,
            approval_note=note,
            approval_receipt=receipt,
        )
        self.assertEqual(result["sync_status"], "succeeded")
        self.assertEqual(auth_attempts, 2)

    def test_second_publish_is_blocked_without_another_network_call(self):
        card, actor, note, receipt = self._approved_card()
        transport = FakeTransport()
        client = self._client(transport)
        client.publish_after_approval(
            card,
            approved_by=actor,
            approval_note=note,
            approval_receipt=receipt,
        )
        call_count = len(transport.calls)

        with self.assertRaises(FeishuApprovalError) as caught:
            client.publish_after_approval(
                card,
                approved_by=actor,
                approval_note=note,
                approval_receipt=receipt,
            )

        self.assertEqual(len(transport.calls), call_count)
        self.assertEqual(caught.exception.sync_result["sync_status"], "not_attempted")
        self.assertEqual(caught.exception.sync_result["failure_stage"], "duplicate_guard")
        self.assertTrue(caught.exception.sync_result["manual_reconciliation_required"])


if __name__ == "__main__":
    unittest.main()
