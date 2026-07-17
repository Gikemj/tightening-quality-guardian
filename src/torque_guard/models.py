from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class Evidence:
    evidence_id: str
    category: str
    title: str
    observation: str
    source: str
    locator: str
    strength: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CandidateCause:
    cause: str
    confidence: str
    basis: list[str]
    verification: str


@dataclass(frozen=True)
class Action:
    action_id: str
    title: str
    owner_role: str
    due_minutes: int
    approval_required: bool
    acceptance_criteria: str


@dataclass
class RiskCard:
    card_id: str
    created_at: str
    station_id: str
    tool_id: str
    fastening_point: str
    risk_level: str
    risk_score: int
    status: str
    observed_facts: list[str]
    inference: str
    uncertainty: str
    affected_scope: dict[str, Any]
    score_breakdown: dict[str, int]
    evidence: list[Evidence]
    candidate_causes: list[CandidateCause]
    recommended_actions: list[Action]
    agent_trace: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
