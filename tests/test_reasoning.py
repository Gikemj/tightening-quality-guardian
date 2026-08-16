import unittest
from pathlib import Path

from torque_guard.agent import DigitalEmployee
from torque_guard.models import CandidateCause, Evidence
from torque_guard.reasoning import (
    DeterministicReasoner,
    ExternalModelConfig,
    ReasoningRequest,
    SafeConfiguredReasoner,
    load_output_schema,
    load_system_prompt,
)
from torque_guard.risk import RiskAnalyzer
from torque_guard.scenarios import generate_independent_case


ROOT = Path(__file__).resolve().parents[1]


class _MustNotBeCalledClient:
    def complete(self, **_kwargs):
        raise AssertionError("缺少密钥时不得调用外部模型客户端")


class _UnsafeOutputClient:
    def __init__(self):
        self.calls = 0

    def complete(self, **_kwargs):
        self.calls += 1
        return {
            "schema_version": "1.0",
            "decision": "supported",
            "conclusion": {
                "text": "已确认根因，可以自动停线。",
                "evidence_ids": ["E-SPC-01"],
            },
            "hypotheses": [
                {
                    "cause": "工具根因",
                    "confidence": "high",
                    "evidence_ids": ["E-EQP-02"],
                    "verification": "无需验证",
                }
            ],
            "uncertainty": "无",
            "refusal_reason": None,
            "safety": {
                "requires_human_approval": True,
                "automatic_action_allowed": False,
            },
        }


class ControlledReasoningTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.card = DigitalEmployee(ROOT / "knowledge").run(
            ROOT / "data" / "tightening_events_demo.csv", "P03"
        )

    def test_every_supported_claim_and_hypothesis_cites_known_evidence(self):
        known_ids = {item.evidence_id for item in self.card.evidence}
        reasoning = self.card.reasoning

        self.assertEqual(reasoning["decision"], "supported")
        self.assertTrue(set(reasoning["conclusion"]["evidence_ids"]).issubset(known_ids))
        self.assertIn("[证据：", self.card.inference)
        for cause in self.card.candidate_causes:
            self.assertTrue(cause.evidence_ids)
            self.assertTrue(set(cause.evidence_ids).issubset(known_ids))
            self.assertTrue(all("[E-" in basis for basis in cause.basis))

    def test_insufficient_evidence_refuses_to_infer(self):
        request = ReasoningRequest(
            card_id="TG-TEST",
            risk_level="medium",
            risk_score=50,
            observed_facts=("只有一条未交叉验证的观察",),
            evidence=(
                Evidence(
                    "E-ONLY",
                    "spc",
                    "单一观察",
                    "均值变化",
                    "test.csv",
                    "row=1",
                    "direct",
                ),
            ),
            proposed_causes=(CandidateCause("未知原因", "low", [], "补充证据"),),
        )

        result = DeterministicReasoner().reason(request)

        self.assertEqual(result.decision, "refused")
        self.assertIsNone(result.conclusion)
        self.assertEqual(result.hypotheses, ())
        self.assertIn("证据", result.refusal_reason)

    def test_new_evidence_ids_are_selected_by_semantics_not_hardcoded_names(self):
        request = ReasoningRequest(
            card_id="TG-GENERIC",
            risk_level="medium",
            risk_score=60,
            observed_facts=("过程信号变化",),
            evidence=(
                Evidence(
                    "MEASUREMENT-2026-001",
                    "spc",
                    "过程测量",
                    "均值偏移",
                    "events.csv",
                    "window=recent",
                    "direct",
                ),
                Evidence(
                    "PFMEA-REV-C-009",
                    "pfmea",
                    "失效链",
                    "待验证失效模式",
                    "pfmea.csv",
                    "FM-009",
                    "document",
                ),
            ),
            proposed_causes=(
                CandidateCause("待验证过程原因", "medium", [], "执行现场复核"),
            ),
        )

        result = DeterministicReasoner().reason(request)

        self.assertEqual(result.decision, "supported")
        self.assertEqual(
            set(result.conclusion.evidence_ids),
            {"MEASUREMENT-2026-001", "PFMEA-REV-C-009"},
        )
        self.assertTrue(result.hypotheses[0].evidence_ids)

    def test_alarm_scenarios_feed_dictionary_evidence_into_reasoning(self):
        analyzer = RiskAnalyzer(ROOT / "knowledge")
        for index, scenario in enumerate(("sensor_zero_drift", "repeated_alarm")):
            with self.subTest(scenario=scenario):
                card = analyzer.analyze(
                    generate_independent_case(scenario, seed=9400 + index), "P03"
                )
                result = DeterministicReasoner().reason(ReasoningRequest.from_card(card))
                known_ids = {item.evidence_id for item in card.evidence}

                self.assertEqual(result.decision, "supported")
                self.assertIn("E-ALM-06", result.hypotheses[0].evidence_ids)
                self.assertTrue(
                    all(
                        set(hypothesis.evidence_ids).issubset(known_ids)
                        for hypothesis in result.hypotheses
                    )
                )

    def test_missing_external_key_falls_back_without_calling_client(self):
        request = ReasoningRequest.from_card(self.card)
        reasoner = SafeConfiguredReasoner(
            ExternalModelConfig(enabled=True, provider="example", model="example-model"),
            client=_MustNotBeCalledClient(),
            env={},
        )

        result = reasoner.reason(request)

        self.assertEqual(result.reasoner_mode, "deterministic")
        self.assertEqual(result.fallback_reason, "external_api_key_missing")
        self.assertEqual(result.decision, "supported")

    def test_unsafe_external_output_is_rejected_and_marked_as_fallback(self):
        client = _UnsafeOutputClient()
        reasoner = SafeConfiguredReasoner(
            ExternalModelConfig(enabled=True, provider="example", model="example-model"),
            client=client,
            env={"TORQUE_GUARD_MODEL_API_KEY": "test-only-secret"},
        )

        result = reasoner.reason(ReasoningRequest.from_card(self.card))

        self.assertEqual(client.calls, 1)
        self.assertEqual(result.reasoner_mode, "deterministic")
        self.assertEqual(result.fallback_reason, "external_output_rejected:ValueError")
        self.assertNotIn("test-only-secret", str(result.to_dict()))

    def test_prompt_and_schema_enforce_citation_refusal_and_human_gate(self):
        prompt = load_system_prompt()
        schema = load_output_schema()

        self.assertIn("evidence_ids", prompt)
        self.assertIn('decision="refused"', prompt)
        self.assertIn("不得自动停线", prompt)
        self.assertEqual(schema["properties"]["schema_version"]["const"], "1.0")
        safety = schema["properties"]["safety"]["properties"]
        self.assertTrue(safety["requires_human_approval"]["const"])
        self.assertFalse(safety["automatic_action_allowed"]["const"])


if __name__ == "__main__":
    unittest.main()
