from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, replace
from importlib import resources
from typing import Any, Mapping, Protocol, Sequence

from .models import CandidateCause, Evidence, RiskCard


PROMPT_VERSION = "1.0"
SCHEMA_VERSION = "1.0"


def load_system_prompt() -> str:
    return (
        resources.files("torque_guard.prompts")
        .joinpath("system_prompt.txt")
        .read_text(encoding="utf-8")
    )


def load_output_schema() -> dict[str, Any]:
    raw = (
        resources.files("torque_guard.prompts")
        .joinpath("reasoning_output.schema.json")
        .read_text(encoding="utf-8")
    )
    return json.loads(raw)


@dataclass(frozen=True)
class ReasoningRequest:
    card_id: str
    risk_level: str
    risk_score: int
    observed_facts: tuple[str, ...]
    evidence: tuple[Evidence, ...]
    proposed_causes: tuple[CandidateCause, ...]
    attribution_required: bool = True

    @classmethod
    def from_card(cls, card: RiskCard) -> "ReasoningRequest":
        return cls(
            card_id=card.card_id,
            risk_level=card.risk_level,
            risk_score=card.risk_score,
            observed_facts=tuple(card.observed_facts),
            evidence=tuple(card.evidence),
            proposed_causes=tuple(card.candidate_causes),
            attribution_required=card.analysis_provenance["attribution_required"],
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "risk_card": {
                "card_id": self.card_id,
                "risk_level": self.risk_level,
                "risk_score": self.risk_score,
                "observed_facts": list(self.observed_facts),
                "attribution_required": self.attribution_required,
                "proposed_causes": [
                    {
                        "cause": item.cause,
                        "confidence": item.confidence,
                        "verification": item.verification,
                    }
                    for item in self.proposed_causes
                ],
            },
            "evidence": [asdict(item) for item in self.evidence],
        }


@dataclass(frozen=True)
class ReasoningClaim:
    text: str
    evidence_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"text": self.text, "evidence_ids": list(self.evidence_ids)}


@dataclass(frozen=True)
class HypothesisAssessment:
    cause: str
    confidence: str
    evidence_ids: tuple[str, ...]
    verification: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "cause": self.cause,
            "confidence": self.confidence,
            "evidence_ids": list(self.evidence_ids),
            "verification": self.verification,
        }


@dataclass(frozen=True)
class ReasoningResult:
    decision: str
    conclusion: ReasoningClaim | None
    hypotheses: tuple[HypothesisAssessment, ...]
    uncertainty: str
    refusal_reason: str | None
    reasoner_mode: str
    disposition: str
    model: str | None = None
    fallback_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "prompt_version": PROMPT_VERSION,
            "decision": self.decision,
            "disposition": self.disposition,
            "conclusion": self.conclusion.to_dict() if self.conclusion else None,
            "hypotheses": [item.to_dict() for item in self.hypotheses],
            "uncertainty": self.uncertainty,
            "refusal_reason": self.refusal_reason,
            "safety": {
                "requires_human_approval": self.disposition != "no_attribution_required",
                "automatic_action_allowed": False,
            },
            "provenance": {
                "reasoner_mode": self.reasoner_mode,
                "model": self.model,
                "fallback_reason": self.fallback_reason,
            },
        }


@dataclass(frozen=True)
class EvidencePolicy:
    minimum_total: int = 2
    minimum_direct: int = 1
    require_knowledge_evidence: bool = True

    def refusal_reason(self, evidence: Sequence[Evidence]) -> str | None:
        unique = {item.evidence_id: item for item in evidence if item.evidence_id.strip()}
        direct_count = sum(item.strength == "direct" for item in unique.values())
        knowledge_count = sum(
            item.category in {"pfmea", "control_plan", "history"}
            for item in unique.values()
        )
        missing: list[str] = []
        if len(unique) < self.minimum_total:
            missing.append(f"有效证据少于 {self.minimum_total} 条")
        if direct_count < self.minimum_direct:
            missing.append(f"直接测量证据少于 {self.minimum_direct} 条")
        if self.require_knowledge_evidence and knowledge_count == 0:
            missing.append("缺少 PFMEA、控制计划或历史案例证据")
        return "；".join(missing) if missing else None


class StructuredReasoner(Protocol):
    def reason(self, request: ReasoningRequest) -> ReasoningResult:
        """Return schema-shaped, evidence-cited reasoning."""


class DeterministicReasoner:
    """Safe offline reasoner; deterministic and fully inspectable by default."""

    def __init__(self, *, evidence_policy: EvidencePolicy | None = None) -> None:
        self.evidence_policy = evidence_policy or EvidencePolicy()

    def reason(self, request: ReasoningRequest) -> ReasoningResult:
        if not request.attribution_required:
            return ReasoningResult(
                decision="refused",
                conclusion=None,
                hypotheses=(),
                uncertainty=(
                    "稳定判定仅适用于当前分析分层与时间窗口；后续继续按常规频率监控。"
                ),
                refusal_reason="当前窗口稳定且未触发异常信号，无需根因归因",
                reasoner_mode="deterministic",
                disposition="no_attribution_required",
            )
        refusal = self.evidence_policy.refusal_reason(request.evidence)
        if refusal:
            return ReasoningResult(
                decision="refused",
                conclusion=None,
                hypotheses=(),
                uncertainty="现有证据未达到受控研判门槛，保留风险并转人工补充证据。",
                refusal_reason=refusal,
                reasoner_mode="deterministic",
                disposition="insufficient_evidence",
            )

        evidence_by_id = {item.evidence_id: item for item in request.evidence}
        direct_ids = self._select_ids(request.evidence, strengths={"direct"}, limit=3)
        knowledge_ids = self._select_ids(
            request.evidence,
            categories={"pfmea", "control_plan", "history"},
            limit=2,
        )
        conclusion_ids = tuple(dict.fromkeys((*direct_ids, *knowledge_ids)))
        if not conclusion_ids:
            # The policy normally catches this.  Keep the branch explicit so a
            # future policy change cannot create an uncited conclusion.
            return ReasoningResult(
                decision="refused",
                conclusion=None,
                hypotheses=(),
                uncertainty="没有可引用的证据 ID，不能形成风险归因。",
                refusal_reason="没有可引用的 evidence_id",
                reasoner_mode="deterministic",
                disposition="insufficient_evidence",
            )

        conclusion = ReasoningClaim(
            text=(
                f"风险卡评分为 {request.risk_score}（{request.risk_level}）；"
                "过程与设备信号存在需要优先核验的同向变化，并与质量知识中的失效链相关。"
                "该结果只支持生成待验证假设，不代表根因已经确认。"
            ),
            evidence_ids=conclusion_ids,
        )
        hypotheses = tuple(
            self._assess_hypothesis(item, evidence_by_id) for item in request.proposed_causes
        )
        hypotheses = tuple(item for item in hypotheses if item.evidence_ids)
        if not hypotheses:
            return ReasoningResult(
                decision="refused",
                conclusion=None,
                hypotheses=(),
                uncertainty="证据能够说明风险存在，但不能支撑任何候选原因。",
                refusal_reason="候选原因缺少可追溯 evidence_id",
                reasoner_mode="deterministic",
                disposition="insufficient_evidence",
            )
        return ReasoningResult(
            decision="supported",
            conclusion=conclusion,
            hypotheses=hypotheses,
            uncertainty=(
                "以上原因均为待验证假设；必须由工程师完成现场点检、抽检或标定检查后，"
                "才能确认原因并决定处置。"
            ),
            refusal_reason=None,
            reasoner_mode="deterministic",
            disposition="attribution_hypotheses_supported",
        )

    @staticmethod
    def _select_ids(
        evidence: Sequence[Evidence],
        *,
        categories: set[str] | None = None,
        strengths: set[str] | None = None,
        limit: int = 3,
    ) -> tuple[str, ...]:
        selected: list[str] = []
        for item in evidence:
            if categories is not None and item.category not in categories:
                continue
            if strengths is not None and item.strength not in strengths:
                continue
            if item.evidence_id not in selected:
                selected.append(item.evidence_id)
            if len(selected) >= limit:
                break
        return tuple(selected)

    def _assess_hypothesis(
        self, cause: CandidateCause, evidence_by_id: Mapping[str, Evidence]
    ) -> HypothesisAssessment:
        evidence = tuple(evidence_by_id.values())
        if "标定" in cause.cause or "传感器" in cause.cause:
            preferred_categories = ("alarm", "equipment", "spc", "control_plan", "pfmea")
        elif "套筒" in cause.cause or "批头" in cause.cause:
            preferred_categories = ("alarm", "equipment", "pfmea", "history")
        elif "批次" in cause.cause:
            preferred_categories = ("equipment", "spc", "pfmea", "history")
        else:
            preferred_categories = ("spc", "equipment", "pfmea", "control_plan", "history")
        selected: list[str] = []
        for category in preferred_categories:
            for item in evidence:
                if item.category == category and item.evidence_id not in selected:
                    selected.append(item.evidence_id)
                    break
            if len(selected) >= 3:
                break
        evidence_ids = tuple(selected)
        return HypothesisAssessment(
            cause=cause.cause,
            confidence=cause.confidence,
            evidence_ids=evidence_ids,
            verification=cause.verification,
        )


@dataclass(frozen=True)
class ExternalModelConfig:
    """Non-secret external model settings; credentials stay in the environment."""

    enabled: bool = False
    provider: str = ""
    model: str = ""
    api_key_env: str = "TORQUE_GUARD_MODEL_API_KEY"


class StructuredModelClient(Protocol):
    def complete(
        self,
        *,
        system_prompt: str,
        payload: Mapping[str, Any],
        output_schema: Mapping[str, Any],
        api_key: str,
        provider: str,
        model: str,
    ) -> Mapping[str, Any]:
        """Perform one real structured-output request in an injected adapter."""


class SafeConfiguredReasoner:
    """Use an injected external client only when configuration is complete.

    No network SDK is bundled and this class never claims an external call when
    it falls back.  Invalid, unsafe or uncited model output is discarded and
    replaced by the deterministic result with an explicit fallback reason.
    """

    def __init__(
        self,
        config: ExternalModelConfig,
        *,
        client: StructuredModelClient | None = None,
        fallback: DeterministicReasoner | None = None,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self.config = config
        self.client = client
        self.fallback = fallback or DeterministicReasoner()
        self.env = os.environ if env is None else env

    def reason(self, request: ReasoningRequest) -> ReasoningResult:
        if not request.attribution_required:
            # A stable window is decided by the deterministic scoring contract;
            # never spend credentials or invite an external model to invent a cause.
            return self.fallback.reason(request)
        if not self.config.enabled:
            return self._fallback(request, "external_model_disabled")
        api_key = self.env.get(self.config.api_key_env, "").strip()
        if not api_key:
            return self._fallback(request, "external_api_key_missing")
        if self.client is None:
            return self._fallback(request, "external_client_not_provided")
        if not self.config.provider.strip() or not self.config.model.strip():
            return self._fallback(request, "external_provider_or_model_missing")
        refusal = self.fallback.evidence_policy.refusal_reason(request.evidence)
        if refusal:
            return replace(
                self.fallback.reason(request),
                fallback_reason="evidence_policy_refused_before_external_call",
            )
        try:
            raw = self.client.complete(
                system_prompt=load_system_prompt(),
                payload=request.to_payload(),
                output_schema=load_output_schema(),
                api_key=api_key,
                provider=self.config.provider,
                model=self.config.model,
            )
            return self._parse_and_validate(raw, request)
        except Exception as exc:
            return self._fallback(request, f"external_output_rejected:{type(exc).__name__}")

    def _fallback(self, request: ReasoningRequest, reason: str) -> ReasoningResult:
        return replace(self.fallback.reason(request), fallback_reason=reason)

    def _parse_and_validate(
        self, raw: Mapping[str, Any], request: ReasoningRequest
    ) -> ReasoningResult:
        known = {item.evidence_id for item in request.evidence}
        if raw.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("reasoning schema_version 不匹配")
        decision = str(raw.get("decision", ""))
        if decision not in {"supported", "refused"}:
            raise ValueError("decision 非法")
        safety = raw.get("safety")
        if not isinstance(safety, Mapping):
            raise ValueError("缺少 safety")
        if safety.get("requires_human_approval") is not True:
            raise ValueError("外部输出试图绕过人工审批")
        if safety.get("automatic_action_allowed") is not False:
            raise ValueError("外部输出请求自动执行动作")

        if decision == "refused":
            reason = str(raw.get("refusal_reason") or "").strip()
            if not reason:
                raise ValueError("拒答结果缺少 refusal_reason")
            return ReasoningResult(
                decision="refused",
                conclusion=None,
                hypotheses=(),
                uncertainty=str(raw.get("uncertainty") or "证据不足，转人工复核。"),
                refusal_reason=reason,
                reasoner_mode=f"external:{self.config.provider}",
                disposition="insufficient_evidence",
                model=self.config.model,
            )

        conclusion_raw = raw.get("conclusion")
        if not isinstance(conclusion_raw, Mapping):
            raise ValueError("支持性输出缺少 conclusion")
        conclusion_ids = self._validate_citations(conclusion_raw.get("evidence_ids"), known)
        conclusion_text = str(conclusion_raw.get("text") or "").strip()
        if not conclusion_text:
            raise ValueError("conclusion.text 为空")
        self._reject_unsafe_assertions(conclusion_text)

        hypotheses_raw = raw.get("hypotheses")
        if not isinstance(hypotheses_raw, list) or not hypotheses_raw:
            raise ValueError("支持性输出缺少 hypotheses")
        hypotheses: list[HypothesisAssessment] = []
        for item in hypotheses_raw:
            if not isinstance(item, Mapping):
                raise ValueError("hypothesis 必须是对象")
            evidence_ids = self._validate_citations(item.get("evidence_ids"), known)
            cause = str(item.get("cause") or "").strip()
            verification = str(item.get("verification") or "").strip()
            confidence = str(item.get("confidence") or "")
            if not cause or not verification:
                raise ValueError("hypothesis 字段为空")
            self._reject_unsafe_assertions(cause, verification)
            if confidence not in {"low", "medium", "medium-high", "high"}:
                raise ValueError("hypothesis.confidence 非法")
            hypotheses.append(
                HypothesisAssessment(cause, confidence, evidence_ids, verification)
            )
        uncertainty = str(raw.get("uncertainty") or "").strip()
        if not uncertainty:
            raise ValueError("uncertainty 为空")
        self._reject_unsafe_assertions(uncertainty)
        return ReasoningResult(
            decision="supported",
            conclusion=ReasoningClaim(conclusion_text, conclusion_ids),
            hypotheses=tuple(hypotheses),
            uncertainty=uncertainty,
            refusal_reason=None,
            reasoner_mode=f"external:{self.config.provider}",
            disposition="attribution_hypotheses_supported",
            model=self.config.model,
        )

    @staticmethod
    def _validate_citations(value: Any, known: set[str]) -> tuple[str, ...]:
        if not isinstance(value, list) or not value:
            raise ValueError("每条推理必须引用 evidence_id")
        ids = tuple(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))
        if not ids or any(item not in known for item in ids):
            raise ValueError("推理引用了未知 evidence_id")
        return ids

    @staticmethod
    def _reject_unsafe_assertions(*texts: str) -> None:
        combined = " ".join(texts).lower().replace(" ", "")
        prohibited = (
            "已确认根因",
            "根因就是",
            "无需验证",
            "自动停线",
            "修改plc",
            "修改工具参数",
            "confirmedrootcause",
            "noverificationrequired",
        )
        if any(term in combined for term in prohibited):
            raise ValueError("外部输出包含越权或未经验证的断言")


def build_reasoner(
    config: ExternalModelConfig | None = None,
    *,
    client: StructuredModelClient | None = None,
    env: Mapping[str, str] | None = None,
) -> StructuredReasoner:
    if config is None:
        return DeterministicReasoner()
    return SafeConfiguredReasoner(config, client=client, env=env)


def apply_reasoning(card: RiskCard, result: ReasoningResult) -> RiskCard:
    """Project a validated structured result onto the backward-compatible card."""

    card.reasoning = result.to_dict()
    if result.decision == "refused" or result.conclusion is None:
        card.inference = (
            "当前窗口稳定且未触发异常信号，无需启动根因归因；保持常规监控。"
            if result.disposition == "no_attribution_required"
            else f"证据不足，受控研判器拒绝形成候选根因：{result.refusal_reason}"
        )
        card.uncertainty = result.uncertainty
        card.candidate_causes = []
        # Without a supported candidate hypothesis there is no honest target
        # for the action contract's candidate_causes references.  Keep the
        # risk under human review, but do not manufacture an executable task.
        card.recommended_actions = []
        return card

    citations = ", ".join(result.conclusion.evidence_ids)
    card.inference = f"{result.conclusion.text} [证据：{citations}]"
    card.uncertainty = result.uncertainty
    evidence_by_id = {item.evidence_id: item for item in card.evidence}
    card.candidate_causes = [
        CandidateCause(
            cause=item.cause,
            confidence=item.confidence,
            basis=[
                f"{evidence_by_id[evidence_id].title} [{evidence_id}]"
                for evidence_id in item.evidence_ids
            ],
            verification=item.verification,
            evidence_ids=list(item.evidence_ids),
        )
        for item in result.hypotheses
    ]
    return card
