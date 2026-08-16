import json
import unittest
from pathlib import Path

from torque_guard.round2 import CaseInput, RelationEvidenceAgent


ROOT = Path(__file__).resolve().parents[1]


class RoundTwoRelationAgentTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = ROOT / "docs" / "data" / "round2_cases.json"
        cls.payload = json.loads(source.read_text(encoding="utf-8"))

    def test_public_cases_are_explicitly_synthetic(self):
        for raw in self.payload["cases"]:
            case = CaseInput.from_mapping(raw)
            self.assertTrue(case.is_synthetic)
            self.assertTrue(case.case_id.startswith("CASE-DEMO-"))

    def test_structure_only_case_never_proposes_root_cause(self):
        case = CaseInput.from_mapping(self.payload["cases"][1])
        report = RelationEvidenceAgent().assess(case).to_dict()
        self.assertEqual(report["disposition"], "complete_case_before_reasoning")
        self.assertTrue(any(item["evidence_id"] == "G-CASE-04" for item in report["gaps"]))
        self.assertIn("不从脱敏包推断", report["boundary"])

    def test_every_task_references_known_evidence_and_needs_approval(self):
        case = CaseInput.from_mapping(self.payload["cases"][0])
        report = RelationEvidenceAgent().assess(case).to_dict()
        evidence = {item["evidence_id"] for item in report["facts"] + report["gaps"]}
        for task in report["tasks"]:
            self.assertTrue(task["approval_required"])
            self.assertTrue(set(task["evidence_ids"]).issubset(evidence))


if __name__ == "__main__":
    unittest.main()
