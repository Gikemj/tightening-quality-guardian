import unittest
from unittest.mock import patch
from pathlib import Path

from torque_guard.agent import DigitalEmployee
from torque_guard.cli import (
    PRIVATE_LIVE_FEISHU_OUTPUT,
    PRIVATE_LIVE_RISK_OUTPUT,
    PUBLIC_FEISHU_PREVIEW_OUTPUT,
    PUBLIC_RISK_OUTPUT,
    _build_preview_records,
    _resolve_output_paths,
    main,
    publish_live_after_workflow_approval,
)
from torque_guard.integrations.feishu import (
    FeishuAPIError,
    FeishuBitableClient,
    FeishuConfig,
)


ROOT = Path(__file__).resolve().parents[1]


class _WorkflowTransport:
    def __init__(self, *, fail_tasks: bool = False):
        self.fail_tasks = fail_tasks

    def __call__(self, method, url, payload, headers, timeout):
        if url.endswith("/auth/v3/tenant_access_token/internal"):
            return {"code": 0, "tenant_access_token": "test-token"}
        if self.fail_tasks and "/tables/task/" in url:
            return {"code": 1254290, "msg": "test task-table failure"}
        return {
            "code": 0,
            "data": {
                "records": [
                    {"record_id": f"rec-{index + 1}", **record}
                    for index, record in enumerate(payload["records"])
                ]
            },
        }


class CliLiveWorkflowTest(unittest.TestCase):
    def _run_employee(self):
        employee = DigitalEmployee(ROOT / "knowledge")
        card = employee.run(ROOT / "data" / "tightening_events_demo.csv", "P03")
        return employee, card

    @staticmethod
    def _client(transport):
        return FeishuBitableClient(
            FeishuConfig("id", "secret", "app", "risk", "task"),
            transport=transport,
            max_attempts=1,
        )

    def test_success_records_approval_before_marking_tasks_created(self):
        employee, card = self._run_employee()

        result = publish_live_after_workflow_approval(
            employee,
            card,
            self._client(_WorkflowTransport()),
            approved_by="quality.owner",
            approval_note="已核对 E-SPC-01 与 E-EQP-02",
        )

        self.assertEqual(result["mode"], "live")
        self.assertEqual(result["sync_status"], "succeeded")
        self.assertTrue(result["workflow_committed"])
        self.assertEqual(card.status, "tasks_created")
        self.assertEqual(
            [event["action"] for event in card.workflow["events"]],
            ["approve", "create_tasks"],
        )
        self.assertEqual(card.workflow["events"][0]["actor"], "quality.owner")
        self.assertEqual(
            card.workflow["external_sync"]["sync_status"], "succeeded"
        )
        self.assertFalse(
            card.workflow["external_sync"]["reconciliation_required"]
        )
        self.assertNotIn(
            "manual_reconciliation_required", card.workflow["external_sync"]
        )
        card.validate()

    def test_partial_external_failure_does_not_claim_tasks_were_created(self):
        employee, card = self._run_employee()

        with self.assertRaises(FeishuAPIError) as caught:
            publish_live_after_workflow_approval(
                employee,
                card,
                self._client(_WorkflowTransport(fail_tasks=True)),
                approved_by="quality.owner",
                approval_note="已授权现场核验",
            )

        self.assertEqual(card.status, "approved")
        self.assertEqual(
            [event["action"] for event in card.workflow["events"]],
            ["approve"],
        )
        sync = caught.exception.sync_result
        self.assertEqual(sync["sync_status"], "partial")
        self.assertEqual(sync["failure_stage"], "task_create")
        self.assertEqual(len(sync["remote_ids"]["risk"]), 1)
        self.assertEqual(card.workflow["external_sync"]["sync_status"], "partial")
        self.assertTrue(
            card.workflow["external_sync"]["reconciliation_required"]
        )
        card.validate()

        preview = _build_preview_records(
            card, feishu_mode="live", live_result=sync
        )
        self.assertEqual(
            preview[0]["fields"]["状态"], "部分同步，待人工对账"
        )
        self.assertTrue(
            all(
                item["fields"]["状态"] == "创建不完整，禁止执行"
                for item in preview[1:]
            )
        )

    def test_cli_refuses_partial_result_even_if_client_does_not_raise(self):
        employee, card = self._run_employee()

        class PartialClient:
            @staticmethod
            def publish_after_approval(*args, **kwargs):
                return {
                    "mode": "live",
                    "card_id": card.card_id,
                    "sync_status": "partial",
                    "failure_stage": "task_create",
                    "request_ids": {"risk_create": "req-risk"},
                    "remote_ids": {"risk": ["rec-risk"], "tasks": []},
                    "manual_reconciliation_required": True,
                }

        with self.assertRaises(FeishuAPIError) as caught:
            publish_live_after_workflow_approval(
                employee,
                card,
                PartialClient(),
                approved_by="quality.owner",
                approval_note="已授权现场核验",
            )

        self.assertEqual(card.status, "approved")
        self.assertEqual(caught.exception.sync_result["sync_status"], "partial")
        self.assertNotIn("create_tasks", [event["action"] for event in card.workflow["events"]])

    def test_default_preview_path_never_constructs_live_client(self):
        employee, card = self._run_employee()

        class LocalEmployee:
            last_workflow = employee.last_workflow

            @staticmethod
            def run(*args, **kwargs):
                return card

        with patch("torque_guard.cli.DigitalEmployee", return_value=LocalEmployee()), patch(
            "torque_guard.cli.FeishuBitableClient",
            side_effect=AssertionError("preview 不得创建网络客户端"),
        ) as client_constructor, patch(
            "torque_guard.cli._persist_outputs",
            return_value=(Path("risk.json"), Path("preview.json")),
        ), patch(
            "sys.argv", ["torque-guard"]
        ):
            main()

        client_constructor.assert_not_called()

    def test_live_defaults_are_private_but_preview_defaults_stay_public(self):
        self.assertEqual(
            _resolve_output_paths(
                "preview", output_path=None, feishu_preview_path=None
            ),
            (PUBLIC_RISK_OUTPUT, PUBLIC_FEISHU_PREVIEW_OUTPUT),
        )
        self.assertEqual(
            _resolve_output_paths(
                "live", output_path=None, feishu_preview_path=None
            ),
            (PRIVATE_LIVE_RISK_OUTPUT, PRIVATE_LIVE_FEISHU_OUTPUT),
        )
        self.assertTrue(PRIVATE_LIVE_RISK_OUTPUT.startswith(".local/live/"))
        self.assertTrue(PRIVATE_LIVE_FEISHU_OUTPUT.startswith(".local/live/"))

    def test_explicit_output_paths_are_not_silently_rewritten(self):
        self.assertEqual(
            _resolve_output_paths(
                "live",
                output_path="D:/private/risk.json",
                feishu_preview_path="D:/private/feishu.json",
            ),
            ("D:/private/risk.json", "D:/private/feishu.json"),
        )

    def test_persistence_failure_does_not_mask_original_live_error(self):
        employee, card = self._run_employee()

        class LocalEmployee:
            last_workflow = employee.last_workflow

            @staticmethod
            def run(*args, **kwargs):
                return card

        original = FeishuAPIError(
            "original live failure",
            failure_stage="configuration",
            sync_result={
                "mode": "live",
                "card_id": card.card_id,
                "sync_status": "not_attempted",
                "failure_stage": "configuration",
                "request_ids": {},
                "remote_ids": {"risk": [], "tasks": []},
            },
        )
        with patch("torque_guard.cli.DigitalEmployee", return_value=LocalEmployee()), patch(
            "torque_guard.cli.FeishuConfig.from_env", side_effect=original
        ), patch(
            "torque_guard.cli._persist_outputs", side_effect=OSError("disk full")
        ), patch(
            "sys.argv", ["torque-guard", "--feishu-mode", "live"]
        ):
            with self.assertRaises(FeishuAPIError) as caught:
                main()

        self.assertIs(caught.exception, original)
        self.assertIsInstance(caught.exception.persistence_error, OSError)


if __name__ == "__main__":
    unittest.main()
