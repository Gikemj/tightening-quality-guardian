from __future__ import annotations

import csv
import json
import random
from datetime import datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"

FIELDS = [
    "event_id",
    "timestamp",
    "station_id",
    "tool_id",
    "controller_id",
    "model_code",
    "program_id",
    "fastening_point",
    "batch_id",
    "shift",
    "torque_nm",
    "angle_deg",
    "current_a",
    "cycle_time_s",
    "retry_count",
    "result",
    "alarm_code",
    "calibration_days_remaining",
    "scenario_label",
]

POINTS = {
    "P01": {"torque": 32.0, "angle": 82.0, "count": 150},
    "P02": {"torque": 40.0, "angle": 90.0, "count": 150},
    "P03": {"torque": 48.0, "angle": 95.0, "count": 220},
    "P04": {"torque": 56.0, "angle": 104.0, "count": 150},
    "P05": {"torque": 64.0, "angle": 112.0, "count": 150},
}


def generate() -> list[dict[str, str | int | float]]:
    rng = random.Random(20260717)
    start = datetime(2026, 7, 14, 8, 0, 0)
    rows: list[dict[str, str | int | float]] = []
    event_number = 1

    for point_index, (point, spec) in enumerate(POINTS.items()):
        for i in range(int(spec["count"])):
            is_hidden_risk = point == "P03" and i >= int(spec["count"]) - 36
            progress = (i - (int(spec["count"]) - 36) + 1) / 36 if is_hidden_risk else 0.0

            torque = float(spec["torque"]) + rng.gauss(0, 0.62)
            angle_sigma = 2.15
            retry = 1 if rng.random() < 0.012 else 0
            current = 6.1 + point_index * 0.16 + rng.gauss(0, 0.12)
            cycle = 4.65 + point_index * 0.08 + rng.gauss(0, 0.10)

            if is_hidden_risk:
                torque += progress * 1.62
                angle_sigma = 2.15 + progress * 3.9
                retry = 1 if rng.random() < (0.08 + progress * 0.24) else 0
                current += progress * 0.34
                cycle += progress * 0.22

            angle = float(spec["angle"]) + rng.gauss(0, angle_sigma)
            batch_number = 1 + i // 55
            timestamp = start + timedelta(minutes=i * 2 + point_index)
            calibration_days = max(7, 45 - i // 6) if point == "P03" else 38
            row = {
                "event_id": f"EVT-{event_number:05d}",
                "timestamp": timestamp.isoformat(timespec="seconds"),
                "station_id": "ST-FAS-07",
                "tool_id": "TOOL-TG-07",
                "controller_id": "CTRL-TG-07",
                "model_code": "DEMO-A",
                "program_id": "PGM-A-07",
                "fastening_point": point,
                "batch_id": f"BATCH-DEMO-{batch_number:02d}",
                "shift": "A" if timestamp.hour < 16 else "B",
                "torque_nm": round(torque, 3),
                "angle_deg": round(angle, 3),
                "current_a": round(current, 3),
                "cycle_time_s": round(cycle, 3),
                "retry_count": retry,
                "result": "OK",
                "alarm_code": "",
                "calibration_days_remaining": calibration_days,
                "scenario_label": "hidden_risk" if is_hidden_risk else "normal",
            }
            rows.append(row)
            event_number += 1

    rows.sort(key=lambda row: str(row["timestamp"]))
    return rows


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    rows = generate()
    csv_path = DATA_DIR / "tightening_events_demo.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

    manifest = {
        "dataset": csv_path.name,
        "generated_by": "scripts/generate_demo_data.py",
        "seed": 20260717,
        "records": len(rows),
        "points": list(POINTS),
        "real_personal_data": False,
        "seres_internal_data": False,
        "primary_scenario": {
            "id": "hidden_torque_drift",
            "point": "P03",
            "description": "扭矩仍在规格内，但均值偏移、角度离散和重试率同步上升。",
        },
    }
    (DATA_DIR / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"generated {len(rows)} rows -> {csv_path}")


if __name__ == "__main__":
    main()
