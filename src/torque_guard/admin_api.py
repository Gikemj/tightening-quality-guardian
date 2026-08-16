"""Local administrator API for the TorqueGuard prototype.

This is intentionally a dependency-free, localhost-only control plane.  It
executes the existing analysis and workflow contracts, while keeping every
mutable administrator artifact under ``.local/admin``.  It is not a production
auth layer and never performs a live Feishu write from a browser request.
"""

from __future__ import annotations

import csv
import json
import mimetypes
import os
import sys
import threading
import time
from dataclasses import replace
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .agent import DigitalEmployee
from .artifacts import write_json
from .integrations.codekey import CodeKeyResponseError, CodeKeyTerraClient, CodeKeyTerraConfig
from .integrations.feishu import build_bitable_records
from .knowledge import KnowledgeBase
from .models import Action, CandidateCause, Evidence, RiskCard
from .risk import RiskAnalyzer, read_events
from .scenarios import SCENARIOS, generate_independent_case
from .workflow import RiskCaseWorkflow, WorkflowAction, WorkflowTransition


ROOT = Path(__file__).resolve().parents[2]
DOCS_ROOT = ROOT / "docs"
LOCAL_ROOT = ROOT / ".local" / "admin"
PUBLIC_CARD = ROOT / "outputs" / "risk_card.json"


def _load_local_env() -> None:
    """Load private .env values for the local service without overwriting the shell."""

    path = ROOT / ".env"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip()
        if not name or name in os.environ:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ[name] = value


_load_local_env()


CAPABILITIES: list[dict[str, Any]] = [
    {"id": "analysis", "name": "风险分析与重放", "status": "implemented", "details": "DigitalEmployee + RiskAnalyzer + SPC + 确定性推理"},
    {"id": "spc", "name": "SPC 规则与指标", "status": "implemented", "details": "均值、标准差、Western Electric 规则、能力快照"},
    {"id": "knowledge", "name": "知识库与关系子图", "status": "implemented", "details": "PFMEA、控制计划、告警字典、历史案例、ontology"},
    {"id": "evidence", "name": "证据链与版本指纹", "status": "implemented", "details": "证据 ID、定位、引用完整性、SHA-256 修订"},
    {"id": "workflow", "name": "人工门禁工作流", "status": "implemented", "details": "审批、建任务、验证、结案、重开状态机"},
    {"id": "audit", "name": "Agent 调用审计", "status": "implemented", "details": "sense / analyze / reason / govern 调用轨迹"},
    {"id": "feishu_preview", "name": "飞书字段预览", "status": "implemented", "details": "本地生成风险表与任务表 payload"},
    {"id": "admin_api", "name": "本地管理员 API", "status": "implemented", "details": "本服务新增，限定 localhost 与 .local/admin 状态"},
    {"id": "simulator", "name": "临时拧紧数据模拟器", "status": "implemented", "details": "生成稳定、扭矩漂移、传感器零漂和重复报警场景并实时分析"},
    {"id": "feishu_live", "name": "飞书真实租户闭环", "status": "partial", "details": "客户端与测试存在，但未完成授权租户联调"},
    {"id": "identity", "name": "企业身份与权限", "status": "missing", "details": "当前没有登录、RBAC、人员目录校验"},
    {"id": "persistence", "name": "企业级持久化与幂等", "status": "missing", "details": "仅本地 JSON；无数据库、事务、幂等键、不可篡改审计"},
    {"id": "closure", "name": "现场结案与案例回写", "status": "missing", "details": "没有真实工程师执行、验证证据回传或案例库回写"},
    {"id": "realtime", "name": "实时产线接入", "status": "missing", "details": "没有 PLC、MES、QMS 或流式事件连接"},
    {"id": "external_model", "name": "外部模型/Aily 调用", "status": "guarded", "details": "保留协议与安全配置，默认使用确定性推理"},
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Unsupported JSON value: {type(value).__name__}")


def _card_from_dict(raw: dict[str, Any]) -> RiskCard:
    return RiskCard(
        card_id=raw["card_id"],
        created_at=raw["created_at"],
        station_id=raw["station_id"],
        tool_id=raw["tool_id"],
        fastening_point=raw["fastening_point"],
        risk_level=raw["risk_level"],
        risk_score=raw["risk_score"],
        status=raw["status"],
        observed_facts=raw.get("observed_facts", []),
        inference=raw.get("inference", ""),
        uncertainty=raw.get("uncertainty", ""),
        affected_scope=raw.get("affected_scope", {}),
        score_breakdown=raw.get("score_breakdown", {}),
        evidence=[Evidence(**item) for item in raw.get("evidence", [])],
        candidate_causes=[CandidateCause(**item) for item in raw.get("candidate_causes", [])],
        recommended_actions=[Action(**item) for item in raw.get("recommended_actions", [])],
        analysis_provenance=raw.get("analysis_provenance", {}),
        schema_version=raw.get("schema_version", "1.0"),
        agent_trace=raw.get("agent_trace", []),
        reasoning=raw.get("reasoning", {}),
        workflow=raw.get("workflow", {}),
    )


class AdminService:
    def __init__(self, root: Path = ROOT) -> None:
        self.root = root.resolve()
        self.local_root = self.root / ".local" / "admin"
        self.local_root.mkdir(parents=True, exist_ok=True)
        self.state_path = self.local_root / "state.json"
        self.audit_path = self.local_root / "audit.jsonl"
        self.monitor_config_path = self.local_root / "monitor_config.json"
        self._monitor_key = ""
        self._monitor_stop = threading.Event()
        self._monitor_thread: threading.Thread | None = None
        self._monitor_lock = threading.Lock()
        self._monitor_samples: list[dict[str, Any]] = []
        self._monitor_last_error: str | None = None
        self.simulator_config_path = self.local_root / "simulator_config.json"
        self._simulator_lock = threading.Lock()
        self._simulator_stop = threading.Event()
        self._simulator_thread: threading.Thread | None = None
        self._simulator_sequence = 0
        self._simulator_card: dict[str, Any] | None = None
        self._simulator_events: list[dict[str, Any]] = []
        self._simulator_series: list[dict[str, Any]] = []
        self._simulator_history: list[dict[str, Any]] = []
        self._simulator_last_error: str | None = None

    def _read_json(self, path: Path, fallback: Any = None) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return fallback

    def _write_json(self, path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default) + "\n", encoding="utf-8")

    def _state(self) -> dict[str, Any]:
        state = self._read_json(self.state_path, {})
        if not isinstance(state, dict):
            state = {}
        state.setdefault("card_path", str(PUBLIC_CARD))
        state.setdefault("runs", [])
        return state

    def _card_path(self) -> Path:
        path = Path(self._state().get("card_path", str(PUBLIC_CARD)))
        if not path.is_absolute():
            path = self.root / path
        return path.resolve()

    def load_card_raw(self) -> dict[str, Any]:
        raw = self._read_json(self._card_path())
        if not isinstance(raw, dict):
            raise ValueError("当前没有可用风险卡，请先运行一次分析")
        return raw

    def load_card(self) -> RiskCard:
        return _card_from_dict(self.load_card_raw())

    def audit(self, action: str, detail: dict[str, Any] | None = None) -> None:
        record = {"at": _now(), "action": action, "detail": detail or {}}
        with self.audit_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def audit_records(self) -> list[dict[str, Any]]:
        if not self.audit_path.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line in self.audit_path.read_text(encoding="utf-8").splitlines()[-200:]:
            try:
                value = json.loads(line)
                if isinstance(value, dict):
                    rows.append(value)
            except json.JSONDecodeError:
                continue
        return list(reversed(rows))

    def summary(self) -> dict[str, Any]:
        raw = self.load_card_raw()
        card = _card_from_dict(raw)
        workflow = raw.get("workflow", {})
        traces = raw.get("agent_trace", [])
        evidence = raw.get("evidence", [])
        events = self._event_stats()
        knowledge = self.knowledge_summary()
        failed_traces = [item.get("call_id", "unknown") for item in traces if item.get("status") != "succeeded"]
        return {
            "generatedAt": _now(),
            "mode": "local_admin",
            "boundary": "仅限本地管理与演示；不会自动停线、改 PLC 或向外部系统写入",
            "case": {
                "cardId": card.card_id,
                "stationId": card.station_id,
                "toolId": card.tool_id,
                "fasteningPoint": card.fastening_point,
                "riskLevel": card.risk_level,
                "riskScore": card.risk_score,
                "status": card.status,
                "analysisDisposition": card.analysis_provenance.get("analysis_disposition"),
            },
            "health": {
                "evidenceCount": len(evidence),
                "traceCount": len(traces),
                "failedTraceCount": len(failed_traces),
                "revision": card.analysis_provenance.get("card_identity_revision"),
                "knowledgeRevision": card.analysis_provenance.get("knowledge_revision"),
                "metricAvailability": card.analysis_provenance.get("metric_availability", {}),
            },
            "workflow": {
                "status": workflow.get("status", card.status),
                "allowedActions": workflow.get("allowed_actions", []),
                "humanApprovalRequired": workflow.get("human_approval_required", False),
                "events": workflow.get("events", []),
                "automaticStopLineAllowed": workflow.get("automatic_stop_line_allowed", False),
            },
            "events": events,
            "knowledge": knowledge,
            "capabilities": CAPABILITIES,
        }

    def _event_stats(self) -> dict[str, Any]:
        path = self.root / "data" / "tightening_events_demo.csv"
        try:
            rows = read_events(path)
        except Exception as exc:
            return {"file": str(path.relative_to(self.root)), "records": 0, "points": [], "error": str(exc)}
        points = sorted({str(row.get("fastening_point", "")) for row in rows})
        return {"file": str(path.relative_to(self.root)), "records": len(rows), "points": points, "latest": max((row.get("timestamp", "") for row in rows), default="")}

    def data_inventory(self) -> list[dict[str, Any]]:
        roots = [self.root / "data", self.root / "knowledge", self.root / "outputs", self.root / "docs" / "data"]
        rows: list[dict[str, Any]] = []
        for directory in roots:
            if not directory.exists():
                continue
            for path in sorted(directory.rglob("*")):
                if not path.is_file():
                    continue
                relative = path.relative_to(self.root).as_posix()
                rows.append({"path": relative, "size": path.stat().st_size, "modified": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat().replace("+00:00", "Z"), "kind": path.suffix.lstrip(".") or "file"})
        return rows

    def knowledge_summary(self) -> dict[str, Any]:
        try:
            bundle = KnowledgeBase(self.root / "knowledge")
            return {"status": "ready", "revision": bundle.revision, "controlPlans": len(bundle.control_plans), "pfmeaRows": len(bundle.pfmea_rows), "historyCases": len(bundle.historical_cases), "ontologyNodes": len(bundle.ontology.get("nodes", [])), "alarmCodes": len(bundle.alarm_dictionary)}
        except Exception as exc:
            return {"status": "error", "error": str(exc)}

    def integration_status(self) -> dict[str, Any]:
        keys = ["FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_APP_TOKEN", "FEISHU_RISK_TABLE_ID", "FEISHU_TASK_TABLE_ID"]
        configured = [key for key in keys if os.getenv(key)]
        terra_key_configured = bool(os.getenv("CODEKEY_API_KEY", "").strip())
        reasoner_model = os.getenv("CODEKEY_TERRA_MODEL", "gpt-5.6-sol")
        reasoner_base_url = os.getenv("CODEKEY_BASE_URL", "https://hetune.top")
        return {
            "mode": "preview",
            "configuredKeys": configured,
            "requiredKeys": keys,
            "liveWriteEnabled": False,
            "message": "浏览器 API 不会触发 live 写入；需在授权环境中显式调用 CLI 并完成审批回执",
            "terra": {
                "configured": terra_key_configured,
                "provider": "Hetune（OpenAI 兼容接口）",
                "model": reasoner_model,
                "baseUrl": reasoner_base_url,
                "keyLoaded": terra_key_configured,
                "serverSideOnly": True,
                "message": "管理员服务端按需调用；密钥不返回浏览器、不写审计日志。" if terra_key_configured else "未配置密钥时使用本地确定性摘要。",
            },
        }

    def _monitor_config(self) -> dict[str, Any]:
        value = self._read_json(self.monitor_config_path, {})
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _validated_monitor_url(value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("API 地址不能为空")
        url = value.strip()
        if len(url) > 2048:
            raise ValueError("API 地址过长")
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("API 地址必须是完整的 http:// 或 https:// URL")
        if parsed.username or parsed.password:
            raise ValueError("API 地址不得包含用户名或密码")
        try:
            _ = parsed.port
        except ValueError as exc:
            raise ValueError("API 端口格式非法") from exc
        return url

    def configure_monitor(self, payload: dict[str, Any]) -> dict[str, Any]:
        name = str(payload.get("name", "项目实时数据")).strip()[:100] or "项目实时数据"
        api_url = self._validated_monitor_url(payload.get("apiUrl"))
        chat_url_value = payload.get("chatUrl", "")
        chat_url = self._validated_monitor_url(chat_url_value) if isinstance(chat_url_value, str) and chat_url_value.strip() else ""
        auth_type = str(payload.get("authType", "bearer")).strip()
        if auth_type not in {"none", "bearer", "x-api-key"}:
            raise ValueError("不支持的鉴权方式")
        interval = int(payload.get("intervalSeconds", 10))
        timeout = int(payload.get("timeoutSeconds", 8))
        if not 2 <= interval <= 3600:
            raise ValueError("轮询间隔必须在 2..3600 秒之间")
        if not 1 <= timeout <= 30:
            raise ValueError("请求超时必须在 1..30 秒之间")
        key = payload.get("apiKey", "")
        if not isinstance(key, str) or len(key) > 4096:
            raise ValueError("API Key 格式非法或长度超过限制")
        if key:
            self._monitor_key = key.strip()
        if auth_type != "none" and not self._monitor_key:
            raise ValueError("当前鉴权方式必须填写 API Key")
        config = {
            "name": name,
            "apiUrl": api_url,
            "chatUrl": chat_url,
            "authType": auth_type,
            "intervalSeconds": interval,
            "timeoutSeconds": timeout,
            "updatedAt": _now(),
        }
        self._write_json(self.monitor_config_path, config)
        self.audit("monitor.configure", {"name": name, "apiUrl": api_url, "authType": auth_type, "intervalSeconds": interval})
        return self.monitor_status()

    def _monitor_headers(self, config: dict[str, Any]) -> dict[str, str]:
        headers = {"Accept": "application/json", "User-Agent": "TorqueGuard-Local-Monitor/1.0"}
        if config.get("authType") == "bearer":
            headers["Authorization"] = f"Bearer {self._monitor_key}"
        elif config.get("authType") == "x-api-key":
            headers["X-API-Key"] = self._monitor_key
        return headers

    def _fetch_monitor_once(self) -> dict[str, Any]:
        config = self._monitor_config()
        if not config:
            raise ValueError("请先保存数据源配置")
        if config.get("authType") != "none" and not self._monitor_key:
            raise ValueError("API Key 仅保存在进程内；服务重启后请重新填写")
        request = Request(config["apiUrl"], headers=self._monitor_headers(config), method="GET")
        started = time.perf_counter()
        try:
            with urlopen(request, timeout=int(config.get("timeoutSeconds", 8))) as response:
                data = response.read(2_000_001)
                if len(data) > 2_000_000:
                    raise ValueError("API 响应超过 2 MB 限制")
                charset = response.headers.get_content_charset() or "utf-8"
                try:
                    payload = json.loads(data.decode(charset))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ValueError("API 必须返回有效 JSON") from exc
                sample = {
                    "at": _now(),
                    "ok": True,
                    "status": response.status,
                    "durationMs": round((time.perf_counter() - started) * 1000, 1),
                    "data": payload,
                }
        except HTTPError as exc:
            message = f"上游 API 返回 HTTP {exc.code}"
            if exc.code in {401, 403}:
                message += "，请检查 Key 与权限"
            raise ValueError(message) from exc
        except URLError as exc:
            raise ValueError(f"无法连接上游 API：{exc.reason}") from exc
        with self._monitor_lock:
            self._monitor_samples.append(sample)
            self._monitor_samples = self._monitor_samples[-100:]
            self._monitor_last_error = None
        return sample

    def test_monitor(self) -> dict[str, Any]:
        try:
            sample = self._fetch_monitor_once()
        except Exception as exc:
            with self._monitor_lock:
                self._monitor_last_error = str(exc)
            self.audit("monitor.test_failed", {"error": str(exc)})
            raise
        self.audit("monitor.test_succeeded", {"status": sample["status"], "durationMs": sample["durationMs"]})
        return {"sample": sample, "status": self.monitor_status()}

    def _monitor_loop(self) -> None:
        while not self._monitor_stop.is_set():
            try:
                self._fetch_monitor_once()
            except Exception as exc:
                with self._monitor_lock:
                    self._monitor_last_error = str(exc)
            config = self._monitor_config()
            self._monitor_stop.wait(max(2, int(config.get("intervalSeconds", 10))))

    def start_monitor(self) -> dict[str, Any]:
        config = self._monitor_config()
        if not config:
            raise ValueError("请先保存并测试数据源配置")
        if config.get("authType") != "none" and not self._monitor_key:
            raise ValueError("服务重启后必须重新填写 API Key")
        if self._monitor_thread and self._monitor_thread.is_alive():
            return self.monitor_status()
        self._monitor_stop.clear()
        self._monitor_thread = threading.Thread(target=self._monitor_loop, name="torque-guard-monitor", daemon=True)
        self._monitor_thread.start()
        self.audit("monitor.start", {"name": config.get("name"), "intervalSeconds": config.get("intervalSeconds")})
        return self.monitor_status()

    def stop_monitor(self) -> dict[str, Any]:
        self._monitor_stop.set()
        thread = self._monitor_thread
        if thread and thread.is_alive():
            thread.join(timeout=2)
        self.audit("monitor.stop")
        return self.monitor_status()

    def monitor_status(self) -> dict[str, Any]:
        config = self._monitor_config()
        with self._monitor_lock:
            samples = list(self._monitor_samples[-20:])
            error = self._monitor_last_error
        return {
            "configured": bool(config),
            "hasKey": bool(self._monitor_key),
            "running": bool(self._monitor_thread and self._monitor_thread.is_alive()),
            "config": config,
            "lastError": error,
            "lastSample": samples[-1] if samples else None,
            "samples": list(reversed(samples)),
            "keyPersistence": "memory_only",
        }

    @staticmethod
    def _extract_ai_text(payload: Any) -> str:
        if isinstance(payload, str):
            return payload.strip()
        if not isinstance(payload, dict):
            return ""
        choices = payload.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0]
            if isinstance(first, dict):
                message = first.get("message")
                if isinstance(message, dict) and isinstance(message.get("content"), str):
                    return message["content"].strip()
                if isinstance(first.get("text"), str):
                    return first["text"].strip()
        for key in ("answer", "response", "text", "content"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        message = payload.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
        if isinstance(message, dict) and isinstance(message.get("content"), str):
            return message["content"].strip()
        return ""

    def _local_ai_answer(self, question: str, status: dict[str, Any], external_error: str | None = None) -> str:
        card = status.get("card") or {}
        reasons = card.get("analysis_provenance", {}).get("trigger_reasons", [])
        causes = [item.get("cause") for item in card.get("candidate_causes", []) if item.get("cause")]
        facts = card.get("observed_facts", [])[-3:]
        lines = [
            f"当前第 {status.get('sequence', 0)} 批，场景 {status.get('scenario', 'unknown')}。",
            f"风险：{str(card.get('risk_level', 'unknown')).upper()} {card.get('risk_score', '—')}/100；工作流：{card.get('status', 'unknown')}。",
            f"触发原因：{'；'.join(reasons) if reasons else '当前没有触发异常规则'}。",
            f"候选原因：{'；'.join(causes[:3]) if causes else '暂无，需要继续观察'}。",
            f"评估总结：{'；'.join(facts) if facts else '等待第一批实时数据。'}",
            f"针对你的问题“{question}”：请先核对上述证据，再由工程师确认现场原因。",
        ]
        if external_error:
            lines.insert(0, f"外部 AI API 未返回有效回答，已切换本地规则摘要（{external_error}）。")
        return "\n".join(lines)

    @staticmethod
    def _terra_dossier(question: str, status: dict[str, Any]) -> dict[str, Any]:
        card = status.get("card") or {}
        evidence = card.get("evidence") or []
        facts = [
            {
                "evidence_id": item.get("evidence_id"),
                "label": item.get("title", "风险卡证据"),
                "strength": "direct",
                "detail": item.get("observation", ""),
            }
            for item in evidence
            if item.get("evidence_id")
        ]
        tasks = [
            {
                "task_id": item.get("action_id"),
                "title": item.get("title", "现场复核任务"),
            }
            for item in (card.get("recommended_actions") or [])
            if item.get("action_id")
        ]
        gaps = []
        uncertainty = card.get("uncertainty")
        if uncertainty:
            gaps.append({"evidence_id": "G-UNCERTAINTY", "label": "待人工核验", "strength": "gap", "detail": uncertainty})
        return {
            "question": question,
            "case": {
                "case_id": card.get("card_id", "RISK-CARD-DEMO"),
                "equipment_family": card.get("station_id", "ST-FAS-07"),
                "component_family": card.get("tool_id", "TOOL-TG-07"),
                "severity_band": card.get("risk_level", "unknown"),
                "status_group": card.get("status", "awaiting_engineer_review"),
                "relation_tier": "设备—过程—质量风险卡",
                "completeness_grade": "已载入风险卡",
            },
            "facts": facts,
            "gaps": gaps,
            "tasks": tasks,
        }

    def ai_chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        question = str(payload.get("question", "")).strip()
        if not question:
            raise ValueError("问题不能为空")
        if len(question) > 2000:
            raise ValueError("问题不能超过 2000 个字符")
        status = self.simulator_status()
        config = self._monitor_config()
        endpoint = config.get("chatUrl") or config.get("apiUrl")
        external_error: str | None = None
        answer = ""
        used_external = False
        terra_used = False
        terra_provider = None
        terra_model = None
        if os.getenv("CODEKEY_API_KEY", "").strip():
            try:
                terra, terra_config = self._draft_with_controlled_fallback(self._terra_dossier(question, status))
                answer = terra["summary"]
                if terra.get("review_questions"):
                    answer += "\n待核对：" + "；".join(terra["review_questions"][:3])
                notes = [item.get("note") for item in terra.get("task_notes", []) if item.get("note")]
                if notes:
                    answer += "\n任务备注：" + "；".join(notes[:3])
                terra_used = True
                used_external = True
                terra_provider = terra_config.base_url
                terra_model = terra_config.model
            except (CodeKeyResponseError, ValueError, OSError) as exc:
                external_error = f"受控文书模型调用失败：{exc}"
        if not answer and endpoint and (config.get("authType") == "none" or self._monitor_key):
            context = {
                "scenario": status.get("scenario"),
                "sequence": status.get("sequence"),
                "card": status.get("card"),
                "latestEvents": status.get("latestEvents", [])[-12:],
            }
            body = {
                "messages": [
                    {"role": "system", "content": "你是拧紧质量监测助手。只依据提供的实时风险卡和事件回答，明确区分观测事实、触发规则、候选原因和需要人工验证的内容。不要声称已停线或已确认根因。用中文，简洁但具体。"},
                    {"role": "user", "content": f"问题：{question}\n实时上下文：{json.dumps(context, ensure_ascii=False, default=_json_default)}"},
                ],
                "temperature": 0.2,
                "stream": False,
            }
            try:
                request = Request(endpoint, data=json.dumps(body, ensure_ascii=False).encode("utf-8"), headers={**self._monitor_headers(config), "Content-Type": "application/json"}, method="POST")
                with urlopen(request, timeout=int(config.get("timeoutSeconds", 8))) as response:
                    raw = response.read(2_000_001)
                    if len(raw) > 2_000_000:
                        raise ValueError("AI API 响应超过 2 MB 限制")
                    parsed = json.loads(raw.decode(response.headers.get_content_charset() or "utf-8"))
                    answer = self._extract_ai_text(parsed)
                    if not answer:
                        raise ValueError("AI API 返回 JSON，但没有识别到回答字段")
                    used_external = True
            except HTTPError as exc:
                if exc.code in {404, 405} and endpoint == config.get("apiUrl") and not config.get("chatUrl"):
                    external_error = f"当前监测地址不支持 AI 对话（HTTP {exc.code}）；请在后台填写单独的 AI 对话接口"
                else:
                    external_error = f"AI API 返回 HTTP {exc.code}"
            except (URLError, ValueError, json.JSONDecodeError) as exc:
                external_error = str(exc)
        elif endpoint and config.get("authType") != "none" and not self._monitor_key:
            external_error = "后台重启后 API Key 未重新填写"
        elif not endpoint and not external_error and not terra_used:
            external_error = "后台尚未配置 API 地址"
        if not answer:
            answer = self._local_ai_answer(question, status, external_error)
        result = {"answer": answer, "source": "controlled_reasoner" if terra_used else ("external_api" if used_external else "local_rule_fallback"), "usedExternalApi": used_external, "externalError": external_error, "provider": terra_provider, "model": terra_model, "at": _now(), "sequence": status.get("sequence", 0), "cardId": (status.get("card") or {}).get("card_id")}
        self.audit("ai.chat", {"source": result["source"], "sequence": result["sequence"]})
        return result

    @staticmethod
    def _draft_with_controlled_fallback(dossier: dict[str, Any]) -> tuple[dict[str, Any], CodeKeyTerraConfig]:
        """Try the configured gateway, then the approved CodeKey alias only.

        The current credential is accepted by ``codekey.ai`` while the
        requested ``hetune.top`` alias can reject the same token. Both hosts
        are explicitly allow-listed by ``CodeKeyTerraConfig``. The fallback is
        server-side, keeps the same bounded prompt/output validator, and never
        exposes the key or accepts an arbitrary endpoint.
        """
        config = CodeKeyTerraConfig.from_env()
        candidates = [config]
        if config.base_url == "https://hetune.top":
            candidates.append(replace(config, base_url="https://codekey.ai", json_mode=False, max_tokens=420))
        errors: list[str] = []
        for candidate in candidates:
            try:
                return CodeKeyTerraClient(candidate).draft(dossier), candidate
            except (CodeKeyResponseError, ValueError, OSError) as exc:
                errors.append(f"{candidate.base_url}: {exc}")
        raise CodeKeyResponseError("；".join(errors))

    def _simulator_config(self) -> dict[str, Any]:
        value = self._read_json(self.simulator_config_path, {})
        return value if isinstance(value, dict) else {}

    def configure_simulator(self, payload: dict[str, Any]) -> dict[str, Any]:
        scenario = str(payload.get("scenario", "hidden_torque_drift")).strip()
        if scenario not in SCENARIOS:
            raise ValueError(f"未知模拟场景：{scenario}")
        strength = float(payload.get("strength", 1.0))
        interval = int(payload.get("intervalSeconds", 4))
        if not 0.25 <= strength <= 3:
            raise ValueError("模拟强度必须在 0.25..3 之间")
        if not 2 <= interval <= 60:
            raise ValueError("模拟间隔必须在 2..60 秒之间")
        config = {"scenario": scenario, "strength": strength, "intervalSeconds": interval, "updatedAt": _now()}
        self._write_json(self.simulator_config_path, config)
        self.audit("simulator.configure", {"scenario": scenario, "strength": strength, "intervalSeconds": interval})
        return self.simulator_status()

    def _simulator_cycle(self) -> dict[str, Any]:
        config = self._simulator_config()
        scenario = config.get("scenario", "hidden_torque_drift")
        strength = float(config.get("strength", 1.0))
        with self._simulator_lock:
            self._simulator_sequence += 1
            seed = 88000 + self._simulator_sequence
        events = generate_independent_case(scenario, seed=seed, strength=strength, count=148)
        employee = DigitalEmployee(self.root / "knowledge", trace_scope="SIM")
        card = employee.run_events(events, "P03", source_label=f"local_simulator:{scenario}")
        raw = card.to_dict()
        with self._simulator_lock:
            self._simulator_card = raw
            self._simulator_events = events[-24:]
            self._simulator_series = events
            stream_rows = []
            received_at = _now()
            for row in events:
                item = dict(row)
                item["stream_sequence"] = self._simulator_sequence
                item["stream_received_at"] = received_at
                stream_rows.append(item)
            self._simulator_history = (self._simulator_history + stream_rows)[-480:]
            self._simulator_last_error = None
        self._write_json(self.local_root / "simulator_risk_card.json", raw)
        return self.simulator_status()

    def generate_simulator_data(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if payload:
            self.configure_simulator(payload)
        if not self._simulator_config():
            self.configure_simulator({})
        try:
            result = self._simulator_cycle()
        except Exception as exc:
            with self._simulator_lock:
                self._simulator_last_error = str(exc)
            self.audit("simulator.generate_failed", {"error": str(exc)})
            raise
        self.audit("simulator.generate", {"sequence": result["sequence"], "scenario": result["scenario"], "cardId": result["card"]["card_id"]})
        return result

    def _simulator_loop(self) -> None:
        while not self._simulator_stop.is_set():
            try:
                self._simulator_cycle()
            except Exception as exc:
                with self._simulator_lock:
                    self._simulator_last_error = str(exc)
            config = self._simulator_config()
            self._simulator_stop.wait(max(2, int(config.get("intervalSeconds", 4))))

    def start_simulator(self) -> dict[str, Any]:
        if not self._simulator_config():
            self.configure_simulator({})
        if self._simulator_thread and self._simulator_thread.is_alive():
            return self.simulator_status()
        self._simulator_stop.clear()
        # Produce the first batch before returning so a dashboard click has an
        # immediate, visible result instead of waiting for the loop interval.
        self._simulator_cycle()
        self._simulator_thread = threading.Thread(target=self._simulator_loop, name="torque-guard-simulator", daemon=True)
        self._simulator_thread.start()
        self.audit("simulator.start")
        return self.simulator_status()

    def stop_simulator(self) -> dict[str, Any]:
        self._simulator_stop.set()
        thread = self._simulator_thread
        if thread and thread.is_alive():
            thread.join(timeout=2)
        self.audit("simulator.stop")
        return self.simulator_status()

    def simulator_status(self) -> dict[str, Any]:
        config = self._simulator_config()
        with self._simulator_lock:
            card = self._simulator_card
            events = list(self._simulator_events)
            series = list(self._simulator_series)
            history = list(self._simulator_history)
            sequence = self._simulator_sequence
            error = self._simulator_last_error
        return {
            "active": bool(card),
            "running": bool(self._simulator_thread and self._simulator_thread.is_alive()),
            "scenario": config.get("scenario", "hidden_torque_drift"),
            "strength": config.get("strength", 1.0),
            "intervalSeconds": config.get("intervalSeconds", 4),
            "sequence": sequence,
            "generatedAt": card.get("created_at") if card else None,
            "card": card,
            "latestEvents": events,
            "series": series,
            "history": history,
            "lastError": error,
            "synthetic": True,
        }

    def run_analysis(self, payload: dict[str, Any]) -> dict[str, Any]:
        relative_input = str(payload.get("input", "data/tightening_events_demo.csv")).strip()
        input_path = (self.root / relative_input).resolve()
        if not input_path.is_file() or self.root not in input_path.parents:
            raise ValueError("输入文件必须是仓库内存在的文件")
        point = str(payload.get("point", "P03")).strip()
        if not point:
            raise ValueError("fastening point 不能为空")
        baseline = int(payload.get("baselineCount", 100))
        recent = int(payload.get("recentCount", 24))
        if baseline < 2 or recent < 1 or baseline > 10000 or recent > 10000:
            raise ValueError("窗口数量不在允许范围内")
        employee = DigitalEmployee(self.root / "knowledge", trace_scope="ADMIN")
        employee.analyzer = RiskAnalyzer(self.root / "knowledge", baseline_count=baseline, recent_count=recent)
        card = employee.run(input_path, point, source_label=input_path.relative_to(self.root).as_posix())
        card_path = self.local_root / "risk_card.json"
        preview_path = self.local_root / "feishu_records_preview.json"
        raw = card.to_dict()
        self._write_json(card_path, raw)
        self._write_json(preview_path, build_bitable_records(card))
        state = self._state()
        state["card_path"] = str(card_path)
        state["runs"].append({"at": _now(), "cardId": card.card_id, "input": relative_input, "point": point, "baselineCount": baseline, "recentCount": recent})
        state["runs"] = state["runs"][-50:]
        self._write_json(self.state_path, state)
        self.audit("analysis.run", {"cardId": card.card_id, "input": relative_input, "point": point, "baselineCount": baseline, "recentCount": recent})
        return {"card": raw, "summary": self.summary()}

    def workflow_transition(self, payload: dict[str, Any]) -> dict[str, Any]:
        action = str(payload.get("action", "")).strip()
        actor = str(payload.get("actor", "")).strip()
        note = str(payload.get("note", "")).strip()
        evidence_ids = payload.get("evidenceIds", [])
        task_ids = payload.get("taskIds", [])
        if not actor:
            raise ValueError("actor 是必填项")
        card = self.load_card()
        workflow = RiskCaseWorkflow.for_card(card)
        previous_events = []
        for event in card.workflow.get("events", []):
            try:
                previous_events.append(WorkflowTransition(**event))
            except TypeError:
                continue
        workflow._events = previous_events
        event = workflow.transition(action, actor=actor, note=note, evidence_ids=evidence_ids, task_ids=task_ids)
        self._write_json(self._card_path(), card.to_dict())
        self.audit("workflow." + action, {"cardId": card.card_id, "actor": actor, "eventId": event.event_id, "toStatus": card.status})
        return {"event": event.to_dict(), "card": card.to_dict(), "summary": self.summary()}

    def history(self) -> list[dict[str, Any]]:
        return list(reversed(self._state().get("runs", [])))

    def api_catalog(self) -> list[dict[str, Any]]:
        return [
            {"method": "GET", "path": "/api/health", "purpose": "服务与版本健康检查"},
            {"method": "GET", "path": "/api/summary", "purpose": "当前风险卡、工作流、健康度总览"},
            {"method": "GET", "path": "/api/capabilities", "purpose": "已实现能力与未完成项"},
            {"method": "GET", "path": "/api/data", "purpose": "数据、输出与网页资产清单"},
            {"method": "GET", "path": "/api/knowledge", "purpose": "知识库条目与修订指纹"},
            {"method": "GET", "path": "/api/integration", "purpose": "飞书配置存在性与安全边界"},
            {"method": "GET", "path": "/api/monitor/status", "purpose": "实时数据源、Key 与轮询状态"},
            {"method": "POST", "path": "/api/monitor/config", "purpose": "保存非敏感配置并在进程内设置 Key"},
            {"method": "POST", "path": "/api/monitor/test", "purpose": "测试上游 JSON API 连接"},
            {"method": "POST", "path": "/api/monitor/start", "purpose": "启动服务端持续轮询"},
            {"method": "POST", "path": "/api/monitor/stop", "purpose": "停止持续轮询"},
            {"method": "POST", "path": "/api/ai/chat", "purpose": "使用后台 API 回答实时分析问题"},
            {"method": "GET", "path": "/api/simulator/status", "purpose": "临时拧紧数据与实时分析状态"},
            {"method": "POST", "path": "/api/simulator/config", "purpose": "配置模拟场景、强度和频率"},
            {"method": "POST", "path": "/api/simulator/generate", "purpose": "立即生成一批临时拧紧数据并分析"},
            {"method": "POST", "path": "/api/simulator/start", "purpose": "开始持续生成和分析"},
            {"method": "POST", "path": "/api/simulator/stop", "purpose": "停止临时数据生成"},
            {"method": "GET", "path": "/api/live-analysis", "purpose": "官网读取的实时分析结果"},
            {"method": "GET", "path": "/api/risk/card", "purpose": "当前风险卡原始 JSON"},
            {"method": "GET", "path": "/api/risk/history", "purpose": "后台分析运行历史"},
            {"method": "POST", "path": "/api/risk/run", "purpose": "运行一次本地风险分析"},
            {"method": "POST", "path": "/api/workflow/transition", "purpose": "执行受门禁保护的工作流动作"},
            {"method": "GET", "path": "/api/audit", "purpose": "管理员操作审计日志"},
            {"method": "GET", "path": "/api/openapi", "purpose": "本地 API 目录"},
        ]


class AdminRequestHandler(BaseHTTPRequestHandler):
    service = AdminService()

    def log_message(self, format: str, *args: Any) -> None:
        sys.stderr.write("[admin-api] " + (format % args) + "\n")

    def _send_json(self, status: int, payload: Any) -> None:
        data = json.dumps(payload, ensure_ascii=False, default=_json_default).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def _send_error(self, status: int, message: str) -> None:
        self._send_json(status, {"error": message, "status": status})

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 1_000_000:
            raise ValueError("请求体过大")
        if not length:
            return {}
        value = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("请求体必须是 JSON 对象")
        return value

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        try:
            if path == "/api/health":
                self._send_json(200, {"status": "ok", "service": "torque-guard-admin", "time": _now(), "python": sys.version.split()[0], "root": str(ROOT)})
            elif path == "/api/summary":
                self._send_json(200, self.service.summary())
            elif path == "/api/capabilities":
                self._send_json(200, {"capabilities": CAPABILITIES})
            elif path == "/api/data":
                self._send_json(200, {"files": self.service.data_inventory(), "events": self.service._event_stats()})
            elif path == "/api/knowledge":
                self._send_json(200, self.service.knowledge_summary())
            elif path == "/api/integration":
                self._send_json(200, self.service.integration_status())
            elif path == "/api/monitor/status":
                self._send_json(200, self.service.monitor_status())
            elif path == "/api/simulator/status" or path == "/api/live-analysis":
                self._send_json(200, self.service.simulator_status())
            elif path == "/api/risk/card":
                self._send_json(200, self.service.load_card_raw())
            elif path == "/api/risk/history":
                self._send_json(200, {"runs": self.service.history()})
            elif path == "/api/audit":
                self._send_json(200, {"records": self.service.audit_records()})
            elif path == "/api/openapi":
                self._send_json(200, {"service": "torque-guard-admin", "baseUrl": "/api", "endpoints": self.service.api_catalog()})
            elif path == "/" or path == "/admin.html":
                self._serve_static("admin.html")
            else:
                self._serve_static(path.lstrip("/"))
        except Exception as exc:
            self._send_error(500, str(exc))

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        try:
            payload = self._body()
            if path == "/api/risk/run":
                self._send_json(200, self.service.run_analysis(payload))
            elif path == "/api/workflow/transition":
                self._send_json(200, self.service.workflow_transition(payload))
            elif path == "/api/monitor/config":
                self._send_json(200, self.service.configure_monitor(payload))
            elif path == "/api/monitor/test":
                self._send_json(200, self.service.test_monitor())
            elif path == "/api/monitor/start":
                self._send_json(200, self.service.start_monitor())
            elif path == "/api/monitor/stop":
                self._send_json(200, self.service.stop_monitor())
            elif path == "/api/simulator/config":
                self._send_json(200, self.service.configure_simulator(payload))
            elif path == "/api/simulator/generate":
                self._send_json(200, self.service.generate_simulator_data(payload))
            elif path == "/api/simulator/start":
                self._send_json(200, self.service.start_simulator())
            elif path == "/api/simulator/stop":
                self._send_json(200, self.service.stop_simulator())
            elif path == "/api/ai/chat":
                self._send_json(200, self.service.ai_chat(payload))
            else:
                self._send_error(404, "未知 POST API")
        except (ValueError, json.JSONDecodeError) as exc:
            self._send_error(400, str(exc))
        except Exception as exc:
            self._send_error(500, str(exc))

    def _serve_static(self, relative: str) -> None:
        requested = (DOCS_ROOT / relative).resolve()
        if DOCS_ROOT not in requested.parents or not requested.is_file():
            self._send_error(404, "页面或资源不存在")
            return
        data = requested.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(str(requested))[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def serve(host: str = "127.0.0.1", port: int = 8010) -> None:
    server = ThreadingHTTPServer((host, port), AdminRequestHandler)
    print(f"TorqueGuard admin: http://{host}:{port}/admin.html")
    print(f"API catalog: http://{host}:{port}/api/openapi")
    server.serve_forever()


if __name__ == "__main__":
    serve()
