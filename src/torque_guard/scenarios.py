from __future__ import annotations

import random
from datetime import datetime, timedelta
from typing import Any


SCENARIOS = (
    "normal",
    "hidden_torque_drift",
    "sensor_zero_drift",
    "repeated_alarm",
)

EXPECTED_PRIMARY_CAUSE = {
    "hidden_torque_drift": "套筒或批头磨损",
    "sensor_zero_drift": "工具标定漂移",
    "repeated_alarm": "套筒或批头磨损",
}


def generate_independent_case(
    scenario: str,
    *,
    seed: int,
    strength: float = 1.0,
    count: int = 148,
) -> list[dict[str, Any]]:
    """Generate one independent, deterministic P03 case for offline validation.

    Each returned case has its own baseline and current window.  It avoids the
    overlapping-window inflation of the original single-series demonstration.
    The data remains synthetic and must never be described as factory evidence.
    """

    if scenario not in SCENARIOS:
        raise ValueError(f"未知验证场景：{scenario}")
    if count < 124:
        raise ValueError("独立案例至少需要 124 条记录")
    rng = random.Random(seed)
    start = datetime(2026, 7, 20, 8, 0, 0) + timedelta(days=seed % 17)
    rows: list[dict[str, Any]] = []
    recent_start = count - 24

    for index in range(count):
        is_recent = index >= recent_start
        progress = (index - recent_start + 1) / 24 if is_recent else 0.0
        torque = 48.0 + rng.gauss(0, 0.62)
        angle_sigma = 2.15
        retry_probability = 0.01
        current = 6.42 + rng.gauss(0, 0.12)
        cycle = 4.82 + rng.gauss(0, 0.10)
        alarm_code = ""
        calibration_days = 32

        if is_recent and scenario == "hidden_torque_drift":
            torque += strength * (0.55 + progress * 0.85)
            angle_sigma = 2.15 + strength * (1.2 + progress * 2.2)
            retry_probability = min(0.38, 0.07 + progress * 0.22 * strength)
            current += progress * 0.28 * strength
            cycle += progress * 0.18 * strength
        elif is_recent and scenario == "sensor_zero_drift":
            torque += strength * (0.62 + progress * 0.60)
            calibration_days = max(1, 8 - int(progress * 4))
            if index % 5 == 0 or index == count - 1:
                alarm_code = "ALM-314"
        elif is_recent and scenario == "repeated_alarm":
            angle_sigma = 2.15 + 1.9 * strength
            retry_probability = min(0.45, 0.18 + 0.14 * strength)
            if index % 3 == 0 or index >= count - 3:
                alarm_code = "ALM-507"

        retry = 1 if rng.random() < retry_probability else 0
        angle = 95.0 + rng.gauss(0, angle_sigma)
        timestamp = start + timedelta(minutes=index * 2)
        rows.append(
            {
                "event_id": f"CASE-{seed:05d}-{index + 1:03d}",
                "timestamp": timestamp.isoformat(timespec="seconds"),
                "station_id": "ST-FAS-07",
                "tool_id": "TOOL-TG-07",
                "controller_id": "CTRL-TG-07",
                "model_code": "DEMO-A",
                "program_id": "PGM-A-07",
                "fastening_point": "P03",
                "batch_id": f"CASE-BATCH-{seed:05d}",
                "shift": "A",
                "torque_nm": round(max(43.05, min(52.95, torque)), 3),
                "angle_deg": round(angle, 3),
                "current_a": round(current, 3),
                "cycle_time_s": round(cycle, 3),
                "retry_count": retry,
                "result": "OK",
                "alarm_code": alarm_code,
                "calibration_days_remaining": calibration_days,
                "scenario_label": scenario if is_recent else "normal",
            }
        )
    return rows
