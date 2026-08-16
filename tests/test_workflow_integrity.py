import copy
import unittest
from pathlib import Path

from torque_guard.agent import DigitalEmployee
from torque_guard.workflow import (
    NOTE_REQUIRED_ACTIONS,
    RiskCaseWorkflow,
    WorkflowAction,
)


ROOT = Path(__file__).resolve().parents[1]


class SerializedWorkflowIntegrityTest(unittest.TestCase):
    @staticmethod
    def _new_card():
        employee = DigitalEmployee(ROOT / "knowledge")
        card = employee.run(ROOT / "data" / "tightening_events_demo.csv", "P03")
        return employee, card

    @classmethod
    def _closed_card(cls):
        employee, card = cls._new_card()
        workflow = employee.last_workflow
        workflow.transition(
            WorkflowAction.APPROVE,
            actor="quality.engineer",
            note="证据链完整，同意现场核验",
        )
        workflow.transition(
            WorkflowAction.CREATE_TASKS,
            actor="quality.engineer",
            task_ids=[item.action_id for item in card.recommended_actions],
        )
        workflow.transition(
            WorkflowAction.START_VERIFICATION,
            actor="equipment.engineer",
        )
        workflow.transition(
            WorkflowAction.PASS_VERIFICATION,
            actor="equipment.engineer",
            evidence_ids=["E-SPC-01"],
        )
        workflow.transition(
            WorkflowAction.CLOSE,
            actor="quality.owner",
            note="现场复核通过并完成案例沉淀",
        )
        card.validate()
        return card

    def test_valid_full_workflow_uses_one_shared_transition_contract(self):
        card = self._closed_card()
        self.assertEqual(card.status, "closed")
        self.assertEqual(card.workflow["allowed_actions"], ["reopen"])
        self.assertEqual(
            {item.value for item in NOTE_REQUIRED_ACTIONS},
            {"approve", "reject", "resubmit", "fail_verification", "close", "reopen"},
        )

    def test_serialized_event_identity_actor_time_and_note_are_fail_closed(self):
        mutations = (
            ("bad event id", lambda card: card.workflow["events"][0].__setitem__("event_id", "WF-bad"), "event_id"),
            ("duplicate event id", lambda card: card.workflow["events"][1].__setitem__("event_id", card.workflow["events"][0]["event_id"]), "event_id 必须唯一"),
            ("empty actor", lambda card: card.workflow["events"][0].__setitem__("actor", ""), "actor"),
            ("date only", lambda card: card.workflow["events"][0].__setitem__("occurred_at", "2026-08-06"), "UTC RFC3339"),
            ("time reversal", lambda card: card.workflow["events"][1].__setitem__("occurred_at", "2000-01-01T00:00:00Z"), "非递减"),
            ("missing approval note", lambda card: card.workflow["events"][0].__setitem__("note", ""), "必须填写依据"),
            ("unknown event field", lambda card: card.workflow["events"][0].__setitem__("forged", True), "字段不完整"),
        )
        for label, mutate, message in mutations:
            with self.subTest(label=label):
                card = copy.deepcopy(self._closed_card())
                mutate(card)
                with self.assertRaisesRegex(ValueError, message):
                    card.to_dict()

    def test_action_specific_task_and_evidence_fields_cannot_be_forged(self):
        mutations = (
            ("empty create tasks", lambda events: events[1].__setitem__("task_ids", []), "至少一个 task_id"),
            ("duplicate create tasks", lambda events: events[1].__setitem__("task_ids", [events[1]["task_ids"][0]] * 2), "不得重复"),
            ("partial create tasks", lambda events: events[1].__setitem__("task_ids", events[1]["task_ids"][:-1]), "完整对应"),
            ("tasks on approve", lambda events: events[0].__setitem__("task_ids", ["A-01"]), "仅 create_tasks"),
            ("missing pass evidence", lambda events: events[3].__setitem__("evidence_ids", []), "pass_verification"),
            ("unknown pass evidence", lambda events: events[3].__setitem__("evidence_ids", ["E-FORGED"]), "未知 evidence_id"),
            ("wrong allowed actions", lambda events: None, "allowed_actions"),
        )
        for label, mutate, message in mutations:
            with self.subTest(label=label):
                card = copy.deepcopy(self._closed_card())
                mutate(card.workflow["events"])
                if label == "wrong allowed actions":
                    card.workflow["allowed_actions"] = []
                with self.assertRaisesRegex(ValueError, message):
                    card.to_dict()

    def test_runtime_transition_rejects_duplicate_and_misplaced_ids(self):
        workflow = RiskCaseWorkflow("TG-WORKFLOW")
        workflow.transition(WorkflowAction.APPROVE, actor="qe", note="批准")
        with self.assertRaisesRegex(ValueError, "task_id 不得重复"):
            workflow.transition(
                WorkflowAction.CREATE_TASKS,
                actor="qe",
                task_ids=["A-01", "A-01"],
            )

        fresh = RiskCaseWorkflow("TG-WORKFLOW")
        with self.assertRaisesRegex(ValueError, "仅 create_tasks"):
            fresh.transition(
                WorkflowAction.APPROVE,
                actor="qe",
                note="批准",
                task_ids=["A-01"],
            )


class ExternalSyncIntegrityTest(unittest.TestCase):
    @staticmethod
    def _new_card():
        employee = DigitalEmployee(ROOT / "knowledge")
        card = employee.run(ROOT / "data" / "tightening_events_demo.csv", "P03")
        return employee, card

    @classmethod
    def _approved_card(cls):
        employee, card = cls._new_card()
        employee.last_workflow.transition(
            WorkflowAction.APPROVE,
            actor="quality.owner",
            note="批准现场核验",
        )
        return employee, card

    @classmethod
    def _succeeded_card(cls):
        employee, card = cls._approved_card()
        employee.last_workflow.transition(
            WorkflowAction.CREATE_TASKS,
            actor="quality.owner",
            task_ids=[item.action_id for item in card.recommended_actions],
        )
        card.workflow["external_sync"] = {
            "schema_version": "1.0",
            "mode": "live",
            "card_id": card.card_id,
            "sync_status": "succeeded",
            "failure_stage": None,
            "request_ids": {
                "risk_create": "req-risk",
                "task_create": "req-task",
            },
            "remote_ids": {
                "risk": ["rec-risk"],
                "tasks": [
                    f"rec-task-{index + 1}"
                    for index in range(len(card.recommended_actions))
                ],
            },
            "reconciliation_required": False,
            "automatic_retry_safe": False,
            "workflow_status": "tasks_created",
            "workflow_committed": True,
            "external_write_status": "succeeded",
        }
        card.validate()
        return card

    @classmethod
    def _partial_card(cls):
        _employee, card = cls._approved_card()
        card.workflow["external_sync"] = {
            "schema_version": "1.0",
            "mode": "live",
            "card_id": card.card_id,
            "sync_status": "partial",
            "failure_stage": "task_create",
            "request_ids": {"risk_create": "req-risk", "task_create": "req-task"},
            "remote_ids": {"risk": ["rec-risk"], "tasks": []},
            "reconciliation_required": True,
            "automatic_retry_safe": False,
            "error": {"type": "FeishuAPIError", "message": "task create failed"},
        }
        card.validate()
        return card

    def test_valid_succeeded_partial_and_preview_without_sync_are_supported(self):
        self._succeeded_card().validate()
        self._partial_card().validate()
        _employee, preview_card = self._new_card()
        self.assertNotIn("external_sync", preview_card.workflow)
        preview_card.validate()

    def test_succeeded_sync_receipt_survives_later_workflow_transitions(self):
        employee, card = self._approved_card()
        workflow = employee.last_workflow
        workflow.transition(
            WorkflowAction.CREATE_TASKS,
            actor="quality.owner",
            task_ids=[item.action_id for item in card.recommended_actions],
        )
        card.workflow["external_sync"] = copy.deepcopy(
            self._succeeded_card().workflow["external_sync"]
        )
        workflow.transition(
            WorkflowAction.START_VERIFICATION,
            actor="equipment.engineer",
        )

        self.assertEqual(card.status, "verification_in_progress")
        self.assertEqual(
            card.workflow["external_sync"]["sync_status"], "succeeded"
        )
        card.validate()

    def test_succeeded_sync_tampering_is_rejected(self):
        mutations = (
            ("invalid status", lambda sync: sync.__setitem__("sync_status", "done"), "sync_status"),
            ("failure on success", lambda sync: sync.__setitem__("failure_stage", "task_create"), "succeeded external_sync"),
            ("missing task request", lambda sync: sync["request_ids"].pop("task_create"), "succeeded external_sync"),
            ("short remote tasks", lambda sync: sync["remote_ids"].__setitem__("tasks", sync["remote_ids"]["tasks"][:-1]), "succeeded external_sync"),
            ("reconciliation on success", lambda sync: sync.__setitem__("reconciliation_required", True), "succeeded external_sync"),
            ("uncommitted success", lambda sync: sync.__setitem__("workflow_committed", False), "succeeded external_sync"),
            ("foreign card", lambda sync: sync.__setitem__("card_id", "TG-00000000000000000000000000000000"), "card_id"),
        )
        for label, mutate, message in mutations:
            with self.subTest(label=label):
                card = copy.deepcopy(self._succeeded_card())
                mutate(card.workflow["external_sync"])
                with self.assertRaisesRegex(ValueError, message):
                    card.to_dict()

    def test_partial_or_failed_sync_cannot_claim_tasks_created(self):
        card = copy.deepcopy(self._succeeded_card())
        card.workflow["external_sync"].update(
            {
                "sync_status": "partial",
                "failure_stage": "task_create",
                "reconciliation_required": True,
                "workflow_committed": False,
            }
        )
        with self.assertRaisesRegex(ValueError, "不得伪称 tasks_created"):
            card.to_dict()

        partial = copy.deepcopy(self._partial_card())
        partial.workflow["external_sync"]["reconciliation_required"] = False
        with self.assertRaisesRegex(ValueError, "必须要求人工对账"):
            partial.to_dict()

        failed = copy.deepcopy(self._partial_card())
        failed.workflow["external_sync"]["sync_status"] = "failed"
        with self.assertRaisesRegex(ValueError, "不得包含已确认远端 ID"):
            failed.to_dict()


if __name__ == "__main__":
    unittest.main()
