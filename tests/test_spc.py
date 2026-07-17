import unittest

from torque_guard.spc import capability_snapshot, western_electric_rules


class SpcRulesTest(unittest.TestCase):
    def test_eight_points_on_one_side_are_flagged(self):
        values = [0.1, -0.1, 0.0, 0.2, -0.2] + [0.35] * 8
        hits = western_electric_rules(values, center=0.0, sigma=1.0)
        self.assertIn("WE-04", {hit.rule_id for hit in hits})

    def test_capability_snapshot_is_transparent(self):
        result = capability_snapshot([48.0, 48.2, 47.8, 48.1], 43.0, 53.0)
        self.assertAlmostEqual(result["mean"], 48.025, places=3)
        self.assertGreater(result["cpk"], 1.0)


if __name__ == "__main__":
    unittest.main()
