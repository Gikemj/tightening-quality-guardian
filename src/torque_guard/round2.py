"""Second-round relation-evidence agent.

The competition data package intentionally contains no production time series,
free-text work orders, personal data, or original technical documents.  This
module therefore treats a case as a relationship-review task, not as a model
training or root-cause prediction problem.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping


EVIDENCE_STRENGTHS = {"direct", "candidate", "gap"}
RELATION_TIERS = {
    "仅工单结构记录",
    "设备-失效模式关联候选",
    "内容较完整候选",
}


@dataclass(frozen=True)
class RelationEvidence:
    evidence_id: str
    label: str
    strength: str
    source_scope: str
    relation: str
    detail: str

    def __post_init__(self) -> None:
        if self.strength not in EVIDENCE_STRENGTHS:
            raise ValueError("关系证据强度必须为 direct、candidate 或 gap")
        if not all(
            isinstance(value, str) and value.strip()
            for value in (
                self.evidence_id,
                self.label,
                self.source_scope,
                self.relation,
                self.detail,
            )
        ):
            raise ValueError("关系证据字段必须为非空字符串")


@dataclass(frozen=True)
class TaskDraft:
    task_id: str
    title: str
    owner_role: str
    due_hint: str
    acceptance_criteria: str
    evidence_ids: tuple[str, ...]
    approval_required: bool = True

    def __post_init__(self) -> None:
        if not self.approval_required:
            raise ValueError("第二轮任务草案必须保留人工审批")
        if not self.evidence_ids:
            raise ValueError("任务草案必须关联至少一条证据")


@dataclass(frozen=True)
class CaseInput:
    """A public, synthetic representative record following the supplied schema."""

    case_id: str
    title: str
    equipment_family: str
    component_family: str
    source_group: str
    severity_band: str
    status_group: str
    phenomenon_category: str
    cause_category: str
    action_category: str
    outcome_group: str
    relation_tier: str
    structure_link_status: str
    failure_mode_link_status: str
    completeness_grade: str
    has_valid_equipment_link: bool
    has_failure_mode_link: bool
    is_synthetic: bool

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "CaseInput":
        required = {
            "case_id",
            "title",
            "equipment_family",
            "component_family",
            "source_group",
            "severity_band",
            "status_group",
            "phenomenon_category",
            "cause_category",
            "action_category",
            "outcome_group",
            "relation_tier",
            "structure_link_status",
            "failure_mode_link_status",
            "completeness_grade",
            "has_valid_equipment_link",
            "has_failure_mode_link",
            "is_synthetic",
        }
        missing = required - set(raw)
        if missing:
            raise ValueError(f"案例缺少字段：{', '.join(sorted(missing))}")
        case = cls(**{field: raw[field] for field in required})
        case.validate()
        return case

    def validate(self) -> None:
        if self.relation_tier not in RELATION_TIERS:
            raise ValueError("relation_tier 不是数据包定义的受控取值")
        if self.is_synthetic is not True:
            raise ValueError("公开演示只允许使用合成代表案例")
        if not self.case_id.startswith("CASE-DEMO-"):
            raise ValueError("公开案例 ID 必须使用 CASE-DEMO- 前缀")
        for field, value in asdict(self).items():
            if field in {
                "has_valid_equipment_link",
                "has_failure_mode_link",
                "is_synthetic",
            }:
                continue
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field} 必须是非空字符串")


@dataclass(frozen=True)
class RelationAssessment:
    case: CaseInput
    disposition: str
    confidence_label: str
    facts: tuple[RelationEvidence, ...]
    gaps: tuple[RelationEvidence, ...]
    tasks: tuple[TaskDraft, ...]
    boundary: str
    terra_mode: str = "deterministic"
    terra_note: str = "公开演示未调用外部模型。"

    def to_dict(self) -> dict[str, Any]:
        evidence = [asdict(item) for item in (*self.facts, *self.gaps)]
        known = {item["evidence_id"] for item in evidence}
        for task in self.tasks:
            if not set(task.evidence_ids).issubset(known):
                raise ValueError("任务引用了不存在的关系证据")
        return {
            "schema_version": "2.0",
            "data_boundary": "schema_and_relationship_reference_only",
            "case": asdict(self.case),
            "disposition": self.disposition,
            "confidence_label": self.confidence_label,
            "facts": [asdict(item) for item in self.facts],
            "gaps": [asdict(item) for item in self.gaps],
            "tasks": [
                {**asdict(item), "evidence_ids": list(item.evidence_ids)}
                for item in self.tasks
            ],
            "boundary": self.boundary,
            "terra": {"mode": self.terra_mode, "note": self.terra_note},
        }


class RelationEvidenceAgent:
    """Build an evidence-bounded review dossier from a structured case."""

    boundary = (
        "该结果只核验对象关系与字段完整性，不从脱敏包推断真实设备状态、"
        "故障概率、质量影响范围或已确认根因。"
    )

    def assess(self, case: CaseInput) -> RelationAssessment:
        case.validate()
        facts: list[RelationEvidence] = [
            RelationEvidence(
                "E-CASE-01",
                "工单结构已接收",
                "direct",
                "故障工单受控字段",
                "工单 → 设备对象",
                f"来源为{case.source_group}；当前状态为{case.status_group}。",
            ),
            RelationEvidence(
                "E-CASE-02",
                "设备类别与功能可作为检索入口",
                "direct" if case.has_valid_equipment_link else "gap",
                "设备实例、设备类别、功能关系",
                "设备实例 → 设备类别 → 功能",
                (
                    f"设备族为{case.equipment_family}，部件族为{case.component_family}。"
                    if case.has_valid_equipment_link
                    else "工单未形成有效设备对象关联，不能进入功能和失效模式检索。"
                ),
            ),
        ]
        gaps: list[RelationEvidence] = []

        if case.has_failure_mode_link:
            facts.append(
                RelationEvidence(
                    "E-CASE-03",
                    "失效模式为历史关联候选",
                    "candidate",
                    "故障工单、失效模式",
                    "工单 → 失效模式",
                    f"关联状态为{case.failure_mode_link_status}，需业务人员核验，不视作已确认失效模式。",
                )
            )
        else:
            gaps.append(
                RelationEvidence(
                    "G-CASE-03",
                    "缺少失效模式关联",
                    "gap",
                    "故障工单、失效模式",
                    "工单 ↛ 失效模式",
                    "当前记录不能直接复用历史处置结论，需要先补充现象、检查结果和关联依据。",
                )
            )

        if case.relation_tier == "内容较完整候选":
            confidence = "可进入跨角色复核"
            disposition = "review_with_candidate_knowledge"
        elif case.relation_tier == "设备-失效模式关联候选":
            confidence = "仅供检索与补证"
            disposition = "verify_relation_before_knowledge_reuse"
        else:
            confidence = "关系不足，先补齐工单"
            disposition = "complete_case_before_reasoning"
            gaps.append(
                RelationEvidence(
                    "G-CASE-04",
                    "关联层级不足",
                    "gap",
                    "关联概览",
                    "工单关系层级",
                    "当前仅保留工单结构记录，不能据此生成候选根因或质量结论。",
                )
            )

        if case.completeness_grade in {"低", "C"}:
            gaps.append(
                RelationEvidence(
                    "G-CASE-05",
                    "工单字段需要补齐",
                    "gap",
                    "故障工单字段完整度",
                    "现象、原因、处置、结果",
                    "现有记录缺少可用于复机确认或知识回写的字段，需保留缺失状态而非补造内容。",
                )
            )

        task_evidence = tuple(item.evidence_id for item in (*facts, *gaps))
        tasks = self._tasks_for(case, disposition, task_evidence)
        return RelationAssessment(
            case=case,
            disposition=disposition,
            confidence_label=confidence,
            facts=tuple(facts),
            gaps=tuple(gaps),
            tasks=tasks,
            boundary=self.boundary,
        )

    @staticmethod
    def _tasks_for(
        case: CaseInput, disposition: str, evidence_ids: tuple[str, ...]
    ) -> tuple[TaskDraft, ...]:
        tasks: list[TaskDraft] = [
            TaskDraft(
                "TASK-REL-01",
                "核验设备对象与工单归属",
                "设备管理员",
                "建议在当班内完成",
                "确认工单对象、设备类别与部件对象的关联状态，并记录核验依据。",
                ("E-CASE-01", "E-CASE-02"),
            )
        ]
        if disposition != "complete_case_before_reasoning":
            tasks.append(
                TaskDraft(
                    "TASK-REL-02",
                    "核验失效模式关联是否可复用",
                    "维修工程师",
                    "建议在现场检查后完成",
                    "记录检查现象、排查路径和是否同意复用历史失效模式；不同意时说明差异。",
                    tuple(item for item in evidence_ids if item in {"E-CASE-02", "E-CASE-03"}),
                )
            )
        else:
            tasks.append(
                TaskDraft(
                    "TASK-REL-02",
                    "补齐工单事实字段",
                    "现场报修人",
                    "建议在首次处置前完成",
                    "补充可观察现象、检查依据和当前处置状态；未知项明确标记为未确认。",
                    tuple(item for item in evidence_ids if item.startswith("G-")),
                )
            )
        tasks.append(
            TaskDraft(
                "TASK-REL-03",
                "评估是否需要质量与工艺共同复核",
                "质量工程师",
                "建议在设备复机前完成",
                "按异常发生区间、生产批次和检查结果判断是否需要扩大影响范围核验。",
                ("E-CASE-01",),
            )
        )
        return tuple(tasks)
