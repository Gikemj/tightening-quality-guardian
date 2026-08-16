import unittest

from torque_guard.workflow import (
    CaseStatus,
    InvalidWorkflowTransition,
    RiskCaseWorkflow,
    WorkflowAction,
)


class RiskCaseWorkflowTest(unittest.TestCase):
    def test_happy_path_requires_approval_tasks_verification_and_closure(self):
        workflow = RiskCaseWorkflow("TG-WORKFLOW")

        workflow.transition(
            WorkflowAction.APPROVE,
            actor="quality.engineer",
            note="证据链完整，同意现场核验",
        )
        workflow.transition(
            WorkflowAction.CREATE_TASKS,
            actor="quality.engineer",
            task_ids=["A-01", "A-02"],
        )
        workflow.transition(
            WorkflowAction.START_VERIFICATION,
            actor="equipment.engineer",
        )
        workflow.transition(
            WorkflowAction.PASS_VERIFICATION,
            actor="equipment.engineer",
            evidence_ids=["EV-FIELD-001"],
        )
        workflow.transition(
            WorkflowAction.CLOSE,
            actor="quality.owner",
            note="复核通过并完成案例沉淀",
        )

        self.assertEqual(workflow.status, CaseStatus.CLOSED)
        self.assertEqual(len(workflow.events), 5)
        self.assertEqual(workflow.events[0].from_status, "awaiting_engineer_review")
        self.assertEqual(workflow.events[-1].to_status, "closed")
        self.assertIn("reopen", workflow.allowed_actions)

    def test_illegal_transition_cannot_bypass_approval(self):
        workflow = RiskCaseWorkflow("TG-WORKFLOW")

        with self.assertRaises(InvalidWorkflowTransition):
            workflow.transition(
                WorkflowAction.CREATE_TASKS,
                actor="system",
                task_ids=["A-01"],
            )

        self.assertEqual(workflow.status, CaseStatus.AWAITING_ENGINEER_REVIEW)
        self.assertEqual(workflow.events, ())

    def test_verification_needs_evidence_and_closure_needs_note(self):
        workflow = RiskCaseWorkflow("TG-WORKFLOW")
        workflow.transition(WorkflowAction.APPROVE, actor="qe", note="批准")
        workflow.transition(WorkflowAction.CREATE_TASKS, actor="qe", task_ids=["A-01"])
        workflow.transition(WorkflowAction.START_VERIFICATION, actor="ee")

        with self.assertRaisesRegex(ValueError, "evidence_id"):
            workflow.transition(WorkflowAction.PASS_VERIFICATION, actor="ee")

        workflow.transition(
            WorkflowAction.PASS_VERIFICATION,
            actor="ee",
            evidence_ids=["EV-001"],
        )
        with self.assertRaisesRegex(ValueError, "依据或说明"):
            workflow.transition(WorkflowAction.CLOSE, actor="owner")


if __name__ == "__main__":
    unittest.main()
