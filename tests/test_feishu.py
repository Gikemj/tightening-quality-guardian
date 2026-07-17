import unittest
from pathlib import Path

from torque_guard.agent import DigitalEmployee
from torque_guard.integrations.feishu import build_bitable_records


ROOT = Path(__file__).resolve().parents[1]


class FeishuPayloadTest(unittest.TestCase):
    def test_preview_contains_owner_deadline_and_acceptance_fields(self):
        card = DigitalEmployee(ROOT / "knowledge").run(
            ROOT / "data" / "tightening_events_demo.csv", "P03"
        )
        records = build_bitable_records(card)
        self.assertEqual(len(records), 1 + len(card.recommended_actions))
        action = records[1]["fields"]
        self.assertIn("责任角色", action)
        self.assertIn("时限（分钟）", action)
        self.assertIn("验收依据", action)
        self.assertTrue(action["需人工审批"])


if __name__ == "__main__":
    unittest.main()
