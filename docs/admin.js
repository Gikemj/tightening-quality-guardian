const $ = (selector) => document.querySelector(selector);
const isPublicPreview = typeof window !== "undefined"
  && (window.location.hostname.endsWith("github.io") || window.location.protocol === "file:");
let adminBackendMode = isPublicPreview ? "static" : "unknown";
let staticAdminPromise = null;

async function loadStaticAdminState() {
  if (!staticAdminPromise) {
    staticAdminPromise = Promise.all([
      fetch("./data/risk_card.json").then((response) => response.json()),
      fetch("./data/risk_result.json").then((response) => response.json()),
      fetch("./data/demo_series.json").then((response) => response.json()),
    ]).then(([card, result, series]) => ({
      card,
      result,
      series,
      workflow: { status: card.status || "awaiting_engineer_review", events: [], allowedActions: card.workflow?.allowed_actions || ["approve", "reject"], humanApprovalRequired: card.workflow?.human_approval_required !== false, automaticStopLineAllowed: false },
      audit: [{ at: "2026-08-17T09:00:00+08:00", action: "公开预览已载入", detail: { mode: "browser_static_preview", source: "docs/data" } }],
      monitor: { configured: false, running: false, config: {}, samples: [], lastSample: null },
      simulator: { active: false, running: false, sequence: 0, scenario: "hidden_torque_drift", strength: 1, intervalSeconds: 4, latestEvents: [], card: null },
    }));
  }
  return staticAdminPromise;
}

function staticSummary(state) {
  const card = state.card || {};
  const result = state.result || {};
  return {
    generatedAt: "2026-08-17T09:00:00+08:00",
    case: {
      cardId: card.card_id || "RISK-CARD-DEMO",
      riskLevel: card.risk_level || "medium",
      riskScore: card.risk_score ?? "—",
      status: state.workflow.status,
      stationId: card.station_id || "ST-FAS-07",
      toolId: card.tool_id || "TOOL-TG-07",
      fasteningPoint: card.fastening_point || "P03",
      analysisDisposition: card.inference || "公开合成风险卡已加载",
    },
    health: {
      evidenceCount: (card.evidence || []).length,
      traceCount: (card.agent_trace || []).length,
      failedTraceCount: (card.agent_trace || []).filter((item) => item.status === "failed").length,
      knowledgeRevision: card.analysis_provenance?.knowledge_revision || "sha256:synthetic-demo",
    },
    workflow: {
      status: state.workflow.status,
      events: state.workflow.events,
      allowedActions: state.workflow.allowedActions,
      humanApprovalRequired: state.workflow.humanApprovalRequired,
      automaticStopLineAllowed: false,
    },
    knowledge: { status: "ready", revision: card.analysis_provenance?.knowledge_revision || "sha256:synthetic-demo", controlPlans: 1, pfmeaRows: 1, historyCases: 1, ontologyNodes: 12, alarmCodes: 4 },
    result,
  };
}

function staticSimulatorStatus(state) {
  if (!state.simulator.active) return { ...state.simulator, latestEvents: [], card: null };
  const base = state.card || {};
  const sequence = state.simulator.sequence || 1;
  const card = { ...base, card_id: `${base.card_id || "RISK-CARD"}-BROWSER-${String(sequence).padStart(2, "0")}`, created_at: "2026-08-17T09:00:00+08:00" };
  const events = (state.series || []).slice(-24).map((item, index) => ({ ...item, torque_nm: Number((Number(item.torque_nm) + ((sequence % 3) - 1) * 0.02 + (index % 6 === 0 ? 0.03 : 0)).toFixed(3)) }));
  return { ...state.simulator, card, latestEvents: events };
}

async function staticApi(path, options = {}) {
  const state = await loadStaticAdminState();
  const method = String(options.method || "GET").toUpperCase();
  if (path === "/api/summary") return staticSummary(state);
  if (path === "/api/risk/card") return state.card;
  if (path === "/api/capabilities") return { capabilities: [
    { name: "窗口对照与 SPC", status: "implemented", details: "同口径基线、最近窗口和多信号规则均有权威结果。" },
    { name: "证据关系链", status: "implemented", details: "设备、拧紧点、知识文件与候选原因可追溯。" },
    { name: "人工工作流", status: "guarded", details: "审批、验证、回写均需具名工程师，公开页面只生成预览。" },
    { name: "外部集成", status: "partial", details: "服务端保留受控接口，当前演示不写入真实租户。" },
  ] };
  if (path === "/api/knowledge") return staticSummary(state).knowledge;
  if (path === "/api/data") return { events: { records: (state.series || []).length, path: "data/tightening_events_demo.csv" }, files: [{ path: "data/tightening_events_demo.csv", kind: "synthetic events", size: 42000, modified: "2026-08-17T09:00:00+08:00" }, { path: "outputs/risk_card.json", kind: "risk card", size: 9000, modified: "2026-08-17T09:00:00+08:00" }] };
  if (path === "/api/integration") return { mode: "preview", liveWriteEnabled: false, configuredKeys: [], requiredKeys: ["FEISHU_APP_ID", "FEISHU_APP_SECRET"], message: "公开管理员工作台不配置企业凭证，不执行外部写入。", terra: { configured: false, provider: "Hetune（OpenAI 兼容接口）", model: "gpt-5.6-sol", baseUrl: "https://hetune.top", keyLoaded: false, serverSideOnly: true, message: "公开页面不加载密钥，使用浏览器内确定性摘要。" } };
  if (path === "/api/openapi") return { endpoints: [
    { method: "GET", path: "/api/summary", purpose: "读取风险、健康与工作流摘要" },
    { method: "POST", path: "/api/risk/run", purpose: "运行公开风险分析回放" },
    { method: "POST", path: "/api/workflow/transition", purpose: "校验人工门禁状态变化" },
    { method: "GET", path: "/api/live-analysis", purpose: "读取临时合成批次" },
  ] };
  if (path === "/api/audit") return { records: state.audit };
  if (path === "/api/monitor/status") return state.monitor;
  if (path === "/api/monitor/config" && method === "POST") {
    state.monitor.configured = true;
    state.monitor.config = JSON.parse(options.body || "{}");
    state.audit.unshift({ at: "2026-08-17T09:00:00+08:00", action: "保存公开监测配置", detail: { name: state.monitor.config.name || "项目实时数据", mode: "synthetic_preview" } });
    return state.monitor;
  }
  if (path === "/api/monitor/test" && method === "POST") {
    const sample = { at: "2026-08-17T09:00:00+08:00", status: 200, durationMs: 18, data: { mode: "synthetic_preview", healthy: true, source: "browser_static_preview" } };
    state.monitor.lastSample = sample;
    state.monitor.samples = [sample, ...(state.monitor.samples || [])].slice(0, 5);
    state.audit.unshift({ at: sample.at, action: "测试公开监测连接", detail: { status: sample.status, durationMs: sample.durationMs } });
    return { status: state.monitor, sample };
  }
  if (path === "/api/monitor/start" && method === "POST") { state.monitor.running = true; state.audit.unshift({ at: "2026-08-17T09:00:00+08:00", action: "启动公开监测回放", detail: { mode: "synthetic_preview" } }); return state.monitor; }
  if (path === "/api/monitor/stop" && method === "POST") { state.monitor.running = false; state.audit.unshift({ at: "2026-08-17T09:00:00+08:00", action: "停止公开监测回放", detail: { mode: "synthetic_preview" } }); return state.monitor; }
  if (path === "/api/simulator/status") return staticSimulatorStatus(state);
  if (path === "/api/simulator/config" && method === "POST") { Object.assign(state.simulator, JSON.parse(options.body || "{}")); return staticSimulatorStatus(state); }
  if (path === "/api/simulator/generate" && method === "POST") { Object.assign(state.simulator, JSON.parse(options.body || "{}")); state.simulator.active = true; state.simulator.sequence += 1; state.simulator.running = false; state.audit.unshift({ at: "2026-08-17T09:00:00+08:00", action: "生成公开模拟批次", detail: { sequence: state.simulator.sequence, scenario: state.simulator.scenario } }); return staticSimulatorStatus(state); }
  if (path === "/api/simulator/start" && method === "POST") { state.simulator.active = true; state.simulator.running = true; state.simulator.sequence = Math.max(1, state.simulator.sequence + 1); state.audit.unshift({ at: "2026-08-17T09:00:00+08:00", action: "启动公开模拟器", detail: { sequence: state.simulator.sequence, scenario: state.simulator.scenario } }); return staticSimulatorStatus(state); }
  if (path === "/api/simulator/stop" && method === "POST") { state.simulator.running = false; state.audit.unshift({ at: "2026-08-17T09:00:00+08:00", action: "停止公开模拟器", detail: { sequence: state.simulator.sequence } }); return staticSimulatorStatus(state); }
  if (path === "/api/risk/run" && method === "POST") { state.audit.unshift({ at: "2026-08-17T09:00:00+08:00", action: "运行公开风险分析", detail: { point: JSON.parse(options.body || "{}").point || "P03", mode: "browser_static_preview" } }); return { card: state.card, summary: staticSummary(state) }; }
  if (path === "/api/workflow/transition" && method === "POST") {
    const body = JSON.parse(options.body || "{}");
    const from = state.workflow.status;
    const transitions = {
      awaiting_engineer_review: { approve: "approved", reject: "rejected" },
      approved: { create_tasks: "tasks_created", start_verification: "verification_in_progress", reject: "rejected" },
      tasks_created: { start_verification: "verification_in_progress", reject: "rejected" },
      verification_in_progress: { pass_verification: "verified", fail_verification: "tasks_created" },
      verified: { close: "closed", reopen: "awaiting_engineer_review" },
      rejected: { resubmit: "awaiting_engineer_review" },
      closed: { reopen: "awaiting_engineer_review" },
    };
    const next = transitions[from]?.[body.action] || from;
    if (next === from && body.action !== "preview") throw new Error(`当前状态不允许动作：${body.action || "未选择"}`);
    state.workflow.status = next;
    state.workflow.events.push({ occurred_at: "2026-08-17T09:00:00+08:00", action: body.action || "preview", actor: body.actor || "demo.reviewer", from_status: from, to_status: next, note: body.note || "浏览器公开预览" });
    state.workflow.allowedActions = {
      awaiting_engineer_review: ["approve", "reject"],
      approved: ["create_tasks", "start_verification", "reject"],
      tasks_created: ["start_verification", "reject"],
      verification_in_progress: ["pass_verification", "fail_verification"],
      verified: ["close", "reopen"],
      rejected: ["resubmit"],
      closed: ["reopen"],
    }[next] || [];
    state.audit.unshift({ at: "2026-08-17T09:00:00+08:00", action: `工作流：${body.action || "preview"}`, detail: { from, to: next, actor: body.actor || "demo.reviewer" } });
    return { card: { ...state.card, status: next }, summary: staticSummary(state) };
  }
  return {};
}

const api = async (path, options = {}) => {
  if (adminBackendMode !== "static") {
    try {
      const response = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options, signal: AbortSignal.timeout?.(1500) });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.error || `请求失败 (${response.status})`);
      adminBackendMode = "backend";
      return payload;
    } catch (error) {
      adminBackendMode = "static";
    }
  }
  return staticApi(path, options);
};

const statusLabels = { implemented: "已实现", partial: "部分完成", guarded: "受保护", missing: "未完成" };
const workflowLabels = {
  monitoring_only: "仅监控",
  awaiting_engineer_review: "待工程师审批",
  approved: "已审批",
  rejected: "已驳回",
  tasks_created: "任务已创建",
  verification_in_progress: "验证中",
  verified: "已验证",
  closed: "已结案",
};
const actionLabels = {
  approve: "审批通过",
  reject: "驳回",
  resubmit: "重新提交",
  create_tasks: "创建任务",
  start_verification: "开始验证",
  pass_verification: "验证通过",
  fail_verification: "验证失败并回到任务",
  close: "结案",
  reopen: "重新打开",
};

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[char]));
}

function showToast(message, isError = false) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.style.background = isError ? "#a62932" : "#17232d";
  toast.classList.add("visible");
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => toast.classList.remove("visible"), 3200);
}

function formatTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { hour12: false });
}

function statusClass(status) {
  return `status-${status}`;
}

function renderCapabilities(capabilities) {
  $("#capability-grid").innerHTML = capabilities.map((item) => `<article class="capability"><div class="capability-head"><strong>${escapeHtml(item.name)}</strong><span class="status-chip ${statusClass(item.status)}">${statusLabels[item.status] || escapeHtml(item.status)}</span></div><p>${escapeHtml(item.details)}</p></article>`).join("");
}

function renderSummary(summary) {
  const { case: currentCase, health, workflow, knowledge } = summary;
  $("#metric-risk").textContent = `${String(currentCase.riskLevel || "—").toUpperCase()} ${currentCase.riskScore ?? "—"}`;
  $("#metric-risk-note").textContent = `${currentCase.stationId || "—"} / ${currentCase.fasteningPoint || "—"}`;
  $("#metric-workflow").textContent = workflowLabels[workflow.status] || workflow.status || "—";
  $("#metric-workflow-note").textContent = workflow.humanApprovalRequired ? "需要具名人工审批" : "当前无审批动作";
  $("#metric-evidence").textContent = `${health.evidenceCount} / ${health.traceCount}`;
  $("#metric-trace").textContent = health.failedTraceCount ? `${health.failedTraceCount} 条调用失败` : "调用轨迹完整";
  $("#metric-knowledge").textContent = knowledge.status === "ready" ? "READY" : "ERROR";
  $("#metric-revision").textContent = (health.knowledgeRevision || "—").slice(0, 22);
  renderCase(summary);
  renderWorkflowActions(workflow.allowedActions || []);
  renderWorkflowEvents(workflow.events || []);
}

function renderCase(summary) {
  const currentCase = summary.case;
  const card = summary.card || {};
  $("#case-summary").innerHTML = `<div class="case-grid"><div class="case-item"><span>风险卡</span><strong class="mono">${escapeHtml(currentCase.cardId)}</strong><small>${escapeHtml(currentCase.stationId)} / ${escapeHtml(currentCase.toolId)}</small></div><div class="case-item"><span>风险评分</span><strong>${escapeHtml(currentCase.riskScore)} / 100</strong><small>${escapeHtml(currentCase.riskLevel)}</small></div><div class="case-item"><span>工作流</span><strong>${escapeHtml(workflowLabels[currentCase.status] || currentCase.status)}</strong><small>${summary.workflow.automaticStopLineAllowed ? "边界异常" : "自动停线禁用"}</small></div></div><p class="case-inference"><strong>分析结论：</strong>${escapeHtml(card.inference || currentCase.analysisDisposition || "当前风险卡已加载，可通过右侧表单提交门禁动作。")}<br /><span class="muted">证据 ${summary.health.evidenceCount} 条 · 调用轨迹 ${summary.health.traceCount} 条 · 知识修订 ${escapeHtml((summary.health.knowledgeRevision || "—").slice(0, 30))}</span></p>`;
}

function renderWorkflowActions(actions) {
  const select = $("#workflow-action");
  select.innerHTML = actions.length ? actions.map((action) => `<option value="${escapeHtml(action)}">${escapeHtml(actionLabels[action] || action)}</option>`).join("") : `<option value="">当前状态无可用动作</option>`;
  select.disabled = !actions.length;
  $("#workflow-form button[type=submit]").disabled = !actions.length;
}

function renderWorkflowEvents(events) {
  const body = $("#workflow-events");
  if (!events.length) {
    body.innerHTML = `<tr><td colspan="5" class="empty-state">尚无工作流事件</td></tr>`;
    return;
  }
  body.innerHTML = [...events].reverse().map((event) => `<tr><td class="mono">${escapeHtml(formatTime(event.occurred_at))}</td><td><strong>${escapeHtml(actionLabels[event.action] || event.action)}</strong></td><td>${escapeHtml(event.actor)}</td><td>${escapeHtml(workflowLabels[event.from_status] || event.from_status)} → ${escapeHtml(workflowLabels[event.to_status] || event.to_status)}</td><td>${escapeHtml(event.note || "—")}</td></tr>`).join("");
}

function renderKnowledge(data) {
  const ready = data.status === "ready";
  $("#knowledge-status").textContent = ready ? "READY" : "ERROR";
  $("#knowledge-status").className = `status-chip ${ready ? "status-implemented" : "status-missing"}`;
  $("#knowledge-details").innerHTML = ready ? [["修订指纹", data.revision], ["控制计划", `${data.controlPlans} 条`], ["PFMEA", `${data.pfmeaRows} 条`], ["历史案例", `${data.historyCases} 条`], ["关系图谱节点", `${data.ontologyNodes} 个`], ["告警字典", `${data.alarmCodes} 条`]].map(([label, value]) => `<div><dt>${label}</dt><dd>${escapeHtml(value)}</dd></div>`).join("") : `<div><dt>加载错误</dt><dd class="bad">${escapeHtml(data.error)}</dd></div>`;
}

function renderData(data) {
  $("#event-count").textContent = `${data.events.records} 条事件`;
  $("#data-files").innerHTML = data.files.map((file) => `<tr><td class="mono">${escapeHtml(file.path)}</td><td>${escapeHtml(file.kind)}</td><td>${(file.size / 1024).toFixed(1)} KB</td><td class="mono">${escapeHtml(formatTime(file.modified))}</td></tr>`).join("");
}

function renderIntegration(data) {
  const configured = data.configuredKeys?.length || 0;
  $("#feishu-status").textContent = configured ? `${configured}/${data.requiredKeys.length} 已配置` : "PREVIEW";
  $("#feishu-status").className = `status-chip ${configured === data.requiredKeys.length ? "status-implemented" : "status-guarded"}`;
  $("#feishu-details").innerHTML = [["当前模式", data.mode], ["浏览器写入", data.liveWriteEnabled ? "允许" : "明确禁用"], ["已发现配置", configured ? data.configuredKeys.join(", ") : "无（安全）"], ["边界", data.message]].map(([label, value]) => `<div><dt>${label}</dt><dd>${escapeHtml(value)}</dd></div>`).join("");
  const terra = data.terra || {};
  const terraReady = terra.configured === true && terra.keyLoaded === true;
  $("#terra-status").textContent = terraReady ? "SERVER READY" : "BROWSER FALLBACK";
  $("#terra-status").className = `status-chip ${terraReady ? "status-implemented" : "status-guarded"}`;
  $("#terra-details").innerHTML = [["服务商", terra.provider || "Hetune（OpenAI 兼容接口）"], ["模型", terra.model || "gpt-5.6-sol"], ["Base URL", terra.baseUrl || "https://hetune.top"], ["密钥状态", terraReady ? "已从服务端环境载入" : "未载入（安全回退）"], ["输出边界", terra.serverSideOnly === false ? "异常" : "服务端调用，人工审批"]].map(([label, value]) => `<div><dt>${label}</dt><dd>${escapeHtml(value)}</dd></div>`).join("");
}

function renderMonitor(status, hydrateForm = false) {
  const config = status.config || {};
  const form = $("#monitor-form");
  if (hydrateForm && status.configured) {
    form.elements.name.value = config.name || "项目实时数据";
    form.elements.apiUrl.value = config.apiUrl || "";
    form.elements.chatUrl.value = config.chatUrl || "";
    form.elements.authType.value = config.authType || "bearer";
    form.elements.intervalSeconds.value = config.intervalSeconds || 10;
    form.elements.timeoutSeconds.value = config.timeoutSeconds || 8;
  }
  const noAuth = form.elements.authType.value === "none";
  $("#monitor-key-field").hidden = noAuth;
  $("#monitor-key-field input").disabled = noAuth;
  const state = status.running ? "正在实时监测" : status.configured ? "已配置，未运行" : "未配置";
  $("#monitor-state").textContent = state;
  $("#monitor-endpoint").textContent = config.apiUrl || "等待填写 API 地址";
  $("#monitor-dot").className = `live-dot ${status.lastError ? "error" : status.running ? "running" : ""}`;
  $("#monitor-start").disabled = status.running || !status.configured;
  $("#monitor-stop").disabled = !status.running;
  const sample = status.lastSample;
  $("#monitor-latency").textContent = sample ? `${sample.durationMs} ms` : "—";
  $("#monitor-updated").textContent = sample ? formatTime(sample.at) : "—";
  $("#monitor-sample-count").textContent = `${status.samples?.length || 0} 条近期样本`;
  $("#monitor-output").textContent = sample ? JSON.stringify(sample.data, null, 2) : "保存并测试连接后，这里显示上游 API 返回的最新项目数据。";
  const error = $("#monitor-error");
  error.hidden = !status.lastError;
  error.textContent = status.lastError || "";
  const samples = status.samples || [];
  $("#monitor-samples").innerHTML = samples.length ? samples.map((item) => `<tr><td class="mono">${escapeHtml(formatTime(item.at))}</td><td>${escapeHtml(item.status)}</td><td>${escapeHtml(item.durationMs)} ms</td><td class="good">成功</td></tr>`).join("") : `<tr><td colspan="4" class="empty-state">尚无实时样本</td></tr>`;
}

async function loadMonitorStatus(hydrateForm = false) {
  const status = await api("/api/monitor/status");
  renderMonitor(status, hydrateForm);
  return status;
}

function monitorPayload() {
  const payload = formPayload($("#monitor-form"));
  payload.intervalSeconds = Number(payload.intervalSeconds);
  payload.timeoutSeconds = Number(payload.timeoutSeconds);
  return payload;
}

async function saveMonitorConfig() {
  const status = await api("/api/monitor/config", { method: "POST", body: JSON.stringify(monitorPayload()) });
  renderMonitor(status);
  return status;
}

function renderSimulator(status) {
  const card = status.card || {};
  const latest = status.latestEvents?.[status.latestEvents.length - 1] || {};
  const active = Boolean(status.running);
  $("#simulator-state").textContent = active ? "正在持续生成" : status.active ? "已生成，未持续" : "尚未生成";
  $("#simulator-scenario").textContent = `${status.scenario || "—"} · 强度 ${status.strength ?? "—"} · 每 ${status.intervalSeconds ?? "—"} 秒`;
  $("#simulator-dot").className = `live-dot ${status.lastError ? "error" : active ? "running" : ""}`;
  $("#simulator-risk").textContent = card.risk_score == null ? "—" : `${String(card.risk_level || "").toUpperCase()} ${card.risk_score}`;
  $("#simulator-sequence").textContent = String(status.sequence || 0);
  $("#simulator-card-id").textContent = card.card_id || "—";
  $("#simulator-workflow").textContent = workflowLabels[card.status] || card.status || "—";
  $("#simulator-torque").textContent = latest.torque_nm == null ? "—" : `${latest.torque_nm} N·m`;
  $("#simulator-updated").textContent = card.created_at ? formatTime(card.created_at) : "—";
  $("#simulator-start").disabled = active;
  $("#simulator-stop").disabled = !active;
  const error = $("#simulator-error");
  error.hidden = !status.lastError;
  error.textContent = status.lastError || "";
}

async function loadSimulatorStatus() {
  const status = await api("/api/simulator/status");
  renderSimulator(status);
  return status;
}

function simulatorPayload() {
  const payload = formPayload($("#simulator-form"));
  payload.strength = Number(payload.strength);
  payload.intervalSeconds = Number(payload.intervalSeconds);
  return payload;
}

async function loadApiCatalog() {
  const data = await api("/api/openapi");
  $("#api-catalog").innerHTML = data.endpoints.map((item) => `<tr><td><span class="status-chip ${item.method === "POST" ? "status-partial" : "status-implemented"}">${item.method}</span></td><td class="mono">${escapeHtml(item.path)}</td><td>${escapeHtml(item.purpose)}</td></tr>`).join("");
}

async function loadAudit() {
  const data = await api("/api/audit");
  $("#audit-events").innerHTML = data.records.length ? data.records.map((record) => `<tr><td class="mono">${escapeHtml(formatTime(record.at))}</td><td><strong>${escapeHtml(record.action)}</strong></td><td><code>${escapeHtml(JSON.stringify(record.detail))}</code></td></tr>`).join("") : `<tr><td colspan="3" class="empty-state">尚无后台操作</td></tr>`;
}

async function loadAll() {
  const summary = await api("/api/summary");
  const capabilities = await api("/api/capabilities");
  renderSummary(summary);
  renderCapabilities(capabilities.capabilities);
  renderCase({ ...summary, card: await api("/api/risk/card") });
  renderKnowledge(summary.knowledge);
  renderData(await api("/api/data"));
  renderIntegration(await api("/api/integration"));
  await Promise.all([loadApiCatalog(), loadAudit(), loadMonitorStatus(true), loadSimulatorStatus()]);
  $("#health-label").textContent = adminBackendMode === "backend"
    ? `API 正常 · ${formatTime(summary.generatedAt)}`
    : `静态预览已就绪 · ${formatTime(summary.generatedAt)}`;
}

function formPayload(form) {
  return Object.fromEntries(new FormData(form).entries());
}

$("#refresh-button").addEventListener("click", async () => {
  try { await loadAll(); showToast("后台状态已刷新"); } catch (error) { showToast(error.message, true); }
});

$("#analysis-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = $("#run-analysis");
  button.disabled = true;
  $("#run-state").textContent = "运行中…";
  try {
    const payload = formPayload(event.currentTarget);
    payload.baselineCount = Number(payload.baselineCount);
    payload.recentCount = Number(payload.recentCount);
    const result = await api("/api/risk/run", { method: "POST", body: JSON.stringify(payload) });
    $("#run-state").textContent = "已完成";
    $("#run-output").textContent = JSON.stringify({ cardId: result.card.card_id, riskLevel: result.card.risk_level, riskScore: result.card.risk_score, status: result.card.status, evidence: result.card.evidence.length, traces: result.card.agent_trace.length }, null, 2);
    renderSummary(result.summary);
    renderCase({ ...result.summary, card: result.card });
    await loadAudit();
    showToast("公开风险分析回放已完成");
  } catch (error) {
    $("#run-state").textContent = "失败";
    $("#run-output").textContent = error.message;
    showToast(error.message, true);
  } finally { button.disabled = false; }
});

$("#workflow-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const payload = formPayload(event.currentTarget);
  payload.evidenceIds = payload.evidenceIds ? payload.evidenceIds.split(",").map((item) => item.trim()).filter(Boolean) : [];
  payload.taskIds = payload.taskIds ? payload.taskIds.split(",").map((item) => item.trim()).filter(Boolean) : [];
  try {
    const result = await api("/api/workflow/transition", { method: "POST", body: JSON.stringify(payload) });
    renderSummary(result.summary);
    renderCase({ ...result.summary, card: result.card });
    await loadAudit();
    showToast(`工作流已变更为：${workflowLabels[result.card.status] || result.card.status}`);
    event.currentTarget.reset();
  } catch (error) { showToast(error.message, true); }
});

$("#reload-data").addEventListener("click", async () => {
  try { renderKnowledge(await api("/api/knowledge")); renderData(await api("/api/data")); showToast("数据与知识资产已重新读取"); } catch (error) { showToast(error.message, true); }
});
$("#reload-audit").addEventListener("click", async () => { try { await loadAudit(); showToast("审计日志已刷新"); } catch (error) { showToast(error.message, true); } });

$("#monitor-auth").addEventListener("change", (event) => {
  const disabled = event.currentTarget.value === "none";
  $("#monitor-key-field").hidden = disabled;
  $("#monitor-key-field input").disabled = disabled;
});

$("#monitor-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = $("#monitor-save");
  button.disabled = true;
  try {
    await saveMonitorConfig();
    showToast("数据源配置已保存，Key 仅保存在当前进程内");
  } catch (error) { showToast(error.message, true); }
  finally { button.disabled = false; }
});

$("#monitor-test").addEventListener("click", async () => {
  const button = $("#monitor-test");
  button.disabled = true;
  try {
    await saveMonitorConfig();
    const result = await api("/api/monitor/test", { method: "POST", body: "{}" });
    renderMonitor(result.status);
    showToast(`连接成功，响应 ${result.sample.status}，耗时 ${result.sample.durationMs} ms`);
  } catch (error) { await loadMonitorStatus().catch(() => {}); showToast(error.message, true); }
  finally { button.disabled = false; }
});

$("#monitor-start").addEventListener("click", async () => {
  const button = $("#monitor-start");
  button.disabled = true;
  try {
    await saveMonitorConfig();
    const status = await api("/api/monitor/start", { method: "POST", body: "{}" });
    renderMonitor(status);
    showToast("实时监测已启动");
  } catch (error) { showToast(error.message, true); }
  finally { button.disabled = false; }
});

$("#monitor-stop").addEventListener("click", async () => {
  const button = $("#monitor-stop");
  button.disabled = true;
  try {
    const status = await api("/api/monitor/stop", { method: "POST", body: "{}" });
    renderMonitor(status);
    showToast("实时监测已停止");
  } catch (error) { showToast(error.message, true); }
  finally { button.disabled = false; }
});

$("#simulator-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = $("#simulator-generate");
  button.disabled = true;
  try {
    const status = await api("/api/simulator/generate", { method: "POST", body: JSON.stringify(simulatorPayload()) });
    renderSimulator(status);
    showToast(`已生成第 ${status.sequence} 批临时数据，风险 ${String(status.card?.risk_level || "").toUpperCase()} ${status.card?.risk_score ?? "—"}`);
  } catch (error) { showToast(error.message, true); }
  finally { button.disabled = false; }
});

$("#simulator-start").addEventListener("click", async () => {
  const button = $("#simulator-start");
  button.disabled = true;
  try {
    await api("/api/simulator/config", { method: "POST", body: JSON.stringify(simulatorPayload()) });
    const status = await api("/api/simulator/start", { method: "POST", body: "{}" });
    renderSimulator(status);
    showToast("临时数据持续生成已启动");
  } catch (error) { showToast(error.message, true); }
  finally { button.disabled = false; }
});

$("#simulator-stop").addEventListener("click", async () => {
  const button = $("#simulator-stop");
  button.disabled = true;
  try {
    const status = await api("/api/simulator/stop", { method: "POST", body: "{}" });
    renderSimulator(status);
    showToast("临时数据生成已停止");
  } catch (error) { showToast(error.message, true); }
  finally { button.disabled = false; }
});

document.querySelectorAll(".nav-link").forEach((link) => link.addEventListener("click", () => {
  document.querySelectorAll(".nav-link").forEach((item) => item.classList.toggle("active", item === link));
}));

loadAll().then(() => {
  window.setInterval(() => Promise.all([loadMonitorStatus(), loadSimulatorStatus()]).catch(() => {}), 2000);
}).catch((error) => {
  $("#health-label").textContent = "静态预览不可用";
  $("#run-output").textContent = `公开合成资产读取失败。\n\n${error.message}`;
  showToast("公开合成资产读取失败", true);
});
