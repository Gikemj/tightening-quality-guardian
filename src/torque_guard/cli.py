from __future__ import annotations

import argparse
import json
from pathlib import Path

from .agent import DigitalEmployee
from .integrations.feishu import build_bitable_records


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the TorqueGuard competition prototype")
    parser.add_argument("--input", default="data/tightening_events_demo.csv")
    parser.add_argument("--knowledge", default="knowledge")
    parser.add_argument("--point", default="P03")
    parser.add_argument("--output", default="outputs/risk_card.json")
    parser.add_argument("--feishu-preview", default="outputs/feishu_records_preview.json")
    args = parser.parse_args()

    card = DigitalEmployee(args.knowledge).run(args.input, args.point)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(card.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    feishu_output = Path(args.feishu_preview)
    feishu_output.parent.mkdir(parents=True, exist_ok=True)
    feishu_output.write_text(
        json.dumps(build_bitable_records(card), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"{card.card_id}: {card.risk_level} ({card.risk_score}) -> {output}")


if __name__ == "__main__":
    main()
