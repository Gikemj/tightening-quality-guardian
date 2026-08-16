from __future__ import annotations

from pathlib import Path

from torque_guard.agent import DigitalEmployee
from torque_guard.artifacts import write_json
from torque_guard.integrations.feishu import build_bitable_records
from torque_guard.knowledge import KnowledgeBase
from torque_guard.models import RiskCard
from torque_guard.risk import RiskAnalyzer, read_events
from torque_guard.workflow import PUBLIC_BUILD_TRACE_MODE


ROOT = Path(__file__).resolve().parents[1]


def _evidence(card: RiskCard, evidence_id: str) -> dict:
    for item in card.evidence:
        if item.evidence_id == evidence_id:
            return item.data
    raise ValueError(f"{card.card_id} 缺少必要证据 {evidence_id}")


def _authoritative_result(card: RiskCard, analyzer: RiskAnalyzer, scenario: str) -> dict:
    """Create a browser-safe projection without reimplementing risk scoring."""
    spc = _evidence(card, "E-SPC-01")
    equipment = _evidence(card, "E-EQP-02")
    control = _evidence(card, "E-CTL-04")
    mean_shift_sigma = float(spc["mean_shift_sigma"])
    trigger_reasons = set(card.analysis_provenance["trigger_reasons"])
    angle_retry_rule_ids = [
        "angle_dispersion_and_retry_increase"
    ] if "angle_dispersion_and_retry_increase" in trigger_reasons else []
    comparisons = [
        {
            "metric_id": "torque_mean_nm",
            "label": "扭矩均值",
            "baseline": spc["baseline_mean_nm"],
            "current": spc["recent_mean_nm"],
            "delta": round(
                float(spc["recent_mean_nm"]) - float(spc["baseline_mean_nm"]),
                3,
            ),
            "unit": "N·m",
            "status": "triggered" if spc["rule_ids"] else "normal",
            "rule_ids": list(spc["rule_ids"]),
            "evidence_ids": ["E-SPC-01"],
        },
        {
            "metric_id": "angle_dispersion_ratio",
            "label": "角度离散比",
            "baseline": 1.0,
            "current": equipment["angle_std_ratio"],
            "delta": round(float(equipment["angle_std_ratio"]) - 1.0, 3),
            "unit": "倍",
            "status": "triggered" if angle_retry_rule_ids else "normal",
            "rule_ids": angle_retry_rule_ids,
            "evidence_ids": ["E-EQP-02"],
        },
        {
            "metric_id": "retry_mean",
            "label": "重试均值",
            "baseline": equipment["retry_baseline"],
            "current": equipment["retry_recent"],
            "delta": round(
                float(equipment["retry_recent"]) - float(equipment["retry_baseline"]),
                3,
            ),
            "unit": "次/循环",
            "status": "triggered" if angle_retry_rule_ids else "normal",
            "rule_ids": angle_retry_rule_ids,
            "evidence_ids": ["E-EQP-02"],
        },
        {
            "metric_id": "in_spec_rate",
            "label": "规格内比例",
            "baseline": round(float(spc["baseline_in_spec_rate"]) * 100, 2),
            "current": round(float(spc["in_spec_rate"]) * 100, 2),
            "delta": round(
                (float(spc["in_spec_rate"]) - float(spc["baseline_in_spec_rate"]))
                * 100,
                2,
            ),
            "unit": "%",
            "status": "normal" if float(spc["in_spec_rate"]) == 1.0 else "triggered",
            "rule_ids": [],
            "evidence_ids": ["E-SPC-01"],
        },
    ]
    return {
        "schema_version": "1.0",
        "scenario": scenario,
        "generated_by": "torque_guard.risk.RiskAnalyzer",
        "display_contract": "browser_read_only",
        "card_id": card.card_id,
        "risk_score": card.risk_score,
        "risk_level": card.risk_level,
        "score_breakdown": card.score_breakdown,
        "analysis_window": {
            "baseline_count": analyzer.baseline_count,
            "recent_count": analyzer.recent_count,
        },
        "comparisons": comparisons,
        "metrics": {
            "baseline_mean_nm": spc["baseline_mean_nm"],
            "baseline_sigma_nm": spc["baseline_sigma_nm"],
            "recent_mean_nm": spc["recent_mean_nm"],
            "recent_cpk": spc["recent_cpk"],
            "mean_shift_sigma": round(mean_shift_sigma, 2),
            "baseline_in_spec_rate": spc["baseline_in_spec_rate"],
            "in_spec_rate": spc["in_spec_rate"],
            "angle_std_ratio": equipment["angle_std_ratio"],
            "retry_baseline": equipment["retry_baseline"],
            "retry_recent": equipment["retry_recent"],
            "calibration_days_remaining": control["calibration_days_remaining"],
            "rule_ids": spc["rule_ids"],
        },
    }


def main(artifact_root: Path = ROOT) -> None:
    artifact_root = Path(artifact_root)
    events = read_events(artifact_root / "data" / "tightening_events_demo.csv")
    point_events = [row for row in events if row["fastening_point"] == "P03"]
    risk_card = DigitalEmployee(
        ROOT / "knowledge",
        trace_mode=PUBLIC_BUILD_TRACE_MODE,
        trace_scope="risk",
    ).run(
        artifact_root / "data" / "tightening_events_demo.csv",
        "P03",
        source_label="data/tightening_events_demo.csv",
    )
    analyzer = RiskAnalyzer(ROOT / "knowledge")
    baseline_event_count = analyzer.baseline_count + analyzer.recent_count
    baseline_events = point_events[:baseline_event_count]
    baseline_card = DigitalEmployee(
        ROOT / "knowledge",
        trace_mode=PUBLIC_BUILD_TRACE_MODE,
        trace_scope="baseline",
    ).run_events(
        baseline_events,
        "P03",
        source_label=f"P03:first_{baseline_event_count}_normal_events",
    )

    output_dir = artifact_root / "outputs"
    web_data = artifact_root / "docs" / "data"
    output_dir.mkdir(parents=True, exist_ok=True)
    web_data.mkdir(parents=True, exist_ok=True)

    write_json(output_dir / "risk_card.json", risk_card.to_dict())
    write_json(web_data / "risk_card.json", risk_card.to_dict())

    write_json(web_data / "baseline_card.json", baseline_card.to_dict())
    write_json(web_data / "risk_result.json", _authoritative_result(risk_card, analyzer, "risk"))
    write_json(web_data / "baseline_result.json", _authoritative_result(baseline_card, analyzer, "baseline"))

    feishu_records = build_bitable_records(risk_card)
    write_json(output_dir / "feishu_records_preview.json", feishu_records)
    write_json(web_data / "feishu_records_preview.json", feishu_records)

    series = [
        {
            "event_id": row["event_id"],
            "timestamp": row["timestamp"],
            "torque_nm": row["torque_nm"],
            "angle_deg": row["angle_deg"],
            "retry_count": row["retry_count"],
            "current_a": row["current_a"],
            "scenario_label": row["scenario_label"],
        }
        for row in point_events[-120:]
    ]
    write_json(web_data / "demo_series.json", series)
    baseline_series = [
        {
            "event_id": row["event_id"],
            "timestamp": row["timestamp"],
            "torque_nm": row["torque_nm"],
            "angle_deg": row["angle_deg"],
            "retry_count": row["retry_count"],
            "current_a": row["current_a"],
            "scenario_label": row["scenario_label"],
        }
        for row in baseline_events
    ]
    write_json(web_data / "baseline_series.json", baseline_series)
    graph = KnowledgeBase(ROOT / "knowledge").retrieve("P03").subgraph
    write_json(web_data / "subgraph.json", graph)
    print(f"built demo assets -> {web_data}")


if __name__ == "__main__":
    main()
