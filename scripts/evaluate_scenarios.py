from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from pathlib import Path

from torque_guard.artifacts import write_json
from torque_guard.risk import RiskAnalyzer
from torque_guard.scenarios import EXPECTED_PRIMARY_CAUSE, SCENARIOS, generate_independent_case


ROOT = Path(__file__).resolve().parents[1]


def ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def wilson_interval(successes: int, total: int, z: float = 1.96) -> list[float]:
    if total == 0:
        return [0.0, 0.0]
    p = successes / total
    denominator = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total) / denominator
    return [round(max(0.0, center - margin), 4), round(min(1.0, center + margin), 4)]


def main(artifact_root: Path = ROOT) -> None:
    artifact_root = Path(artifact_root)
    analyzer = RiskAnalyzer(ROOT / "knowledge")
    cases = []
    per_scenario: dict[str, list[dict[str, object]]] = defaultdict(list)

    for scenario_index, scenario in enumerate(SCENARIOS):
        for case_index in range(30):
            seed = 20260800 + scenario_index * 100 + case_index
            strength = 0.78 + (case_index % 8) * 0.06 if scenario != "normal" else 1.0
            events = generate_independent_case(scenario, seed=seed, strength=strength)
            card = analyzer.analyze(events, "P03")
            truth = scenario != "normal"
            prediction = card.risk_level in {"medium", "high"}
            conventional_alarm = any(
                row["alarm_code"] or row["result"] != "OK"
                for row in events[-24:]
            )
            expected_cause = EXPECTED_PRIMARY_CAUSE.get(scenario, "")
            primary_cause = (
                card.candidate_causes[0].cause if card.candidate_causes else None
            )
            cause_correct = bool(
                expected_cause and primary_cause == expected_cause
            )
            item = {
                "scenario": scenario,
                "case_id": f"{scenario}-{case_index + 1:02d}",
                "truth": truth,
                "prediction": prediction,
                "conventional_alarm": conventional_alarm,
                "risk_level": card.risk_level,
                "risk_score": card.risk_score,
                "primary_cause": primary_cause,
                "cause_correct": cause_correct,
            }
            cases.append(item)
            per_scenario[scenario].append(item)

    tp = sum(bool(item["truth"] and item["prediction"]) for item in cases)
    fp = sum(bool(not item["truth"] and item["prediction"]) for item in cases)
    tn = sum(bool(not item["truth"] and not item["prediction"]) for item in cases)
    fn = sum(bool(item["truth"] and not item["prediction"]) for item in cases)
    positives = tp + fn
    negatives = fp + tn
    cause_items = [item for item in cases if item["truth"]]
    cause_hits = sum(bool(item["cause_correct"]) for item in cause_items)
    conventional_tp = sum(bool(item["truth"] and item["conventional_alarm"]) for item in cases)
    conventional_fp = sum(bool(not item["truth"] and item["conventional_alarm"]) for item in cases)

    scenario_summary = {}
    for scenario, items in per_scenario.items():
        detections = sum(bool(item["prediction"]) for item in items)
        level_counts = Counter(str(item["risk_level"]) for item in items)
        scenario_summary[scenario] = {
            "cases": len(items),
            "detection_rate": round(ratio(detections, len(items)), 4),
            "level_counts": dict(level_counts),
            "primary_cause_accuracy": round(
                ratio(sum(bool(item["cause_correct"]) for item in items), len(items)), 4
            )
            if scenario != "normal"
            else None,
        }

    metrics = {
        "evaluation_scope": "independent synthetic case suite; not factory evidence",
        "cases": len(cases),
        "cases_per_scenario": 30,
        "scenarios": scenario_summary,
        "confusion_matrix": {"tp": tp, "fp": fp, "tn": tn, "fn": fn},
        "recall": round(ratio(tp, positives), 4),
        "recall_95pct_wilson": wilson_interval(tp, positives),
        "false_positive_rate": round(ratio(fp, negatives), 4),
        "false_positive_rate_95pct_wilson": wilson_interval(fp, negatives),
        "primary_cause_top1_accuracy": round(ratio(cause_hits, len(cause_items)), 4),
        "conventional_alarm_recall": round(ratio(conventional_tp, positives), 4),
        "conventional_alarm_false_positive_rate": round(ratio(conventional_fp, negatives), 4),
        "limitations": [
            "All cases are deterministic synthetic competition data.",
            "The suite validates code behavior and scenario coverage, not factory generalization.",
            "Business value and thresholds require an enterprise-controlled pilot.",
        ],
    }
    for target in [
        artifact_root / "outputs" / "scenario_metrics.json",
        artifact_root / "docs" / "data" / "scenario_metrics.json",
    ]:
        target.parent.mkdir(parents=True, exist_ok=True)
        write_json(target, metrics)
    print(json.dumps(metrics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
