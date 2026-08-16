"""Guarded OpenAI-compatible client for the optional CodeKey Terra reasoner.

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
    base_url: str = "https://codekey.ai"
    model: str = "terra"
    timeout_seconds: float = 12.0
    max_tokens: int = 600
    temperature: float = 0.1

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "CodeKeyTerraConfig":
        values = os.environ if env is None else env
        config = cls(
            api_key=values.get("CODEKEY_API_KEY", "").strip(),
            base_url=(values.get("CODEKEY_BASE_URL") or "https://codekey.ai").rstrip("/"),
            model=(values.get("CODEKEY_TERRA_MODEL") or "terra").strip(),
        )
        config.validate()
        return config

    def validate(self) -> None:
        if not self.api_key:
            raise CodeKeyConfigurationError("CODEKEY_API_KEY 未配置")
        if not self.model:
            raise CodeKeyConfigurationError("CODEKEY_TERRA_MODEL 未配置")
        parsed = urlsplit(self.base_url)
        if parsed.scheme != "https" or parsed.netloc != "codekey.ai" or parsed.query or parsed.fragment:
            raise CodeKeyConfigurationError("CODEKEY_BASE_URL 只允许 https://codekey.ai")
        if not 1 <= self.max_tokens <= 800:
            raise CodeKeyConfigurationError("Terra 最大输出 token 必须位于 1..800")
        if not 0 <= self.temperature <= 0.2:
            raise CodeKeyConfigurationError("Terra 温度必须位于 0..0.2")
        if not 1 <= self.timeout_seconds <= 20:
            raise CodeKeyConfigurationError("Terra 超时必须位于 1..20 秒")


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
        headers=headers,
    )
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed HTTPS host
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        raise CodeKeyResponseError(f"Terra 请求返回 HTTP {exc.code}") from exc
    except URLError as exc:
        if isinstance(exc.reason, (socket.gaierror, ConnectionRefusedError)):
            raise CodeKeyResponseError("Terra 服务不可达") from exc
        raise CodeKeyResponseError("Terra 请求失败") from exc
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CodeKeyResponseError("Terra 返回了非 JSON 响应") from exc
    if not isinstance(parsed, Mapping):
        raise CodeKeyResponseError("Terra 响应不是对象")
    return parsed


class CodeKeyTerraClient:
    """One-shot, bounded call for a pre-approved relationship dossier."""

    def __init__(self, config: CodeKeyTerraConfig, *, transport: Transport = _transport) -> None:
        config.validate()
        self.config = config
        self.transport = transport

    def draft(self, dossier: Mapping[str, Any]) -> dict[str, Any]:
        minimized = self._minimize(dossier)
        response = self.transport(
            f"{self.config.base_url}/v1/chat/completions",
            {
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
            },
            {
                "model": self.config.model,
                "temperature": self.config.temperature,
                "max_tokens": self.config.max_tokens,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": TERRA_SYSTEM_PROMPT},
                    {"role": "user", "content": json.dumps(minimized, ensure_ascii=False)},
                ],
            },
            self.config.timeout_seconds,
        )
        raw = self._content(response)
        return self._validate_output(raw, minimized)

    @staticmethod
    def _minimize(dossier: Mapping[str, Any]) -> dict[str, Any]:
        case = dossier.get("case")
        facts = dossier.get("facts")
        gaps = dossier.get("gaps")
        tasks = dossier.get("tasks")
        if not all(isinstance(value, (dict, list)) for value in (case, facts, gaps, tasks)):
            raise CodeKeyResponseError("Terra 输入必须是结构化关系案卷")
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
            raise CodeKeyResponseError("Terra 响应缺少 choices[0].message.content") from exc
        if not isinstance(content, str):
            raise CodeKeyResponseError("Terra 内容不是文本")
        try:
            output = json.loads(content)
        except json.JSONDecodeError as exc:
            raise CodeKeyResponseError("Terra 未返回有效 JSON 文书") from exc
        if not isinstance(output, Mapping):
            raise CodeKeyResponseError("Terra 文书不是对象")
        return output

    @staticmethod
    def _validate_output(output: Mapping[str, Any], minimized: Mapping[str, Any]) -> dict[str, Any]:
        expected = {"summary", "review_questions", "task_notes", "safety"}
        if set(output) != expected:
            raise CodeKeyResponseError("Terra 输出字段不符合受控合同")
        summary = output.get("summary")
        questions = output.get("review_questions")
        notes = output.get("task_notes")
        safety = output.get("safety")
        if not isinstance(summary, str) or not summary.strip() or len(summary) > 360:
            raise CodeKeyResponseError("Terra summary 非法或过长")
        if not isinstance(questions, list) or not all(isinstance(item, str) and item.strip() for item in questions):
            raise CodeKeyResponseError("Terra review_questions 非法")
        if not isinstance(notes, list) or not all(isinstance(item, Mapping) for item in notes):
            raise CodeKeyResponseError("Terra task_notes 非法")
        allowed_ids = set(minimized["task_ids"])
        for note in notes:
            if set(note) != {"task_id", "note"} or note.get("task_id") not in allowed_ids:
                raise CodeKeyResponseError("Terra task_notes 引用了未知任务")
            if not isinstance(note.get("note"), str) or not note["note"].strip() or len(note["note"]) > 220:
                raise CodeKeyResponseError("Terra task_notes 内容非法")
        if safety != {
            "root_cause_confirmed": False,
            "automatic_action_allowed": False,
            "human_approval_required": True,
        }:
            raise CodeKeyResponseError("Terra 输出试图绕过人工与安全门禁")
        prohibited = ("已确认根因", "自动停线", "修改plc", "无需验证", "质量放行")
        combined = " ".join([summary, *questions, *(str(item.get("note", "")) for item in notes)]).lower().replace(" ", "")
        if any(term in combined for term in prohibited):
            raise CodeKeyResponseError("Terra 输出包含未经授权的结论或动作")
        return {
            "summary": summary.strip(),
            "review_questions": [item.strip() for item in questions],
            "task_notes": [{"task_id": item["task_id"], "note": item["note"].strip()} for item in notes],
            "safety": dict(safety),
            "provenance": {"provider": "codekey", "model": "terra", "external_call": True},
        }
