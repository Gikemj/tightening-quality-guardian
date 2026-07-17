from __future__ import annotations

from pathlib import Path

from .models import RiskCard
from .risk import RiskAnalyzer, read_events


class DigitalEmployee:
    """Event-driven orchestrator with an auditable tool trace.

    It does not change PLC parameters or make an automatic stop-line decision.
    The last step always routes a review task to an accountable engineer.
    """

    def __init__(self, knowledge_root: str | Path):
        self.analyzer = RiskAnalyzer(knowledge_root)

    def run(self, event_file: str | Path, fastening_point: str) -> RiskCard:
        events = read_events(event_file)
        card = self.analyzer.analyze(events, fastening_point)
        card.agent_trace = [
            {"step": "sense", "tool": "read_events", "result": f"读取 {len(events)} 条合成拧紧记录"},
            {"step": "detect", "tool": "spc_rules", "result": "检查过程中心、趋势、离散与重试变化"},
            {"step": "retrieve", "tool": "knowledge_subgraph", "result": "检索 PFMEA、控制计划和相似历史案例"},
            {"step": "reason", "tool": "risk_analyzer", "result": f"形成 {card.risk_level} 风险卡，评分 {card.risk_score}"},
            {"step": "act", "tool": "task_payload", "result": "生成待人工确认的点检、抽检和追溯任务"},
        ]
        return card
