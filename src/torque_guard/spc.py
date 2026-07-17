from __future__ import annotations

from dataclasses import dataclass
from statistics import fmean, pstdev
from typing import Iterable


@dataclass(frozen=True)
class RuleHit:
    rule_id: str
    name: str
    detail: str
    indices: list[int]


def safe_mean(values: Iterable[float]) -> float:
    items = list(values)
    return fmean(items) if items else 0.0


def safe_std(values: Iterable[float]) -> float:
    items = list(values)
    if len(items) < 2:
        return 0.0
    return pstdev(items)


def _same_side(values: list[float], center: float) -> bool:
    return all(value > center for value in values) or all(value < center for value in values)


def western_electric_rules(values: list[float], center: float, sigma: float) -> list[RuleHit]:
    """Return a compact set of SPC trend-rule hits for the latest sequence.

    The implementation is deterministic and intentionally transparent. It is
    used to flag a process that is shifting before measurements cross a product
    specification limit.
    """

    if not values or sigma <= 0:
        return []

    hits: list[RuleHit] = []
    latest = values[-1]
    latest_index = len(values) - 1

    if abs(latest - center) >= 3 * sigma:
        hits.append(
            RuleHit("WE-01", "单点超出 3σ", f"最新点偏离中心线 {abs(latest-center)/sigma:.2f}σ", [latest_index])
        )

    if len(values) >= 3:
        window = values[-3:]
        high = [i for i, value in enumerate(window) if value - center >= 2 * sigma]
        low = [i for i, value in enumerate(window) if center - value >= 2 * sigma]
        selected = high if len(high) >= 2 else low
        if len(selected) >= 2:
            base = len(values) - 3
            hits.append(
                RuleHit("WE-02", "连续 3 点中至少 2 点超出同侧 2σ", "过程均值出现显著同向偏移", [base + i for i in selected])
            )

    if len(values) >= 5:
        window = values[-5:]
        high = [i for i, value in enumerate(window) if value - center >= sigma]
        low = [i for i, value in enumerate(window) if center - value >= sigma]
        selected = high if len(high) >= 4 else low
        if len(selected) >= 4:
            base = len(values) - 5
            hits.append(
                RuleHit("WE-03", "连续 5 点中至少 4 点超出同侧 1σ", "过程正在持续偏离基线", [base + i for i in selected])
            )

    if len(values) >= 8 and _same_side(values[-8:], center):
        hits.append(
            RuleHit("WE-04", "连续 8 点位于中心线同侧", "规格仍可能合格，但过程中心已经偏移", list(range(len(values) - 8, len(values))))
        )

    if len(values) >= 6:
        window = values[-6:]
        increasing = all(a < b for a, b in zip(window, window[1:]))
        decreasing = all(a > b for a, b in zip(window, window[1:]))
        if increasing or decreasing:
            direction = "上升" if increasing else "下降"
            hits.append(
                RuleHit("TR-06", "连续 6 点单向变化", f"最新序列持续{direction}", list(range(len(values) - 6, len(values))))
            )

    return hits


def capability_snapshot(values: list[float], lower: float, upper: float) -> dict[str, float]:
    mean = safe_mean(values)
    sigma = safe_std(values)
    if sigma <= 0:
        return {"mean": mean, "sigma": sigma, "cp": 0.0, "cpk": 0.0}
    cp = (upper - lower) / (6 * sigma)
    cpk = min((upper - mean) / (3 * sigma), (mean - lower) / (3 * sigma))
    return {"mean": mean, "sigma": sigma, "cp": cp, "cpk": cpk}
