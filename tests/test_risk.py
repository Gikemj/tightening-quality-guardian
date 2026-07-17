import json
import unittest
from pathlib import Path

from torque_guard.agent import DigitalEmployee
from torque_guard.risk import read_events


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

    def test_card_is_json_serializable(self):
        payload = json.dumps(self.card.to_dict(), ensure_ascii=False)
        self.assertIn("TG-", payload)


if __name__ == "__main__":
    unittest.main()
