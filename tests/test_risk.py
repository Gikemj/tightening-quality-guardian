import json
import unittest
from pathlib import Path

from scripts.build_demo_assets import _authoritative_result
from torque_guard.agent import DigitalEmployee
from torque_guard.risk import RiskAnalyzer, read_events


ROOT = Path(__file__).resolve().parents[1]


class RiskCardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.events = read_events(ROOT / "data" / "tightening_events_demo.csv")
        cls.card = DigitalEmployee(ROOT / "knowledge").run(
            ROOT / "data" / "tightening_events_demo.csv", "P03"
        )

    def test_hidden_risk_remains_inside_specification(self):
        recent = [row for row in self.events if row["fastening_point"] == "P03"][-24:]
        self.assertTrue(all(43 <= row["torque_nm"] <= 53 for row in recent))
        self.assertEqual(self.card.risk_level, "high")
        self.assertGreaterEqual(self.card.risk_score, 75)

    def test_evidence_is_traceable(self):
        self.assertGreaterEqual(len(self.card.evidence), 5)
        for item in self.card.evidence:
            self.assertTrue(item.source)
            self.assertTrue(item.locator)

    def test_actions_keep_human_approval(self):
        self.assertTrue(all(item.approval_required for item in self.card.recommended_actions))
        self.assertIn("不得自动停线", self.card.recommended_actions[-1].acceptance_criteria)

    def test_actions_explain_why_and_reference_only_known_evidence_and_candidates(self):
        evidence_ids = {item.evidence_id for item in self.card.evidence}
        candidate_causes = {item.cause for item in self.card.candidate_causes}
        self.assertEqual(len(self.card.recommended_actions), 4)
        for action in self.card.recommended_actions:
            self.assertTrue(action.why)
            self.assertTrue(action.evidence_ids)
            self.assertTrue(set(action.evidence_ids).issubset(evidence_ids))
            self.assertTrue(action.candidate_causes)
            self.assertTrue(set(action.candidate_causes).issubset(candidate_causes))
            self.assertNotIn("已确认根因", action.why)

    def test_card_is_json_serializable(self):
        payload = json.dumps(self.card.to_dict(), ensure_ascii=False)
        self.assertIn("TG-", payload)

    def test_card_records_policy_knowledge_and_input_revisions(self):
        provenance = self.card.analysis_provenance
        self.assertEqual(self.card.schema_version, "1.0")
        self.assertEqual(provenance["generated_by"], "torque_guard.risk.RiskAnalyzer")
        self.assertEqual(provenance["risk_policy_version"], "risk-policy-2.0")
        self.assertTrue(provenance["knowledge_revision"].startswith("sha256:"))
        self.assertTrue(provenance["input_window_revision"].startswith("sha256:"))
        self.assertEqual(len(provenance["knowledge_revision"]), 71)
        self.assertEqual(len(provenance["input_window_revision"]), 71)
        self.assertEqual(len(provenance["card_identity_revision"]), 71)
        self.assertEqual(len(self.card.card_id), 35)

    def test_public_demo_risk_card_golden_contract(self):
        self.assertEqual(self.card.card_id, "TG-24FE6C93FCFE64619D890D4247FA71D7")
        self.assertEqual(self.card.created_at, "2026-07-14T07:20:00Z")
        self.assertEqual(self.card.risk_score, 80)
        self.assertEqual(
            self.card.score_breakdown,
            {
                "process_stability": 22,
                "equipment_health": 23,
                "quality_impact": 22,
                "context": 13,
            },
        )

    def test_authoritative_results_publish_four_data_driven_comparisons(self):
        analyzer = RiskAnalyzer(ROOT / "knowledge")
        point_events = [row for row in self.events if row["fastening_point"] == "P03"]
        baseline_card = DigitalEmployee(ROOT / "knowledge").run_events(
            point_events[: analyzer.baseline_count + analyzer.recent_count], "P03"
        )
        for scenario, card in (("risk", self.card), ("baseline", baseline_card)):
            with self.subTest(scenario=scenario):
                result = _authoritative_result(card, analyzer, scenario)
                comparisons = result["comparisons"]
                self.assertEqual(
                    [item["metric_id"] for item in comparisons],
                    [
                        "torque_mean_nm",
                        "angle_dispersion_ratio",
                        "retry_mean",
                        "in_spec_rate",
                    ],
                )
                known_evidence = {item.evidence_id for item in card.evidence}
                for comparison in comparisons:
                    self.assertEqual(
                        set(comparison),
                        {
                            "metric_id",
                            "label",
                            "baseline",
                            "current",
                            "delta",
                            "unit",
                            "status",
                            "rule_ids",
                            "evidence_ids",
                        },
                    )
                    self.assertIn(comparison["status"], {"normal", "triggered"})
                    self.assertTrue(comparison["evidence_ids"])
                    self.assertTrue(
                        set(comparison["evidence_ids"]).issubset(known_evidence)
                    )
                self.assertIn("retry_baseline", result["metrics"])
                self.assertIn("baseline_in_spec_rate", result["metrics"])


if __name__ == "__main__":
    unittest.main()
