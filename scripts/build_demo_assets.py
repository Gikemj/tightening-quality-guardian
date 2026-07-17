from __future__ import annotations

import json
from pathlib import Path

from torque_guard.agent import DigitalEmployee
from torque_guard.integrations.feishu import build_bitable_records
from torque_guard.knowledge import KnowledgeBase
from torque_guard.risk import read_events


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    events = read_events(ROOT / "data" / "tightening_events_demo.csv")
    point_events = [row for row in events if row["fastening_point"] == "P03"]
    card = DigitalEmployee(ROOT / "knowledge").run(
        ROOT / "data" / "tightening_events_demo.csv", "P03"
    )

    output_dir = ROOT / "outputs"
    web_data = ROOT / "docs" / "data"
    output_dir.mkdir(parents=True, exist_ok=True)
    web_data.mkdir(parents=True, exist_ok=True)

    card_text = json.dumps(card.to_dict(), ensure_ascii=False, indent=2)
    (output_dir / "risk_card.json").write_text(card_text, encoding="utf-8")
    (web_data / "risk_card.json").write_text(card_text, encoding="utf-8")

    feishu = json.dumps(build_bitable_records(card), ensure_ascii=False, indent=2)
    (output_dir / "feishu_records_preview.json").write_text(feishu, encoding="utf-8")
    (web_data / "feishu_records_preview.json").write_text(feishu, encoding="utf-8")

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
    (web_data / "demo_series.json").write_text(
        json.dumps(series, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    graph = KnowledgeBase(ROOT / "knowledge").retrieve("P03").subgraph
    (web_data / "subgraph.json").write_text(
        json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"built demo assets -> {web_data}")


if __name__ == "__main__":
    main()
