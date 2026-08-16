import unittest
from pathlib import Path

from torque_guard.risk import RiskAnalyzer
from torque_guard.scenarios import EXPECTED_PRIMARY_CAUSE, generate_independent_case


ROOT = Path(__file__).resolve().parents[1]


class IndependentScenarioTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.analyzer = RiskAnalyzer(ROOT / "knowledge")

    def test_three_risk_scenarios_are_detected_and_traceable(self):
        for index, scenario in enumerate(EXPECTED_PRIMARY_CAUSE):
            with self.subTest(scenario=scenario):
                events = generate_independent_case(scenario, seed=9100 + index, strength=1.1)
                card = self.analyzer.analyze(events, "P03")
                self.assertIn(card.risk_level, {"medium", "high"})
                self.assertEqual(card.candidate_causes[0].cause, EXPECTED_PRIMARY_CAUSE[scenario])
                self.assertTrue(all(43 <= row["torque_nm"] <= 53 for row in events[-24:]))

    def test_alarm_scenarios_include_dictionary_evidence(self):
        for scenario in ("sensor_zero_drift", "repeated_alarm"):
            with self.subTest(scenario=scenario):
                events = generate_independent_case(scenario, seed=9200, strength=1.0)
                card = self.analyzer.analyze(events, "P03")
                self.assertIn("E-ALM-06", {item.evidence_id for item in card.evidence})

    def test_unknown_point_and_missing_fields_fail_with_clear_errors(self):
        events = generate_independent_case("normal", seed=9300)
        with self.assertRaisesRegex(ValueError, "不存在紧固点"):
            self.analyzer.analyze(events, "P99")
        damaged = [dict(row) for row in events]
        damaged[0].pop("torque_nm")
        with self.assertRaisesRegex(ValueError, "缺少字段"):
            self.analyzer.analyze(damaged, "P03")

    def test_every_row_is_validated_and_non_finite_measurements_fail_closed(self):
        events = generate_independent_case("normal", seed=9301)
        damaged = [dict(row) for row in events]
        damaged[17].pop("angle_deg")
        with self.assertRaisesRegex(ValueError, "第 18 条事件缺少字段"):
            self.analyzer.analyze(damaged, "P03")

        damaged = [dict(row) for row in events]
        damaged[-1]["torque_nm"] = float("nan")
        with self.assertRaisesRegex(ValueError, "必须是有限数字"):
            self.analyzer.analyze(damaged, "P03")

        damaged = [dict(row) for row in events]
        damaged[23]["timestamp"] = "not-a-timestamp"
        with self.assertRaisesRegex(ValueError, "ISO 8601"):
            self.analyzer.analyze(damaged, "P03")

    def test_duplicate_event_ids_and_invalid_window_configuration_are_rejected(self):
        events = generate_independent_case("normal", seed=9302)
        duplicated = [dict(row) for row in events]
        duplicated[-1]["event_id"] = duplicated[-2]["event_id"]
        with self.assertRaisesRegex(ValueError, "重复 event_id"):
            self.analyzer.analyze(duplicated, "P03")

        with self.assertRaisesRegex(ValueError, "baseline_count"):
            RiskAnalyzer(ROOT / "knowledge", baseline_count=1)
        with self.assertRaisesRegex(ValueError, "recent_count"):
            RiskAnalyzer(ROOT / "knowledge", recent_count=0)


if __name__ == "__main__":
    unittest.main()
