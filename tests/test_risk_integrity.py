import copy
import json
import shutil
import tempfile
import unittest
from collections import Counter
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from torque_guard.agent import DigitalEmployee
from torque_guard.knowledge import KnowledgeBase
from torque_guard.reasoning import ExternalModelConfig
from torque_guard.risk import RiskAnalyzer, read_events
from torque_guard.scenarios import generate_independent_case


ROOT = Path(__file__).resolve().parents[1]


class ActionContractIntegrityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.card = DigitalEmployee(ROOT / "knowledge").run(
            ROOT / "data" / "tightening_events_demo.csv", "P03"
        )

    def test_action_explanation_and_cross_references_fail_closed_when_tampered(self):
        mutations = (
            ("empty why", {"why": ""}, "action.why"),
            ("empty evidence", {"evidence_ids": []}, "至少包含一项"),
            ("unknown evidence", {"evidence_ids": ["E-FORGED"]}, "未知 evidence_id"),
            (
                "duplicate evidence",
                {"evidence_ids": ["E-SPC-01", "E-SPC-01"]},
                "evidence_ids 不得重复",
            ),
            ("empty causes", {"candidate_causes": []}, "至少包含一项"),
            (
                "unknown cause",
                {"candidate_causes": ["已经确认的伪造根因"]},
                "未知候选原因",
            ),
        )
        for label, changes, message in mutations:
            with self.subTest(label=label):
                card = copy.deepcopy(self.card)
                card.recommended_actions[0] = replace(
                    card.recommended_actions[0], **changes
                )
                with self.assertRaisesRegex(ValueError, message):
                    card.to_dict()


class _UnexpectedExternalClient:
    def __init__(self):
        self.calls = 0

    def complete(self, **_kwargs):
        self.calls += 1
        raise AssertionError("稳定窗口不得调用外部模型")


class RiskInputIntegrityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.analyzer = RiskAnalyzer(ROOT / "knowledge")
        cls.events = generate_independent_case("hidden_torque_drift", seed=9510)

    @staticmethod
    def _semantic_variant(events):
        local_zone = timezone(timedelta(hours=8))
        variant = []
        identifiers = {
            "event_id",
            "station_id",
            "tool_id",
            "model_code",
            "program_id",
            "fastening_point",
            "batch_id",
        }
        numerics = {
            "torque_nm",
            "angle_deg",
            "current_a",
            "cycle_time_s",
            "retry_count",
            "calibration_days_remaining",
        }
        for source in reversed(events):
            row = dict(source)
            for field in identifiers:
                row[field] = f"  {row[field]}  "
            parsed = datetime.fromisoformat(row["timestamp"]).replace(tzinfo=local_zone)
            row["timestamp"] = parsed.astimezone(timezone.utc).isoformat().replace(
                "+00:00", "Z"
            )
            for field in numerics:
                if row.get(field) is not None:
                    row[field] = f" {float(row[field]):.6f} "
            row["alarm_code"] = f" {row.get('alarm_code', '')} "
            variant.append(row)
        return variant

    def test_semantically_equivalent_inputs_share_window_revision_and_card_id(self):
        original = self.analyzer.analyze(self.events, "P03")
        equivalent = self.analyzer.analyze(self._semantic_variant(self.events), " P03 ")

        self.assertEqual(original.card_id, equivalent.card_id)
        self.assertEqual(
            original.analysis_provenance["input_window_revision"],
            equivalent.analysis_provenance["input_window_revision"],
        )
        self.assertEqual(original.created_at, equivalent.created_at)

    def test_material_window_or_stratum_change_changes_card_identity(self):
        original = self.analyzer.analyze(self.events, "P03")
        measurement_change = [dict(row) for row in self.events]
        measurement_change[-1]["torque_nm"] += 0.001
        changed_measurement = self.analyzer.analyze(measurement_change, "P03")
        self.assertNotEqual(
            original.analysis_provenance["input_window_revision"],
            changed_measurement.analysis_provenance["input_window_revision"],
        )
        self.assertNotEqual(original.card_id, changed_measurement.card_id)

        stratum_change = [dict(row, station_id="ST-FAS-08") for row in self.events]
        changed_stratum = self.analyzer.analyze(stratum_change, "P03")
        self.assertNotEqual(original.card_id, changed_stratum.card_id)
        self.assertEqual(
            changed_stratum.analysis_provenance["analysis_stratum"]["station_id"],
            "ST-FAS-08",
        )

    def test_all_missing_optional_metrics_are_unavailable_not_zero_sigma(self):
        missing = [dict(row, current_a=None, cycle_time_s=None) for row in self.events]
        card = self.analyzer.analyze(missing, "P03")
        availability = card.analysis_provenance["metric_availability"]

        self.assertFalse(availability["current_a"]["available"])
        self.assertFalse(availability["cycle_time_s"]["available"])
        self.assertEqual(availability["current_a"]["baseline_sample_count"], 0)
        self.assertEqual(availability["cycle_time_s"]["recent_sample_count"], 0)
        facts = " ".join(card.observed_facts)
        self.assertIn("电流偏移不可用", facts)
        self.assertIn("节拍偏移不可用", facts)
        equipment = next(item for item in card.evidence if item.evidence_id == "E-EQP-02")
        self.assertIsNone(equipment.data["current_shift_sigma"])
        self.assertIsNone(equipment.data["cycle_time_shift_sigma"])

    def test_partial_optional_metrics_are_excluded_from_score(self):
        partial = [dict(row) for row in self.events]
        partial[0]["current_a"] = None
        partial[-1]["cycle_time_s"] = None
        first = self.analyzer.analyze(partial, "P03")

        extreme = [dict(row) for row in partial]
        for row in extreme:
            if row["current_a"] is not None:
                row["current_a"] = 500.0
            if row["cycle_time_s"] is not None:
                row["cycle_time_s"] = 500.0
        second = self.analyzer.analyze(extreme, "P03")

        self.assertFalse(
            first.analysis_provenance["metric_availability"]["current_a"]["available"]
        )
        self.assertFalse(
            first.analysis_provenance["metric_availability"]["cycle_time_s"]["available"]
        )
        self.assertEqual(first.risk_score, second.risk_score)
        self.assertEqual(first.score_breakdown, second.score_breakdown)

    def test_strict_event_schema_and_physical_ranges_fail_closed(self):
        invalid_cases = (
            ("event_id", "   ", "event_id.*不能为空"),
            ("timestamp", "2026-07-20", "完整 ISO 8601"),
            ("retry_count", -1, "0..10000"),
            ("retry_count", 1.5, "0..10000"),
            ("calibration_days_remaining", -1, "0..36500"),
            ("torque_nm", 0, "合理范围"),
            ("current_a", -0.1, "合理范围"),
            ("cycle_time_s", 0, "合理范围"),
        )
        for field, value, message in invalid_cases:
            with self.subTest(field=field, value=value):
                damaged = [dict(row) for row in self.events]
                damaged[7][field] = value
                with self.assertRaisesRegex(ValueError, message):
                    self.analyzer.analyze(damaged, "P03")

    def test_duplicate_timestamp_is_allowed_with_deterministic_event_id_order(self):
        duplicated_time = [dict(row) for row in self.events]
        duplicated_time[1]["timestamp"] = duplicated_time[0]["timestamp"]
        ordered = self.analyzer.analyze(duplicated_time, "P03")
        reversed_input = self.analyzer.analyze(list(reversed(duplicated_time)), "P03")

        self.assertEqual(ordered.card_id, reversed_input.card_id)
        policy = ordered.analysis_provenance["timestamp_policy"]
        self.assertEqual(policy["event_ordering"], "utc_timestamp_then_event_id")
        self.assertEqual(
            policy["duplicate_timestamp_policy"], "allowed_and_ordered_by_event_id"
        )

    def test_trimmed_duplicate_event_ids_are_rejected_globally(self):
        damaged = [dict(row) for row in self.events]
        damaged[-1]["event_id"] = f"  {damaged[0]['event_id']}  "
        with self.assertRaisesRegex(ValueError, "重复 event_id"):
            self.analyzer.analyze(damaged, "P03")


class KnowledgeIntegrityTest(unittest.TestCase):
    def test_bom_line_endings_and_row_order_do_not_change_semantic_revision(self):
        original = KnowledgeBase(ROOT / "knowledge")
        with tempfile.TemporaryDirectory() as temporary:
            copy_root = Path(temporary) / "knowledge"
            shutil.copytree(ROOT / "knowledge", copy_root)
            for path in copy_root.iterdir():
                text = path.read_bytes().decode("utf-8-sig")
                text = text.replace("\r\n", "\n").replace("\n", "\r\n")
                path.write_bytes(("\ufeff" + text).encode("utf-8"))
            rewritten = KnowledgeBase(copy_root)
        self.assertEqual(original.revision, rewritten.revision)

    def test_semantic_knowledge_change_changes_revision(self):
        original = KnowledgeBase(ROOT / "knowledge")
        with tempfile.TemporaryDirectory() as temporary:
            copy_root = Path(temporary) / "knowledge"
            shutil.copytree(ROOT / "knowledge", copy_root)
            history_path = copy_root / "historical_cases.json"
            history = json.loads(history_path.read_text(encoding="utf-8-sig"))
            history[0]["summary"] += "语义变更"
            history_path.write_text(
                json.dumps(history, ensure_ascii=False), encoding="utf-8"
            )
            changed = KnowledgeBase(copy_root)
        self.assertNotEqual(original.revision, changed.revision)

    def test_duplicate_keys_and_broken_references_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            copy_root = Path(temporary) / "knowledge"
            shutil.copytree(ROOT / "knowledge", copy_root)
            alarm_path = copy_root / "alarm_dictionary_demo.csv"
            lines = alarm_path.read_text(encoding="utf-8-sig").splitlines()
            alarm_path.write_text("\n".join([*lines, lines[1]]) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "alarm_code.*必须唯一"):
                KnowledgeBase(copy_root)

        with tempfile.TemporaryDirectory() as temporary:
            copy_root = Path(temporary) / "knowledge"
            shutil.copytree(ROOT / "knowledge", copy_root)
            ontology_path = copy_root / "ontology.json"
            ontology = json.loads(ontology_path.read_text(encoding="utf-8-sig"))
            ontology["nodes"] = [
                node for node in ontology["nodes"] if node["id"] != "C-CAL-DRIFT"
            ]
            ontology_path.write_text(
                json.dumps(ontology, ensure_ascii=False), encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "不存在的 node id|缺少引用节点"):
                KnowledgeBase(copy_root)

    def test_each_knowledge_file_is_read_only_once(self):
        original_read_bytes = Path.read_bytes
        calls = Counter()

        def tracked_read_bytes(path):
            calls[path.name] += 1
            return original_read_bytes(path)

        with patch.object(Path, "read_bytes", tracked_read_bytes):
            KnowledgeBase(ROOT / "knowledge")

        self.assertEqual(sum(calls.values()), 5)
        self.assertTrue(all(count == 1 for count in calls.values()))


class RiskCardConsistencyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.card = DigitalEmployee(ROOT / "knowledge").run(
            ROOT / "data" / "tightening_events_demo.csv", "P03"
        )

    def test_score_and_provenance_tampering_are_rejected_at_serialization(self):
        score_tampered = copy.deepcopy(self.card)
        score_tampered.score_breakdown["context"] += 1
        with self.assertRaisesRegex(ValueError, "risk_score 必须等于"):
            score_tampered.to_dict()

        provenance_tampered = copy.deepcopy(self.card)
        provenance_tampered.analysis_provenance["knowledge_revision"] = "sha256:bad"
        with self.assertRaisesRegex(ValueError, "knowledge_revision"):
            provenance_tampered.to_dict()

    def test_duplicate_or_dangling_evidence_references_are_rejected(self):
        duplicate = copy.deepcopy(self.card)
        duplicate.evidence.append(duplicate.evidence[0])
        with self.assertRaisesRegex(ValueError, "evidence_id 必须唯一"):
            duplicate.to_dict()

        dangling = copy.deepcopy(self.card)
        dangling.candidate_causes[0].evidence_ids.append("E-NOT-FOUND")
        with self.assertRaisesRegex(ValueError, "未知 evidence_id"):
            dangling.to_dict()

    def test_workflow_status_mismatch_is_rejected(self):
        tampered = copy.deepcopy(self.card)
        tampered.status = "approved"
        with self.assertRaisesRegex(ValueError, "workflow.status"):
            tampered.to_dict()


class StableWindowContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        all_events = read_events(ROOT / "data" / "tightening_events_demo.csv")
        cls.events = [
            row for row in all_events if row["fastening_point"] == "P03"
        ][:124]

    def test_low_stable_window_does_not_manufacture_an_incident(self):
        card = DigitalEmployee(ROOT / "knowledge").run_events(
            self.events,
            "P03",
            source_label="P03:first_124_normal_events",
        )

        self.assertEqual((card.risk_level, card.risk_score), ("low", 39))
        self.assertEqual(card.status, "monitoring_only")
        self.assertFalse(card.analysis_provenance["attribution_required"])
        self.assertEqual(
            card.analysis_provenance["analysis_disposition"], "stable_monitoring"
        )
        self.assertEqual(card.analysis_provenance["trigger_reasons"], [])
        self.assertEqual(card.candidate_causes, [])
        self.assertEqual(card.recommended_actions, [])
        facts = " ".join(card.observed_facts)
        self.assertIn("当前窗口稳定", facts)
        self.assertIn("未触发 SPC 异常规则或设备组合异常信号", facts)
        self.assertNotIn("趋势风险", facts)

        self.assertEqual(card.reasoning["decision"], "refused")
        self.assertEqual(
            card.reasoning["disposition"], "no_attribution_required"
        )
        self.assertIsNone(card.reasoning["conclusion"])
        self.assertEqual(card.reasoning["hypotheses"], [])
        self.assertFalse(card.reasoning["safety"]["requires_human_approval"])
        self.assertIn("无需启动根因归因", card.inference)

        self.assertEqual(card.workflow["status"], "monitoring_only")
        self.assertEqual(card.workflow["allowed_actions"], [])
        self.assertFalse(card.workflow["human_approval_required"])
        self.assertEqual(card.workflow["events"], [])
        trace_results = " ".join(item["result"] for item in card.agent_trace)
        self.assertNotIn("等待具名工程师审批", trace_results)
        self.assertNotIn("待验证假设", trace_results)
        card.validate()

    def test_stable_window_never_calls_configured_external_model(self):
        client = _UnexpectedExternalClient()
        card = DigitalEmployee(
            ROOT / "knowledge",
            external_model=ExternalModelConfig(
                enabled=True,
                provider="example",
                model="example-model",
            ),
            model_client=client,
            environment={"TORQUE_GUARD_MODEL_API_KEY": "test-only-secret"},
        ).run_events(self.events, "P03")

        self.assertEqual(client.calls, 0)
        self.assertEqual(card.reasoning["disposition"], "no_attribution_required")
        self.assertEqual(
            card.reasoning["provenance"]["reasoner_mode"], "deterministic"
        )

    def test_low_score_with_a_real_alarm_still_routes_for_investigation(self):
        alarm_events = [dict(row) for row in self.events]
        alarm_events[-1]["alarm_code"] = "ALM-314"
        card = DigitalEmployee(ROOT / "knowledge").run_events(alarm_events, "P03")

        self.assertEqual(card.risk_level, "low")
        self.assertTrue(card.analysis_provenance["attribution_required"])
        self.assertIn(
            "equipment_alarm=ALM-314",
            card.analysis_provenance["trigger_reasons"],
        )
        self.assertTrue(card.candidate_causes)
        self.assertTrue(card.recommended_actions)
        self.assertEqual(card.reasoning["decision"], "supported")
        self.assertEqual(card.status, "awaiting_engineer_review")

    def test_stable_card_tampering_with_an_action_fails_closed(self):
        stable = DigitalEmployee(ROOT / "knowledge").run_events(self.events, "P03")
        high = DigitalEmployee(ROOT / "knowledge").run(
            ROOT / "data" / "tightening_events_demo.csv", "P03"
        )
        stable.recommended_actions.append(high.recommended_actions[0])
        with self.assertRaisesRegex(ValueError, "不得生成候选原因或处置任务"):
            stable.to_dict()


if __name__ == "__main__":
    unittest.main()
