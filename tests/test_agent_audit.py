import json
import tempfile
import unittest
from pathlib import Path

from torque_guard.agent import DigitalEmployee
from torque_guard.workflow import PUBLIC_BUILD_TIMESTAMP, PUBLIC_BUILD_TRACE_MODE


ROOT = Path(__file__).resolve().parents[1]


class AgentAuditTrailTest(unittest.TestCase):
    def test_trace_is_created_at_real_tool_boundaries(self):
        employee = DigitalEmployee(ROOT / "knowledge")
        card = employee.run(ROOT / "data" / "tightening_events_demo.csv", "P03")

        self.assertEqual(
            [item["step"] for item in card.agent_trace],
            ["sense", "analyze", "reason", "govern"],
        )
        for sequence, item in enumerate(card.agent_trace, start=1):
            self.assertEqual(item["sequence"], sequence)
            self.assertEqual(item["status"], "succeeded")
            self.assertEqual(item["trace_mode"], "runtime")
            self.assertTrue(item["call_id"].startswith("CALL-"))
            self.assertIn("input_summary", item)
            self.assertIn("output_summary", item)
            self.assertGreaterEqual(item["duration_ms"], 0)
            self.assertIsNone(item["error"])
            self.assertTrue(item["result"])

        self.assertEqual(card.agent_trace, employee.last_trace)
        self.assertEqual(card.workflow["status"], "awaiting_engineer_review")
        self.assertFalse(card.workflow["automatic_stop_line_allowed"])

    def test_failed_tool_call_is_available_without_changing_exception_type(self):
        employee = DigitalEmployee(ROOT / "knowledge")
        missing = Path(tempfile.gettempdir()) / "torque-guard-definitely-missing.csv"

        with self.assertRaises(FileNotFoundError) as caught:
            employee.run(missing, "P03")

        self.assertEqual(len(employee.last_trace), 1)
        record = employee.last_trace[0]
        self.assertEqual(record["tool"], "read_events")
        self.assertEqual(record["status"], "failed")
        self.assertEqual(record["error"]["type"], "FileNotFoundError")
        self.assertEqual(caught.exception.agent_trace, employee.last_trace)

    def test_trace_and_reasoning_remain_json_serializable(self):
        card = DigitalEmployee(ROOT / "knowledge").run(
            ROOT / "data" / "tightening_events_demo.csv", "P03"
        )
        payload = json.dumps(card.to_dict(), ensure_ascii=False)
        self.assertIn('"status": "succeeded"', payload)
        self.assertIn('"decision": "supported"', payload)

    def test_measurement_evidence_uses_the_actual_input_source_label(self):
        card = DigitalEmployee(ROOT / "knowledge").run(
            ROOT / "data" / "tightening_events_demo.csv",
            "P03",
            source_label="incoming/shift-a-events.csv",
        )
        measurement_sources = {
            item.source for item in card.evidence if item.category in {"spc", "equipment"}
        }
        self.assertEqual(measurement_sources, {"incoming/shift-a-events.csv"})

    def test_public_build_trace_is_reproducible_but_runtime_trace_stays_real(self):
        cards = [
            DigitalEmployee(
                ROOT / "knowledge",
                trace_mode=PUBLIC_BUILD_TRACE_MODE,
                trace_scope="risk",
            ).run(
                ROOT / "data" / "tightening_events_demo.csv",
                "P03",
                source_label="data/tightening_events_demo.csv",
            )
            for _ in range(2)
        ]

        self.assertEqual(cards[0].agent_trace, cards[1].agent_trace)
        self.assertEqual(
            [item["call_id"] for item in cards[0].agent_trace],
            [f"CALL-PUBLIC-RISK-{index:03d}" for index in range(1, 5)],
        )
        self.assertEqual(
            cards[0].agent_trace[0]["input_summary"]["file"],
            "data/tightening_events_demo.csv",
        )
        for item in cards[0].agent_trace:
            self.assertEqual(item["trace_mode"], "deterministic_public_build")
            self.assertEqual(item["started_at"], PUBLIC_BUILD_TIMESTAMP)
            self.assertEqual(item["completed_at"], PUBLIC_BUILD_TIMESTAMP)
            self.assertEqual(item["duration_ms"], 0.0)

        runtime = DigitalEmployee(ROOT / "knowledge").run(
            ROOT / "data" / "tightening_events_demo.csv", "P03"
        )
        self.assertTrue(
            all(item["trace_mode"] == "runtime" for item in runtime.agent_trace)
        )
        self.assertTrue(
            all(not item["call_id"].startswith("CALL-PUBLIC-") for item in runtime.agent_trace)
        )


if __name__ == "__main__":
    unittest.main()
