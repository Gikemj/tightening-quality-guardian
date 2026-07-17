from __future__ import annotations

from typing import Any

from ..models import RiskCard


def build_bitable_records(card: RiskCard) -> list[dict[str, Any]]:
    """Build Feishu Bitable-shaped records without transmitting them."""

    return [
        {
            "fields": {
                "风险卡编号": card.card_id,
                "工位": card.station_id,
                "工具": card.tool_id,
                "紧固点": card.fastening_point,
                "风险等级": card.risk_level,
                "风险评分": card.risk_score,
                "状态": "待工程师确认",
                "证据数量": len(card.evidence),
                "窗口开始": card.affected_scope["window_start"],
                "窗口结束": card.affected_scope["window_end"],
            }
        },
        *[
            {
                "fields": {
                    "风险卡编号": card.card_id,
                    "任务编号": action.action_id,
                    "任务": action.title,
                    "责任角色": action.owner_role,
                    "时限（分钟）": action.due_minutes,
                    "需人工审批": action.approval_required,
                    "验收依据": action.acceptance_criteria,
                }
            }
            for action in card.recommended_actions
        ],
    ]
