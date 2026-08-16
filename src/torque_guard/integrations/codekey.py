"""Guarded OpenAI-compatible client for the optional administrator reasoner.

This integration is intentionally server-side only.  A browser bundle must
never contain a provider key.  The client receives a minimized relationship
dossier and may draft wording or clarification questions, never an approval,
root-cause conclusion, PLC command, or external task write.
"""

from __future__ import annotations

import json
import os
import socket
from dataclasses import dataclass
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


class CodeKeyConfigurationError(ValueError):
    pass


class CodeKeyResponseError(RuntimeError):
    pass


Transport = Callable[[str, dict[str, str], dict[str, Any], float], Mapping[str, Any]]


@dataclass(frozen=True)
class CodeKeyTerraConfig:
    api_key: str
    base_url: str = "https://hetune.top"
    model: str = "gpt-5.6-sol"
    timeout_seconds: float = 45.0
    max_tokens: int = 600
    temperature: float = 0.1
    # The hetune gateway accepts the OpenAI JSON-mode field. The codekey.ai
    # compatibility endpoint returns JSON from the guarded prompt but rejects
    # that optional field, so the server-side fallback can disable it without
    # weakening the response validator below.
    json_mode: bool = True

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "CodeKeyTerraConfig":
        values = os.environ if env is None else env
        config = cls(
            api_key=values.get("CODEKEY_API_KEY", "").strip(),
            base_url=(values.get("CODEKEY_BASE_URL") or "https://hetune.top").rstrip("/"),
            model=(values.get("CODEKEY_TERRA_MODEL") or "gpt-5.6-sol").strip(),
        )
        config.validate()
        return config

    def validate(self) -> None:
        if not self.api_key:
            raise CodeKeyConfigurationError("CODEKEY_API_KEY 未配置")
        if not self.model:
            raise CodeKeyConfigurationError("CODEKEY_TERRA_MODEL 未配置")
        parsed = urlsplit(self.base_url)
        if parsed.scheme != "https" or parsed.hostname not in {"codekey.ai", "hetune.top"} or parsed.port or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise CodeKeyConfigurationError("CODEKEY_BASE_URL 只允许受控的 https://codekey.ai 或 https://hetune.top")
        if not 1 <= self.max_tokens <= 800:
            raise CodeKeyConfigurationError("受控模型最大输出 token 必须位于 1..800")
        if not 0 <= self.temperature <= 0.2:
            raise CodeKeyConfigurationError("受控模型温度必须位于 0..0.2")
        if not 1 <= self.timeout_seconds <= 60:
            raise CodeKeyConfigurationError("受控模型超时必须位于 1..60 秒")


TERRA_SYSTEM_PROMPT = """你是设备质量风险闭环中的受控文书助手。
输入只包含脱敏关系数据的事实、候选关联、缺失字段和评审问题。你只能基于这些字段组织待核验说明和补证问题，并直接回应评审问题。
禁止确认根因、推断真实设备状态、判断质量放行、建议停线、修改 PLC 或工具参数、创建外部任务。
事实、候选关联和信息缺口必须分开。若证据不足，明确写“需补证”。
严格返回 JSON 对象，字段仅包含 summary、review_questions、task_notes、safety。
safety 必须为 {"root_cause_confirmed":false,"automatic_action_allowed":false,"human_approval_required":true}。"""


def _transport(
    url: str, headers: dict[str, str], payload: dict[str, Any], timeout: float
) -> Mapping[str, Any]:
    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={
            "Accept": "application/json",
            "User-Agent": "TorqueGuard/2.0 (controlled server-side reasoner)",
            **headers,
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed HTTPS host
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        raise CodeKeyResponseError(f"受控模型请求返回 HTTP {exc.code}") from exc
    except URLError as exc:
        if isinstance(exc.reason, (socket.gaierror, ConnectionRefusedError)):
            raise CodeKeyResponseError("受控模型服务不可达") from exc
        raise CodeKeyResponseError("受控模型请求失败") from exc
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CodeKeyResponseError("受控模型返回了非 JSON 响应") from exc
    if not isinstance(parsed, Mapping):
        raise CodeKeyResponseError("受控模型响应不是对象")
    return parsed


class CodeKeyTerraClient:
    """One-shot, bounded call for a pre-approved relationship dossier."""

    def __init__(self, config: CodeKeyTerraConfig, *, transport: Transport = _transport) -> None:
        config.validate()
        self.config = config
        self.transport = transport

    def draft(self, dossier: Mapping[str, Any]) -> dict[str, Any]:
        minimized = self._minimize(dossier)
        payload = {
            "model": self.config.model,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
            "messages": [
                {"role": "system", "content": TERRA_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(minimized, ensure_ascii=False)},
            ],
        }
        if self.config.json_mode:
            payload["response_format"] = {"type": "json_object"}
        response = self.transport(
            f"{self.config.base_url}/v1/chat/completions",
            {
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
            },
            payload,
            self.config.timeout_seconds,
        )
        raw = self._content(response)
        return self._validate_output(raw, minimized, self.config.model)

    @staticmethod
    def _minimize(dossier: Mapping[str, Any]) -> dict[str, Any]:
        case = dossier.get("case")
        facts = dossier.get("facts")
        gaps = dossier.get("gaps")
        tasks = dossier.get("tasks")
        if not all(isinstance(value, (dict, list)) for value in (case, facts, gaps, tasks)):
            raise CodeKeyResponseError("受控模型输入必须是结构化关系案卷")
        return {
            "question": str(dossier.get("question", "")).strip()[:2000],
            "case": {
                key: case.get(key)
                for key in (
                    "case_id",
                    "equipment_family",
                    "component_family",
                    "severity_band",
                    "status_group",
                    "relation_tier",
                    "completeness_grade",
                )
            },
            "facts": [
                {key: item.get(key) for key in ("evidence_id", "label", "strength", "detail")}
                for item in facts
                if isinstance(item, Mapping)
            ],
            "gaps": [
                {key: item.get(key) for key in ("evidence_id", "label", "strength", "detail")}
                for item in gaps
                if isinstance(item, Mapping)
            ],
            "task_ids": [item.get("task_id") for item in tasks if isinstance(item, Mapping)],
        }

    @staticmethod
    def _content(response: Mapping[str, Any]) -> Mapping[str, Any]:
        try:
            content = response["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise CodeKeyResponseError("受控模型响应缺少 choices[0].message.content") from exc
        if not isinstance(content, str):
            raise CodeKeyResponseError("受控模型内容不是文本")
        try:
            output = json.loads(content)
        except json.JSONDecodeError as exc:
            raise CodeKeyResponseError("受控模型未返回有效 JSON 文书") from exc
        if not isinstance(output, Mapping):
            raise CodeKeyResponseError("受控模型文书不是对象")
        return output

    @staticmethod
    def _validate_output(output: Mapping[str, Any], minimized: Mapping[str, Any], model: str) -> dict[str, Any]:
        expected = {"summary", "review_questions", "task_notes", "safety"}
        if set(output) != expected:
            raise CodeKeyResponseError("受控模型输出字段不符合受控合同")
        summary = output.get("summary")
        # codekey.ai may return a richer structured summary while preserving
        # the same top-level contract. Keep only its bounded human-readable
        # response field; all other facts remain governed by the local dossier.
        if isinstance(summary, Mapping):
            summary = summary.get("direct_response") or summary.get("response")
            if not summary:
                fragments: list[str] = []
                for section in ("facts", "candidate_associations", "information_gaps"):
                    for item in output.get("summary", {}).get(section, []):
                        if not isinstance(item, Mapping):
                            continue
                        detail = item.get("description") or item.get("detail") or item.get("label")
                        if isinstance(detail, str) and detail.strip():
                            fragments.append(detail.strip())
                if fragments:
                    summary = "；".join(fragments) + "。需补证并由工程师复核，不能据此确认根因。"
                else:
                    summary = "受控模型已返回结构化审阅结果，请结合当前风险卡补充证据并由工程师复核；不能据此确认根因。"
        raw_questions = output.get("review_questions")
        questions = []
        if isinstance(raw_questions, list):
            for item in raw_questions:
                if isinstance(item, str) and item.strip():
                    questions.append(item.strip())
                elif isinstance(item, Mapping):
                    question = item.get("question") or item.get("description") or item.get("text")
                    if isinstance(question, str) and question.strip():
                        questions.append(question.strip())
        elif isinstance(raw_questions, str) and raw_questions.strip():
            questions = [part.strip() for part in raw_questions.replace("；", "\n").splitlines() if part.strip()]
        raw_notes = output.get("task_notes")
        notes = []
        if isinstance(raw_notes, list):
            allowed_ids = set(minimized["task_ids"])
            for item in raw_notes:
                if not isinstance(item, Mapping):
                    continue
                task_id = item.get("task_id") or item.get("action_id")
                note = item.get("note") or item.get("description") or item.get("text")
                # Ignore malformed or unknown references rather than allowing
                # model text to create an untraceable task association.
                if task_id not in allowed_ids or not isinstance(note, str) or not note.strip() or len(note) > 220:
                    continue
                notes.append({"task_id": task_id, "note": note.strip()})
        safety = output.get("safety")
        if not isinstance(summary, str) or not summary.strip():
            raise CodeKeyResponseError("受控模型 summary 非法或过长")
        full_summary = summary.strip()
        # Keep the browser response bounded while retaining the full text for
        # the safety scan below. Providers occasionally add a useful second
        # sentence even though the public contract only needs a short answer.
        summary = full_summary[:360].rstrip()
        if not questions:
            questions = ["请补充现场核验依据，并由具名工程师复核。"]
        if safety != {
            "root_cause_confirmed": False,
            "automatic_action_allowed": False,
            "human_approval_required": True,
        }:
            raise CodeKeyResponseError("受控模型输出试图绕过人工与安全门禁")
        prohibited = ("已确认根因", "自动停线", "修改plc", "无需验证", "质量放行")
        combined = " ".join([full_summary, *questions, *(str(item.get("note", "")) for item in notes)]).lower().replace(" ", "")
        if any(term in combined for term in prohibited):
            raise CodeKeyResponseError("受控模型输出包含未经授权的结论或动作")
        return {
            "summary": summary.strip(),
            "review_questions": [item.strip() for item in questions],
            "task_notes": [{"task_id": item["task_id"], "note": item["note"].strip()} for item in notes],
            "safety": dict(safety),
            "provenance": {"provider": "hetune", "model": model, "external_call": True},
        }
