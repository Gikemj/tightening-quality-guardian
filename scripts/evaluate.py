from __future__ import annotations

import json
from pathlib import Path
from statistics import median

from torque_guard.artifacts import write_json
from torque_guard.risk import RiskAnalyzer, read_events


ROOT = Path(__file__).resolve().parents[1]


def ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def main(artifact_root: Path = ROOT) -> None:
    artifact_root = Path(artifact_root)
    events = read_events(artifact_root / "data" / "tightening_events_demo.csv")
    point_events = [row for row in events if row["fastening_point"] == "P03"]
    analyzer = RiskAnalyzer(ROOT / "knowledge")
    samples = []

    for end in range(124, len(point_events) + 1, 2):
        window = point_events[:end]
        card = analyzer.analyze(window, "P03")
        truth = any(row["scenario_label"] == "hidden_risk" for row in window[-24:])
        prediction = card.risk_level in {"medium", "high"}
        samples.append(
            {"end": end, "truth": truth, "prediction": prediction, "score": card.risk_score}
        )

    tp = sum(item["truth"] and item["prediction"] for item in samples)
    fp = sum((not item["truth"]) and item["prediction"] for item in samples)
    tn = sum((not item["truth"]) and (not item["prediction"]) for item in samples)
    fn = sum(item["truth"] and (not item["prediction"]) for item in samples)

    full_card = analyzer.analyze(point_events, "P03")
    evidence_traceability = ratio(
        sum(bool(item.source and item.locator) for item in full_card.evidence), len(full_card.evidence)
    )
    task_completeness = ratio(
        sum(
            bool(
                action.owner_role
                and action.due_minutes
                and action.acceptance_criteria
                and action.why
                and action.evidence_ids
                and action.candidate_causes
                and action.approval_required
            )
            for action in full_card.recommended_actions
        ),
        len(full_card.recommended_actions),
    )
    positive_scores = [item["score"] for item in samples if item["truth"]]
    negative_scores = [item["score"] for item in samples if not item["truth"]]

    metrics = {
        "evaluation_scope": "synthetic rolling-window validation for P03",
        "samples": len(samples),
        "confusion_matrix": {"tp": tp, "fp": fp, "tn": tn, "fn": fn},
        "recall": round(ratio(tp, tp + fn), 4),
        "precision": round(ratio(tp, tp + fp), 4),
        "false_positive_rate": round(ratio(fp, fp + tn), 4),
        "median_positive_score": round(median(positive_scores), 1) if positive_scores else 0,
        "median_negative_score": round(median(negative_scores), 1) if negative_scores else 0,
        "evidence_traceability": round(evidence_traceability, 4),
        "task_field_completeness": round(task_completeness, 4),
        "limitations": [
            "All measurements and labels are synthetic.",
            "The validation tests scenario reproducibility, not factory-wide generalization.",
            "Thresholds require recalibration on real equipment and stratification by model, program, and fastening point.",
        ],
    }
    output_dir = artifact_root / "outputs"
    output_dir.mkdir(parents=True, exist_ok=True)
    write_json(output_dir / "metrics.json", metrics)
    web_data = artifact_root / "docs" / "data"
    web_data.mkdir(parents=True, exist_ok=True)
    write_json(web_data / "metrics.json", metrics)
    print(json.dumps(metrics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
