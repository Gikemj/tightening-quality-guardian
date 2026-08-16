const $ = (selector, root = document) => root.querySelector(selector);

const escapeHtml = (value) => String(value ?? "")
  .replaceAll("&", "&amp;")
  .replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;")
  .replaceAll("'", "&#039;");

const strengthLabel = { direct: "直接事实", candidate: "关联候选", gap: "信息缺口" };
const processLevelLabel = { high: "高风险", medium: "中风险", low: "低风险" };
const processStrengthLabel = { direct: "直接数据", document: "受控文件", analogy: "历史类比" };
const runSteps = ["sense", "detect", "retrieve", "card", "approve", "verify", "writeback"];
const processPhaseLabel = {
  idle: "待开始主动研判",
  running: "主动研判运行中",
  awaiting_engineer_review: "等待具名工程师审批",
  needs_more_evidence: "退回补证，尚未进入现场验证",
  verification_pending: "已通过本地审批，等待现场验证",
  verified_local_demo: "现场验证结果已回填，等待知识审核",
  knowledge_reviewed_local_demo: "案例已完成本地审核回写",
  monitoring_only: "稳定窗口，保持常规监控",
};
const PUBLIC_DEMO_WRITE_BOUNDARY = "公开演示没有发起飞书写入";

let publicPayload = null;
let activeReport = null;
let processPayload = null;
let processMode = "risk";
let interactionsConnected = false;
let processRunToken = 0;
let caseRunToken = 0;
const processDemo = {
  phase: "idle",
  cursor: -1,
  selectedEvidenceId: null,
  selectedCauseIndex: null,
  evidenceRequested: false,
};

const processFiles = {
  riskCard: "./data/risk_card.json",
  baselineCard: "./data/baseline_card.json",
  riskResult: "./data/risk_result.json",
  baselineResult: "./data/baseline_result.json",
  riskSeries: "./data/demo_series.json",
  baselineSeries: "./data/baseline_series.json",
  graph: "./data/subgraph.json",
  metrics: "./data/metrics.json",
  scenarioMetrics: "./data/scenario_metrics.json",
};

function wait(ms) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function setText(selector, value) {
  const element = $(selector);
  if (element) element.textContent = value;
}

function setFeedback(selector, value, kind = "") {
  const element = $(selector);
  if (!element) return;
  element.textContent = value;
  element.className = kind;
}

function writeClipboard(text, onStart, onSuccess, onFailure) {
  onStart?.();
  if (!navigator.clipboard?.writeText) {
    onFailure?.();
    return Promise.resolve(false);
  }
  return navigator.clipboard.writeText(text).then(() => {
    onSuccess?.();
    return true;
  }).catch(() => {
    onFailure?.();
    return false;
  });
}

function evidenceItem(item) {
  return `<li><strong>${escapeHtml(item.label)}</strong><span>${escapeHtml(item.detail)}</span><small>${escapeHtml(strengthLabel[item.strength])} · ${escapeHtml(item.evidence_id)}</small></li>`;
}

function processCard() {
  return processPayload?.[processMode === "risk" ? "riskCard" : "baselineCard"] || null;
}

function processResult() {
  return processPayload?.[processMode === "risk" ? "riskResult" : "baselineResult"] || null;
}

function processSeries() {
  return processPayload?.[processMode === "risk" ? "riskSeries" : "baselineSeries"] || [];
}

function formatProcessNumber(value, digits = 2) {
  return Number.isFinite(Number(value)) ? Number(value).toFixed(digits) : "—";
}

function metricById(result, id) {
  return result?.comparisons?.find((item) => item.metric_id === id) || null;
}

function processPath(values, min = 43, max = 53) {
  if (!values.length) return "";
  const width = 870;
  const height = 260;
  return values.map((value, index) => {
    const x = values.length === 1 ? 0 : index / (values.length - 1) * width;
    const y = height - Math.max(0, Math.min(1, (Number(value) - min) / (max - min))) * height;
    return `${index ? "L" : "M"}${x.toFixed(1)} ${y.toFixed(1)}`;
  }).join(" ");
}

function renderProcessChart(series, result) {
  const allValues = series.map((item) => Number(item.torque_nm)).filter(Number.isFinite);
  const recentCount = result?.analysis_window?.recent_count || 24;
  const recent = allValues.slice(-recentCount);
  const line = $("#process-torque-line");
  const recentLine = $("#process-recent-line");
  if (line) line.setAttribute("d", processPath(allValues));
  if (recentLine) {
    const startIndex = Math.max(0, allValues.length - recent.length);
    const recentPath = processPath(recent);
    const offset = allValues.length > 1 ? startIndex / (allValues.length - 1) * 870 : 0;
    const shifted = recentPath.replace(/([ML])([0-9.\-]+) /g, (_, command, x) => `${command}${(Number(x) + offset).toFixed(1)} `);
    recentLine.setAttribute("d", shifted);
  }
  const baselineMean = Number(result?.metrics?.baseline_mean_nm || 48.126);
  const y = 260 - Math.max(0, Math.min(1, (baselineMean - 43) / 10)) * 260;
  $("#process-baseline-line")?.setAttribute("y1", y.toFixed(1));
  $("#process-baseline-line")?.setAttribute("y2", y.toFixed(1));
  const title = processMode === "risk" ? "风险窗口：扭矩仍在规格内，但过程中心和设备侧信号同向变化" : "稳定窗口：同口径基线未触发趋势或设备侧规则";
  $("#process-chart-title")?.replaceChildren(document.createTextNode(title));
  $("#process-chart-desc")?.replaceChildren(document.createTextNode(`${series.length} 条公开合成拧紧记录，最近 ${recentCount} 条作为当前窗口。`));
}

function renderProcessSignals(result, card) {
  const metrics = result?.metrics || {};
  const comparison = result?.comparisons || [];
  const rules = [...new Set(comparison.flatMap((item) => item.rule_ids || []))];
  const items = [
    { title: `扭矩均值 ${formatProcessNumber(metrics.baseline_mean_nm)} → ${formatProcessNumber(metrics.recent_mean_nm)} N·m`, detail: `中心偏移 ${formatProcessNumber(metrics.mean_shift_sigma)}σ，规格内比例 ${formatProcessNumber(metrics.in_spec_rate * 100, 1)}%`, triggered: rules.includes("MW-24") },
    { title: `角度离散为基线的 ${formatProcessNumber(metrics.angle_std_ratio)} 倍`, detail: `与重试均值 ${formatProcessNumber(metrics.retry_baseline, 3)} → ${formatProcessNumber(metrics.retry_recent, 3)} 次/循环一起核对`, triggered: metrics.angle_std_ratio > 1.2 },
    { title: "辅助信号已纳入可用性检查", detail: `工具距模拟标定到期 ${metrics.calibration_days_remaining ?? "—"} 天，不能据此确认漂移`, triggered: false },
    { title: `触发规则：${rules.length ? rules.join("、") : "无"}`, detail: card?.analysis_provenance?.trigger_reasons?.join("；") || "保持同分层滚动监控", triggered: rules.length > 0 },
  ];
  const list = $("#process-signal-list");
  if (list) list.innerHTML = items.map((item) => `<li class="${item.triggered ? "triggered" : ""}"><strong>${escapeHtml(item.title)}</strong><span>${escapeHtml(item.detail)}</span></li>`).join("");
  setText("#process-boundary-note", card?.uncertainty || card?.reasoning?.uncertainty || "当前结果仅适用于公开合成数据的声明分析分层。");
}

function traceSummary(value) {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string") return value;
  if (Array.isArray(value)) return value.join("、");
  return Object.entries(value).map(([key, item]) => `${key}=${Array.isArray(item) ? item.join("、") : item}`).join("；");
}

function renderProcessTrace(card) {
  const list = $("#process-trace-list");
  if (!list) return;
  const trace = card?.agent_trace || [];
  list.innerHTML = trace.map((call) => `<article class="trace-item"><span class="trace-sequence">${String(call.sequence ?? "—").padStart(2, "0")}</span><div><header><strong>${escapeHtml(call.tool)}</strong><span>${escapeHtml(call.step)} · ${escapeHtml(call.status)}</span></header><p>${escapeHtml(call.result)}</p><small>输入：${escapeHtml(traceSummary(call.input_summary))}</small><small>输出：${escapeHtml(traceSummary(call.output_summary))}</small></div></article>`).join("") || `<p class="process-note">当前稳定窗口没有可展示的工具调用轨迹。</p>`;
}

function renderProcessEvidence(card) {
  const evidence = $("#process-evidence-table");
  if (!evidence) return;
  evidence.innerHTML = (card?.evidence || []).map((item) => `<tr class="evidence-row" data-evidence-id="${escapeHtml(item.evidence_id)}" tabindex="0" aria-selected="${String(processDemo.selectedEvidenceId === item.evidence_id)}"><td><button class="table-link" type="button" data-evidence-id="${escapeHtml(item.evidence_id)}">${escapeHtml(item.evidence_id)}</button><br>${escapeHtml(item.title)}</td><td>${escapeHtml(item.observation)}</td><td>${escapeHtml(item.source)}<br>${escapeHtml(item.locator)}</td><td><span class="evidence-strength ${escapeHtml(item.strength)}">${escapeHtml(processStrengthLabel[item.strength] || item.strength)}</span></td></tr>`).join("") || `<tr><td colspan="4">当前窗口没有证据记录。</td></tr>`;
  setText("#process-evidence-count", `${(card?.evidence || []).length} 条风险卡证据`);
  renderEvidenceDetail(card);
}

function renderEvidenceDetail(card) {
  const panel = $("#process-evidence-detail");
  if (!panel) return;
  const item = card?.evidence?.find((entry) => entry.evidence_id === processDemo.selectedEvidenceId);
  if (!item) {
    panel.hidden = true;
    panel.innerHTML = "";
    return;
  }
  const causes = (card.candidate_causes || []).filter((cause) => (cause.evidence_ids || []).includes(item.evidence_id)).map((cause) => cause.cause);
  const tasks = (card.recommended_actions || []).filter((task) => (task.evidence_ids || []).includes(item.evidence_id)).map((task) => task.action_id);
  panel.hidden = false;
  panel.innerHTML = `<strong>${escapeHtml(item.evidence_id)} · ${escapeHtml(item.title)}</strong><p>${escapeHtml(item.observation)}</p><small>来源：${escapeHtml(item.source)} · 定位：${escapeHtml(item.locator)}</small><small>关联候选：${escapeHtml(causes.join("、") || "无")} · 任务：${escapeHtml(tasks.join("、") || "无")}</small>`;
  document.querySelectorAll(".evidence-row").forEach((row) => row.classList.toggle("selected", row.dataset.evidenceId === item.evidence_id));
}

function renderProcessCauses(card) {
  const causes = $("#process-cause-list");
  if (!causes) return;
  causes.innerHTML = (card?.candidate_causes || []).map((cause, index) => `<article class="cause-item ${processDemo.selectedCauseIndex === index ? "selected" : ""}" data-cause-index="${index}" tabindex="0" role="button" aria-pressed="${String(processDemo.selectedCauseIndex === index)}"><header><strong>${escapeHtml(cause.cause)}</strong><em>${escapeHtml(cause.confidence)}</em></header><p>${escapeHtml(cause.verification)}</p><small>${escapeHtml((cause.evidence_ids || []).join(" · "))}</small></article>`).join("") || `<p class="process-note">稳定窗口没有启动根因归因。</p>`;
  setText("#process-cause-state", card?.candidate_causes?.length ? `${card.candidate_causes.length} 个待验证假设` : "无需归因");
  renderCauseDetail(card);
}

function renderCauseDetail(card) {
  const panel = $("#process-cause-detail");
  if (!panel) return;
  const cause = card?.candidate_causes?.[processDemo.selectedCauseIndex];
  if (!cause) {
    panel.hidden = true;
    panel.innerHTML = "";
    return;
  }
  panel.hidden = false;
  panel.innerHTML = `<strong>验证方法</strong><p>${escapeHtml(cause.verification)}</p><small>证据：${escapeHtml((cause.evidence_ids || []).join(" · "))}</small>`;
}

function renderProcessCard(card, result) {
  if (!card || !result) return;
  const torque = metricById(result, "torque_mean_nm");
  const angle = metricById(result, "angle_dispersion_ratio");
  const retry = metricById(result, "retry_mean");
  const inSpec = metricById(result, "in_spec_rate");
  setText("#process-scenario-title", processMode === "risk" ? "规格内缓慢漂移 · 风险窗口" : "稳定窗口 · 常规监控");
  setText("#process-scenario-meta", `${card.station_id} · ${card.tool_id} · ${card.fastening_point} · ${result.analysis_window.baseline_count} 条基线 / ${result.analysis_window.recent_count} 条最近窗口`);
  setText("#process-risk-level", processLevelLabel[result.risk_level] || result.risk_level);
  setText("#process-risk-score", `${result.risk_score} / 100 · 仅用于处理排序`);
  setText("#process-torque-mean", `${formatProcessNumber(torque?.current)} N·m`);
  setText("#process-torque-delta", `较基线 ${formatProcessNumber(torque?.delta, 2)} N·m`);
  setText("#process-angle-ratio", `${formatProcessNumber(angle?.current)} 倍`);
  setText("#process-retry", `${formatProcessNumber(retry?.current, 3)} 次`);
  setText("#process-retry-delta", `较基线 ${formatProcessNumber(retry?.delta, 3)} 次/循环`);
  setText("#process-in-spec", `${formatProcessNumber(inSpec?.current, 1)}%`);
  setText("#process-process-state", result.risk_level === "low" ? "未触发规则" : "已触发规则，等待研判");
  const stratum = card.analysis_provenance?.analysis_stratum || {};
  setText("#process-stratum", `${stratum.model_code || "—"} / ${stratum.program_id || "—"} / ${stratum.fastening_point || card.fastening_point}`);
  setText("#process-window", `${result.analysis_window.baseline_count} → ${result.analysis_window.recent_count} 条`);
  setText("#process-inference", card.inference || card.reasoning?.conclusion?.text || "当前窗口保持监控。");
  setText("#process-uncertainty", card.uncertainty || card.reasoning?.uncertainty || "—");
  const breakdown = $("#process-score-breakdown");
  if (breakdown) breakdown.innerHTML = Object.entries(result.score_breakdown || {}).map(([key, value]) => {
    const labels = { context: "上下文", equipment_health: "设备状态", process_stability: "过程稳定", quality_impact: "质量影响" };
    const width = Math.min(100, Number(value) / 25 * 100);
    return `<div class="score-row"><span>${escapeHtml(labels[key] || key)}</span><i style="--score-width:${width.toFixed(1)}%"></i><strong>${escapeHtml(value)}</strong></div>`;
  }).join("");
  renderProcessCauses(card);
  renderProcessEvidence(card);
  renderProcessTrace(card);
  renderProcessSignals(result, card);
  renderProcessChart(processSeries(), result);
  renderProcessTasks(card);
  renderProcessWorkflow();
  renderProcessRunSteps();
}

function renderProcessTasks(card) {
  const tasks = card?.recommended_actions || [];
  const table = $("#process-task-table");
  if (table) table.innerHTML = tasks.map((task) => `<tr><td><strong>${escapeHtml(task.title)}</strong><br><code>${escapeHtml(task.action_id)}</code></td><td>${escapeHtml(task.owner_role)}</td><td>${escapeHtml(task.due_minutes)} 分钟</td><td>${escapeHtml(task.acceptance_criteria)}</td><td>${task.approval_required ? "需具名审批" : "无需审批"}</td></tr>`).join("") || `<tr><td colspan="5">当前窗口稳定，无异常处置任务。</td></tr>`;
}

function renderProcessWorkflow() {
  const card = processCard();
  const stable = processMode === "baseline" || !card?.recommended_actions?.length;
  let status = stable ? "稳定监控" : processPhaseLabel[processDemo.phase] || processDemo.phase;
  if (processDemo.phase === "awaiting_engineer_review" && processDemo.evidenceRequested) status = "补证后重新复核";
  setText("#process-workflow-status", status);
  setText("#process-workflow-boundary", stable ? "不生成异常任务；页面不写入外部系统" : "仅生成本地预览，不停线、不改参数、不确认根因");
  const tasks = card?.recommended_actions || [];
  const preview = {
    risk_card_id: card?.card_id || null,
    status: stable ? "monitoring_only" : processDemo.phase,
    human_approval_required: !stable,
    automatic_stop_line_allowed: false,
    tasks: tasks.map(({ action_id, title, owner_role, due_minutes, acceptance_criteria, evidence_ids }) => ({ action_id, title, owner_role, due_minutes, acceptance_criteria, evidence_ids })),
    mode: "browser_local_preview",
  };
  $("#process-task-preview")?.toggleAttribute("disabled", stable);
  $("#process-approve")?.toggleAttribute("disabled", stable || !["awaiting_engineer_review"].includes(processDemo.phase));
  $("#process-request-evidence")?.toggleAttribute("disabled", stable || !["awaiting_engineer_review"].includes(processDemo.phase));
  $("#process-submit-verification")?.toggleAttribute("disabled", stable || processDemo.phase !== "verification_pending");
  $("#process-review-writeback")?.toggleAttribute("disabled", stable || processDemo.phase !== "verified_local_demo");
  const output = $("#process-task-output");
  if (output && !output.hidden) output.textContent = JSON.stringify(preview, null, 2);
}

function renderProcessGraph() {
  const graph = processPayload?.graph;
  if (!graph) return;
  const names = new Map((graph.nodes || []).map((node) => [node.id, node]));
  const chain = $("#process-graph-chain");
  if (chain) chain.innerHTML = (graph.edges || []).slice(0, 8).map((edge) => `<div class="graph-node"><small>${escapeHtml(edge.relation)}</small><strong>${escapeHtml(names.get(edge.source)?.name || edge.source)} → ${escapeHtml(names.get(edge.target)?.name || edge.target)}</strong></div>`).join("");
  setText("#process-graph-count", `${graph.nodes.length} 个节点 · ${graph.edges.length} 条关系`);
  setText("#process-graph-start", "ST-FAS-07 / TOOL-TG-07 / P03");
  setText("#process-graph-size", `${graph.nodes.length} / ${graph.edges.length}`);
}

function renderProcessEvaluation() {
  const metrics = processPayload?.metrics || {};
  setText("#process-eval-samples", `${metrics.samples || "—"}`);
  setText("#process-eval-recall", `${formatProcessNumber((metrics.recall || 0) * 100, 1)}%`);
  setText("#process-eval-fpr", `${formatProcessNumber((metrics.false_positive_rate || 0) * 100, 1)}%`);
  setText("#process-eval-trace", `${formatProcessNumber((metrics.evidence_traceability || 0) * 100, 0)}%`);
}

function renderProcessRunSteps() {
  const list = $("#process-run-steps");
  if (!list) return;
  const stable = processMode === "baseline";
  const completed = processDemo.phase === "knowledge_reviewed_local_demo" ? 6 : processDemo.phase === "verified_local_demo" ? 5 : processDemo.phase === "verification_pending" ? 4 : processDemo.phase === "monitoring_only" ? 3 : processDemo.phase === "awaiting_engineer_review" || processDemo.phase === "needs_more_evidence" ? 3 : processDemo.phase === "running" ? Math.max(-1, processDemo.cursor - 1) : -1;
  const activeIndex = processDemo.phase === "running" ? processDemo.cursor : processDemo.phase === "awaiting_engineer_review" || processDemo.phase === "needs_more_evidence" ? 4 : processDemo.phase === "verification_pending" ? 5 : processDemo.phase === "verified_local_demo" ? 6 : -1;
  [...list.children].forEach((item, index) => {
    const active = index === activeIndex;
    const done = stable ? index <= completed : index <= completed;
    item.classList.toggle("active", active);
    item.classList.toggle("complete", done);
    item.classList.toggle("blocked", !done && !active && ((processDemo.phase === "awaiting_engineer_review" || processDemo.phase === "needs_more_evidence") && index > 4));
    item.setAttribute("aria-current", active ? "step" : "false");
  });
  const status = processDemo.phase === "idle" ? (stable ? "待运行：稳定窗口不会生成异常任务" : "待开始：点击“开始主动研判”") : `${processPhaseLabel[processDemo.phase] || processDemo.phase}${processDemo.phase === "running" ? ` · ${runSteps[processDemo.cursor] || ""}` : ""}`;
  setText("#process-run-status", status);
  const runButton = $("#process-run");
  if (runButton) runButton.textContent = processDemo.phase === "running" ? "运行中…" : stable ? "重新运行稳定监控" : processDemo.phase === "knowledge_reviewed_local_demo" ? "重新运行风险窗口" : "开始主动研判";
  if (runButton) runButton.disabled = processDemo.phase === "running";
}

function renderProcess() {
  const card = processCard();
  const result = processResult();
  if (!card || !result) return;
  renderProcessCard(card, result);
  renderProcessGraph();
  renderProcessEvaluation();
  $("#process-risk-toggle")?.setAttribute("aria-pressed", String(processMode === "risk"));
  $("#process-baseline-toggle")?.setAttribute("aria-pressed", String(processMode === "baseline"));
  const riskButton = $("#process-risk-toggle");
  const baselineButton = $("#process-baseline-toggle");
  if (riskButton) riskButton.textContent = processMode === "risk" ? "当前：风险窗口" : "加载风险窗口";
  if (baselineButton) baselineButton.textContent = processMode === "baseline" ? "当前：稳定窗口" : "加载稳定窗口";
}

async function loadProcessAssets() {
  const entries = await Promise.all(Object.entries(processFiles).map(async ([key, path]) => {
    const response = await fetch(path, { cache: "no-store" });
    if (!response.ok) throw new Error(`过程分析资产读取失败：${path} (${response.status})`);
    return [key, await response.json()];
  }));
  processPayload = Object.fromEntries(entries);
  renderProcess();
}

function renderCase(report) {
  activeReport = report;
  const { case: record, facts, gaps, tasks, disposition, confidence_label: confidence, boundary, terra } = report;
  $("#case-title").textContent = record.title;
  $("#case-meta").textContent = `${record.case_id} · ${record.source_group} · ${record.status_group}`;
  $("#disposition").textContent = disposition === "complete_case_before_reasoning" ? "先补齐工单" : disposition === "verify_relation_before_knowledge_reuse" ? "先核验关联" : "进入跨角色复核";
  $("#disposition-note").textContent = record.relation_tier;
  $("#confidence").textContent = confidence;
  $("#evidence-count").textContent = `${facts.length} 条事实 / ${gaps.length} 项缺口`;
  $("#node-equipment").textContent = `${record.equipment_family} · ${record.component_family}`;
  $("#node-workorder").textContent = `${record.phenomenon_category} · ${record.status_group}`;
  $("#node-failure").textContent = record.has_failure_mode_link ? record.failure_mode_link_status : "当前未关联";
  $("#fact-list").innerHTML = facts.filter((item) => item.strength === "direct").map(evidenceItem).join("") || "<li><span>当前没有可直接确认的结构事实。</span></li>";
  $("#candidate-list").innerHTML = facts.filter((item) => item.strength === "candidate").map(evidenceItem).join("") || "<li><span>当前没有可供检索的历史候选。</span></li>";
  $("#gap-list").innerHTML = gaps.map(evidenceItem).join("") || "<li><span>当前案卷没有额外字段缺口，但仍需人工核验候选关联。</span></li>";
  $("#task-table").innerHTML = tasks.map((task) => `<tr><td>${escapeHtml(task.title)}<br><code>${escapeHtml(task.task_id)}</code></td><td>${escapeHtml(task.owner_role)}</td><td>${escapeHtml(task.due_hint)}</td><td>${escapeHtml(task.acceptance_criteria)}</td><td>${task.evidence_ids.map((id) => `<code>${escapeHtml(id)}</code>`).join("<br>")}</td></tr>`).join("");
  $("#boundary-text").textContent = boundary;
  const compactPayload = getCompactPayload(report);
  $("#model-preview").textContent = JSON.stringify(compactPayload, null, 2);
  setFeedback("#case-feedback", `已读取 ${record.case_id}，页面未写入外部系统。`);
}

function dossierText(report = activeReport) {
  if (!report) return "暂无案卷";
  const { case: record, facts, gaps, tasks } = report;
  return [
    `案卷：${record.case_id}｜${record.title}`,
    `关系层级：${record.relation_tier}；完整度：${record.completeness_grade}`,
    `直接事实：${facts.filter((item) => item.strength === "direct").map((item) => item.label).join("、") || "无"}`,
    `待核验关联：${facts.filter((item) => item.strength === "candidate").map((item) => item.label).join("、") || "无"}`,
    `信息缺口：${gaps.map((item) => item.label).join("、") || "无"}`,
    `任务：${tasks.map((task) => task.title).join("；") || "无"}`,
    "公开演示：只生成本地预览，不创建飞书任务。",
  ].join("\n");
}

function getCompactPayload(report = activeReport) {
  if (!report) return null;
  const { case: record, facts, gaps, tasks, terra } = report;
  return {
    case: { case_id: record.case_id, title: record.title, relation_tier: record.relation_tier, completeness_grade: record.completeness_grade },
    evidence: [...facts, ...gaps].map(({ evidence_id, strength, label }) => ({ evidence_id, strength, label })),
    task_ids: tasks.map(({ task_id }) => task_id),
    public_demo_mode: terra.mode,
  };
}

function generateTaskPreview() {
  const payload = getCompactPayload();
  if (!payload) {
    setFeedback("#task-feedback", "案卷尚未读取，无法生成预览。", "error");
    return null;
  }
  const output = $("#task-preview-output");
  if (output) {
    output.hidden = false;
    output.textContent = JSON.stringify(payload, null, 2);
  }
  setFeedback("#task-feedback", `已生成 ${payload.task_ids.length} 项案卷任务预览。${PUBLIC_DEMO_WRITE_BOUNDARY}，正式写入仍需具名审批与授权租户。`, "success");
  return payload;
}

function selectCase(index) {
  if (!publicPayload?.reports?.length) return null;
  const safeIndex = Math.max(0, Math.min(Number(index) || 0, publicPayload.reports.length - 1));
  const select = $("#case-select");
  if (select) select.value = String(safeIndex);
  renderCase(publicPayload.reports[safeIndex]);
  history.replaceState(null, "", `?case=${safeIndex}#top`);
  return publicPayload.reports[safeIndex];
}

async function copyDossier() {
  const text = dossierText();
  return writeClipboard(text, () => setFeedback("#case-feedback", "正在准备案卷摘要…"), () => setFeedback("#case-feedback", "案卷摘要已复制，可粘贴到评审记录或飞书任务草稿。", "success"), () => setFeedback("#case-feedback", "已生成案卷摘要，但浏览器未授权剪贴板；可直接复制页面中的 JSON。", "error"));
}

function showProcessCardJson() {
  const card = processCard();
  const output = $("#process-card-output");
  if (!card || !output) return;
  output.hidden = !output.hidden;
  output.textContent = JSON.stringify({ ...card, public_demo_boundary: "browser_local_read_only" }, null, 2);
  setFeedback("#process-card-feedback", output.hidden ? "已收起风险卡 JSON。" : "已展开风险卡 JSON，可检查卡片、证据与工具轨迹。", "success");
  $("#process-copy-card")?.setAttribute("aria-expanded", String(!output.hidden));
}

function processTaskPreviewPayload() {
  const card = processCard();
  const tasks = card?.recommended_actions || [];
  return {
    risk_card_id: card?.card_id || null,
    status: processMode === "baseline" ? "monitoring_only" : processDemo.phase,
    human_approval_required: processMode === "risk",
    automatic_stop_line_allowed: false,
    tasks: tasks.map(({ action_id, title, owner_role, due_minutes, acceptance_criteria, evidence_ids }) => ({ action_id, title, owner_role, due_minutes, acceptance_criteria, evidence_ids })),
    mode: "browser_local_preview",
  };
}

function showProcessTaskPreview() {
  const card = processCard();
  const tasks = card?.recommended_actions || [];
  if (!tasks.length) {
    setFeedback("#process-task-feedback", "稳定窗口不生成异常任务，只保留监控记录。", "success");
    return null;
  }
  const preview = processTaskPreviewPayload();
  const output = $("#process-task-output");
  if (output) {
    output.hidden = false;
    output.textContent = JSON.stringify(preview, null, 2);
  }
  setFeedback("#process-task-feedback", `已生成 ${tasks.length} 项本地任务预览。没有发送飞书请求，需具名工程师审批后执行。`, "success");
  return preview;
}

function resetProcess() {
  processRunToken += 1;
  processDemo.phase = "idle";
  processDemo.cursor = -1;
  processDemo.selectedEvidenceId = null;
  processDemo.selectedCauseIndex = null;
  processDemo.evidenceRequested = false;
  const cardOutput = $("#process-card-output");
  const taskOutput = $("#process-task-output");
  if (cardOutput) cardOutput.hidden = true;
  if (taskOutput) taskOutput.hidden = true;
  setFeedback("#process-card-feedback", "");
  setFeedback("#process-task-feedback", "");
  renderProcess();
}

async function runProcess() {
  if (!processPayload) return false;
  resetProcess();
  const token = ++processRunToken;
  processDemo.phase = "running";
  const lastStep = processMode === "baseline" ? 3 : 4;
  for (let index = 0; index <= lastStep; index += 1) {
    if (token !== processRunToken) return false;
    processDemo.cursor = index;
    renderProcessRunSteps();
    await wait(260);
  }
  if (token !== processRunToken) return false;
  if (processMode === "baseline") {
    processDemo.phase = "monitoring_only";
    processDemo.cursor = 3;
    setFeedback("#process-task-feedback", "稳定窗口已完成检测与记录，没有生成异常处置任务。", "success");
  } else {
    processDemo.phase = "awaiting_engineer_review";
    processDemo.cursor = 4;
    setFeedback("#process-task-feedback", "风险卡已进入人工审批。下一步必须由工程师决定通过或退回补证。", "success");
  }
  renderProcess();
  return true;
}

function approveProcess() {
  if (processMode !== "risk" || processDemo.phase !== "awaiting_engineer_review") return false;
  processDemo.evidenceRequested = false;
  processDemo.phase = "verification_pending";
  processDemo.cursor = 5;
  setFeedback("#process-task-feedback", "已通过审批（本地演示）。请按验收条件完成点检、抽检或批次核对，再提交现场验证。", "success");
  renderProcess();
  return true;
}

function requestEvidence() {
  if (processMode !== "risk" || processDemo.phase !== "awaiting_engineer_review") return false;
  processDemo.evidenceRequested = true;
  setFeedback("#process-task-feedback", "已退回补证。当前只允许补充现场记录，系统不会把候选原因改写成根因。", "success");
  renderProcess();
  return true;
}

function submitVerification() {
  if (processMode !== "risk" || processDemo.phase !== "verification_pending") return false;
  processDemo.phase = "verified_local_demo";
  processDemo.cursor = 5;
  setFeedback("#process-task-feedback", "已提交现场验证（本地演示）。结果仍需审核，尚未确认候选原因。", "success");
  renderProcess();
  return true;
}

function reviewKnowledgeWriteback() {
  if (processMode !== "risk" || processDemo.phase !== "verified_local_demo") return false;
  processDemo.phase = "knowledge_reviewed_local_demo";
  processDemo.cursor = 6;
  setFeedback("#process-task-feedback", "已完成知识回写审核（本地演示）。页面没有修改知识库或外部系统。", "success");
  renderProcess();
  return true;
}

function selectProcessEvidence(id) {
  processDemo.selectedEvidenceId = id;
  renderEvidenceDetail(processCard());
  setFeedback("#process-task-feedback", `已定位证据 ${id}：可继续查看其关联候选原因与任务。`, "success");
}

function selectProcessCause(index) {
  processDemo.selectedCauseIndex = Number(index);
  renderProcessCauses(processCard());
  const cause = processCard()?.candidate_causes?.[processDemo.selectedCauseIndex];
  if (cause) setFeedback("#process-task-feedback", `已展开候选原因“${cause.cause}”的验证方法。`, "success");
}

function rerunCase() {
  const token = ++caseRunToken;
  const stages = ["读取对象与工单", "分级事实与候选", "检查缺口与任务"];
  const feedback = $("#case-feedback");
  if (!feedback) return;
  let index = 0;
  const tick = () => {
    if (token !== caseRunToken) return;
    if (index >= stages.length) {
      feedback.textContent = "关系核验完成：事实、候选、缺口和任务已重新分栏。页面未写入外部系统。";
      feedback.className = "case-feedback success";
      return;
    }
    feedback.textContent = `正在${stages[index]}…`;
    feedback.className = "case-feedback";
    index += 1;
    window.setTimeout(tick, 180);
  };
  tick();
}

function connectInteractions() {
  if (interactionsConnected) return;
  interactionsConnected = true;
  $("#model-toggle")?.addEventListener("click", () => {
    const preview = $("#model-preview");
    if (!preview) return;
    const expanded = preview.hidden;
    preview.hidden = !expanded;
    $("#model-toggle").setAttribute("aria-expanded", String(expanded));
    $("#model-toggle").textContent = expanded ? "收起模型输入边界" : "查看模型输入边界";
  });
  $("#task-preview")?.addEventListener("click", generateTaskPreview);
  $("#copy-dossier")?.addEventListener("click", copyDossier);
  $("#run-case")?.addEventListener("click", rerunCase);
  $("#retry-data")?.addEventListener("click", init);
  $("#process-run")?.addEventListener("click", runProcess);
  $("#process-reset")?.addEventListener("click", resetProcess);
  $("#process-risk-toggle")?.addEventListener("click", () => { if (processMode !== "risk") { processMode = "risk"; resetProcess(); } });
  $("#process-baseline-toggle")?.addEventListener("click", () => { if (processMode !== "baseline") { processMode = "baseline"; resetProcess(); } });
  $("#process-copy-card")?.addEventListener("click", showProcessCardJson);
  $("#process-task-preview")?.addEventListener("click", showProcessTaskPreview);
  $("#process-approve")?.addEventListener("click", approveProcess);
  $("#process-request-evidence")?.addEventListener("click", requestEvidence);
  $("#process-submit-verification")?.addEventListener("click", submitVerification);
  $("#process-review-writeback")?.addEventListener("click", reviewKnowledgeWriteback);
  $("#process-evidence-table")?.addEventListener("click", (event) => {
    const target = event.target.closest("[data-evidence-id]");
    if (target) selectProcessEvidence(target.dataset.evidenceId);
  });
  $("#process-evidence-table")?.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    const target = event.target.closest("[data-evidence-id]");
    if (target) { event.preventDefault(); selectProcessEvidence(target.dataset.evidenceId); }
  });
  $("#process-cause-list")?.addEventListener("click", (event) => {
    const target = event.target.closest("[data-cause-index]");
    if (target) selectProcessCause(target.dataset.causeIndex);
  });
  $("#process-cause-list")?.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    const target = event.target.closest("[data-cause-index]");
    if (target) { event.preventDefault(); selectProcessCause(target.dataset.causeIndex); }
  });
  window.round2Demo = {
    selectCase,
    generateTaskPreview,
    getModelPayload: getCompactPayload,
    getDossierText: dossierText,
    getProcessCard: processCard,
    getProcessTaskPreview: processTaskPreviewPayload,
    runProcess,
    approveProcess,
    requestEvidence,
    submitVerification,
    reviewKnowledgeWriteback,
    resetProcess,
    reload: init,
  };
}

async function init() {
  try {
    const response = await fetch("./data/round2_reports.json", { cache: "no-store" });
    if (!response.ok) throw new Error(`无法读取公开演示资产：${response.status}`);
    const payload = await response.json();
    if (payload.schema_version !== "2.0" || payload.data_boundary !== "schema_and_relationship_reference_only" || !Array.isArray(payload.reports)) throw new Error("公开演示资产不符合第二轮数据边界合同");
    publicPayload = payload;
    const select = $("#case-select");
    if (select) {
      select.replaceChildren();
      payload.reports.forEach((report, index) => {
        const option = document.createElement("option");
        option.value = String(index);
        option.textContent = `${report.case.case_id} · ${report.case.title}`;
        select.append(option);
      });
    }
    const requested = Number(new URLSearchParams(location.search).get("case"));
    const initialIndex = Number.isInteger(requested) && requested >= 0 && requested < payload.reports.length ? requested : 0;
    if (select && !select.dataset.bound) {
      select.addEventListener("change", () => selectCase(select.value));
      select.dataset.bound = "true";
    }
    selectCase(initialIndex);
    connectInteractions();
    await loadProcessAssets();
  } catch (error) {
    $("#case-title").textContent = "公开演示资产加载失败";
    $("#case-meta").textContent = error instanceof Error ? error.message : "未知错误";
    setFeedback("#case-feedback", "页面未写入任何外部系统，可点击“重试读取”。", "case-feedback error");
    $("#retry-data").hidden = false;
    const processFeedback = $("#process-boundary-note");
    if (processFeedback) processFeedback.textContent = error instanceof Error ? error.message : "过程分析资产读取失败";
  }
}

init();
