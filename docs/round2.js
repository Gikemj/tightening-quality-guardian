const $ = (selector) => document.querySelector(selector);

const escapeHtml = (value) => String(value)
  .replaceAll("&", "&amp;")
  .replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;")
  .replaceAll("'", "&#039;");

const strengthLabel = { direct: "直接事实", candidate: "关联候选", gap: "信息缺口" };
let publicPayload = null;
let activeReport = null;
let processPayload = null;
let processMode = "risk";

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

function evidenceItem(item) {
  return `<li><strong>${escapeHtml(item.label)}</strong><span>${escapeHtml(item.detail)}</span><small>${escapeHtml(strengthLabel[item.strength])} · ${escapeHtml(item.evidence_id)}</small></li>`;
}

const processLevelLabel = { high: "高风险", medium: "中风险", low: "低风险" };
const processStrengthLabel = { direct: "直接数据", document: "受控文件", analogy: "历史类比" };

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

function setProcessText(selector, value) {
  const element = $(selector);
  if (element) element.textContent = value;
}

function renderProcessChart(series, result) {
  const allValues = series.map((item) => Number(item.torque_nm)).filter(Number.isFinite);
  const recentCount = result?.analysis_window?.recent_count || 24;
  const recent = allValues.slice(-recentCount);
  setProcessText("#process-torque-line", "");
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
    { title: `辅助信号：工具电流与节拍已纳入可用性检查`, detail: `工具距模拟标定到期 ${metrics.calibration_days_remaining} 天，不能据此确认漂移`, triggered: false },
    { title: `触发规则：${rules.length ? rules.join("、") : "无"}`, detail: card?.analysis_provenance?.trigger_reasons?.join("；") || "保持同分层滚动监控", triggered: rules.length > 0 },
  ];
  const list = $("#process-signal-list");
  if (list) list.innerHTML = items.map((item) => `<li class="${item.triggered ? "triggered" : ""}"><strong>${escapeHtml(item.title)}</strong><span>${escapeHtml(item.detail)}</span></li>`).join("");
  setProcessText("#process-boundary-note", card?.uncertainty || card?.reasoning?.uncertainty || "当前结果仅适用于公开合成数据的声明分析分层。");
}

function renderProcessCard(card, result) {
  if (!card || !result) return;
  const metrics = result.metrics || {};
  const torque = metricById(result, "torque_mean_nm");
  const angle = metricById(result, "angle_dispersion_ratio");
  const retry = metricById(result, "retry_mean");
  const inSpec = metricById(result, "in_spec_rate");
  setProcessText("#process-scenario-title", processMode === "risk" ? "规格内缓慢漂移 · 风险窗口" : "稳定窗口 · 常规监控");
  setProcessText("#process-scenario-meta", `${card.station_id} · ${card.tool_id} · ${card.fastening_point} · ${result.analysis_window.baseline_count} 条基线 / ${result.analysis_window.recent_count} 条最近窗口`);
  setProcessText("#process-risk-level", processLevelLabel[result.risk_level] || result.risk_level);
  setProcessText("#process-risk-score", `${result.risk_score} / 100 · 仅用于处理排序`);
  setProcessText("#process-torque-mean", `${formatProcessNumber(torque?.current)} N·m`);
  setProcessText("#process-torque-delta", `较基线 ${formatProcessNumber(torque?.delta, 2)} N·m`);
  setProcessText("#process-angle-ratio", `${formatProcessNumber(angle?.current)} 倍`);
  setProcessText("#process-retry", `${formatProcessNumber(retry?.current, 3)} 次`);
  setProcessText("#process-retry-delta", `较基线 ${formatProcessNumber(retry?.delta, 3)} 次/循环`);
  setProcessText("#process-in-spec", `${formatProcessNumber(inSpec?.current, 1)}%`);
  setProcessText("#process-process-state", result.risk_level === "low" ? "未触发规则" : "已触发规则，等待研判");
  const stratum = card.analysis_provenance?.analysis_stratum || {};
  setProcessText("#process-stratum", `${stratum.model_code || "—"} / ${stratum.program_id || "—"} / ${stratum.fastening_point || card.fastening_point}`);
  setProcessText("#process-window", `${result.analysis_window.baseline_count} → ${result.analysis_window.recent_count} 条`);
  setProcessText("#process-inference", card.inference || card.reasoning?.conclusion?.text || "当前窗口保持监控。");
  setProcessText("#process-uncertainty", card.uncertainty || card.reasoning?.uncertainty || "—");
  setProcessText("#process-cause-state", card.candidate_causes?.length ? `${card.candidate_causes.length} 个待验证假设` : "无需归因");
  const breakdown = $("#process-score-breakdown");
  if (breakdown) breakdown.innerHTML = Object.entries(result.score_breakdown || {}).map(([key, value]) => {
    const labels = { context: "上下文", equipment_health: "设备状态", process_stability: "过程稳定", quality_impact: "质量影响" };
    const width = Math.min(100, Number(value) / 25 * 100);
    return `<div class="score-row"><span>${escapeHtml(labels[key] || key)}</span><i style="--score-width:${width.toFixed(1)}%"></i><strong>${escapeHtml(value)}</strong></div>`;
  }).join("");
  const causes = $("#process-cause-list");
  if (causes) causes.innerHTML = (card.candidate_causes || []).map((cause) => `<article class="cause-item"><header><strong>${escapeHtml(cause.cause)}</strong><em>${escapeHtml(cause.confidence)}</em></header><p>${escapeHtml(cause.verification)}</p><small>${escapeHtml((cause.evidence_ids || []).join(" · "))}</small></article>`).join("") || `<p class="process-note">稳定窗口没有启动根因归因。</p>`;
  const evidence = $("#process-evidence-table");
  if (evidence) evidence.innerHTML = (card.evidence || []).map((item) => `<tr><td><strong>${escapeHtml(item.evidence_id)}</strong><br>${escapeHtml(item.title)}</td><td>${escapeHtml(item.observation)}</td><td>${escapeHtml(item.source)}<br>${escapeHtml(item.locator)}</td><td><span class="evidence-strength ${escapeHtml(item.strength)}">${escapeHtml(processStrengthLabel[item.strength] || item.strength)}</span></td></tr>`).join("");
  setProcessText("#process-evidence-count", `${(card.evidence || []).length} 条风险卡证据`);
  renderProcessSignals(result, card);
  renderProcessChart(processSeries(), result);
  renderProcessTasks(card);
}

function renderProcessTasks(card) {
  const tasks = card?.recommended_actions || [];
  const table = $("#process-task-table");
  if (table) table.innerHTML = tasks.map((task) => `<tr><td><strong>${escapeHtml(task.title)}</strong><br><code>${escapeHtml(task.action_id)}</code></td><td>${escapeHtml(task.owner_role)}</td><td>${escapeHtml(task.due_minutes)} 分钟</td><td>${escapeHtml(task.acceptance_criteria)}</td><td>${task.approval_required ? "需具名审批" : "无需审批"}</td></tr>`).join("") || `<tr><td colspan="5">当前窗口稳定，无异常处置任务。</td></tr>`;
  setProcessText("#process-workflow-status", card?.workflow?.status === "awaiting_engineer_review" ? "等待工程师复核" : "稳定监控");
  setProcessText("#process-workflow-boundary", card?.workflow?.automatic_stop_line_allowed ? "允许自动停线" : "不允许自动停线、改参数或确认根因");
}

function renderProcessGraph() {
  const graph = processPayload?.graph;
  if (!graph) return;
  const names = new Map((graph.nodes || []).map((node) => [node.id, node]));
  const chain = $("#process-graph-chain");
  if (chain) chain.innerHTML = (graph.edges || []).slice(0, 8).map((edge) => `<div class="graph-node"><small>${escapeHtml(edge.relation)}</small><strong>${escapeHtml(names.get(edge.source)?.name || edge.source)} → ${escapeHtml(names.get(edge.target)?.name || edge.target)}</strong></div>`).join("");
  setProcessText("#process-graph-count", `${graph.nodes.length} 个节点 · ${graph.edges.length} 条关系`);
  setProcessText("#process-graph-start", "ST-FAS-07 / TOOL-TG-07 / P03");
  setProcessText("#process-graph-size", `${graph.nodes.length} / ${graph.edges.length}`);
}

function renderProcessEvaluation() {
  const metrics = processPayload?.metrics || {};
  setProcessText("#process-eval-samples", `${metrics.samples || "—"}`);
  setProcessText("#process-eval-recall", `${formatProcessNumber((metrics.recall || 0) * 100, 1)}%`);
  setProcessText("#process-eval-fpr", `${formatProcessNumber((metrics.false_positive_rate || 0) * 100, 1)}%`);
  setProcessText("#process-eval-trace", `${formatProcessNumber((metrics.evidence_traceability || 0) * 100, 0)}%`);
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

  const compactPayload = {
    case: { case_id: record.case_id, equipment_family: record.equipment_family, relation_tier: record.relation_tier, completeness_grade: record.completeness_grade },
    evidence: [...facts, ...gaps].map(({ evidence_id, strength, label }) => ({ evidence_id, strength, label })),
    task_ids: tasks.map(({ task_id }) => task_id),
    public_demo_mode: terra.mode,
  };
  $("#model-preview").textContent = JSON.stringify(compactPayload, null, 2);
  $("#case-feedback").textContent = `已读取 ${record.case_id}，页面未写入外部系统。`;
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
  const feedback = $("#task-feedback");
  const payload = getCompactPayload();
  if (!payload) {
    feedback.textContent = "案卷尚未读取，无法生成预览。";
    feedback.className = "error";
    return null;
  }
  feedback.textContent = `已生成 ${payload.task_ids.length} 项本地任务预览。公开演示没有发起飞书写入，正式写入仍需具名审批与授权租户。`;
  feedback.className = "success";
  return payload;
}

function selectCase(index) {
  if (!publicPayload?.reports?.length) return null;
  const safeIndex = Math.max(0, Math.min(Number(index) || 0, publicPayload.reports.length - 1));
  $("#case-select").value = String(safeIndex);
  renderCase(publicPayload.reports[safeIndex]);
  history.replaceState(null, "", `?case=${safeIndex}#top`);
  return publicPayload.reports[safeIndex];
}

async function copyDossier() {
  const feedback = $("#case-feedback");
  const text = dossierText();
  try {
    await navigator.clipboard.writeText(text);
    feedback.textContent = "案卷摘要已复制，可粘贴到评审记录或飞书任务草稿。";
  } catch {
    feedback.textContent = "浏览器未授权剪贴板，请使用模型输入边界中的 JSON 预览。";
  }
}

async function copyProcessCard() {
  const card = processCard();
  const feedback = $("#process-task-feedback");
  if (!card) return;
  try {
    await navigator.clipboard.writeText(JSON.stringify(card, null, 2));
    if (feedback) feedback.textContent = "当前风险卡 JSON 已复制。";
  } catch {
    if (feedback) feedback.textContent = "浏览器未授权剪贴板，请直接查看仓库中的 risk_card.json。";
  }
}

async function copyProcessTaskPreview() {
  const card = processCard();
  const tasks = card?.recommended_actions || [];
  const feedback = $("#process-task-feedback");
  if (!feedback) return;
  if (!tasks.length) {
    feedback.textContent = "当前稳定窗口不生成异常任务。";
    return;
  }
  const preview = {
    risk_card_id: card.card_id,
    status: card.workflow?.status || "awaiting_engineer_review",
    human_approval_required: true,
    automatic_stop_line_allowed: false,
    tasks: tasks.map(({ action_id, title, owner_role, due_minutes, acceptance_criteria, evidence_ids }) => ({ action_id, title, owner_role, due_minutes, acceptance_criteria, evidence_ids })),
    mode: "browser_local_preview",
  };
  try {
    await navigator.clipboard.writeText(JSON.stringify(preview, null, 2));
    feedback.textContent = `已复制 ${tasks.length} 项任务预览。没有发送飞书请求，需具名工程师审批后执行。`;
  } catch {
    feedback.textContent = `已生成 ${tasks.length} 项任务预览。剪贴板不可用时，可在仓库 feishu_records_preview.json 查看字段。`;
  }
}

function connectInteractions() {
  $("#model-toggle").addEventListener("click", () => {
    const preview = $("#model-preview");
    const expanded = preview.hidden;
    preview.hidden = !expanded;
    $("#model-toggle").setAttribute("aria-expanded", String(expanded));
    $("#model-toggle").textContent = expanded ? "收起模型输入边界" : "查看模型输入边界";
  });
  $("#task-preview").addEventListener("click", generateTaskPreview);
  $("#copy-dossier").addEventListener("click", copyDossier);
  $("#run-case").addEventListener("click", () => {
    const current = $("#case-select").value;
    selectCase(current);
    $("#case-feedback").textContent = "已重新运行关系核验：事实、候选和缺口已重新分栏。";
  });
  $("#retry-data").addEventListener("click", init);
  $("#process-risk-toggle")?.addEventListener("click", () => { processMode = "risk"; renderProcess(); });
  $("#process-baseline-toggle")?.addEventListener("click", () => { processMode = "baseline"; renderProcess(); });
  $("#process-copy-card")?.addEventListener("click", copyProcessCard);
  $("#process-task-preview")?.addEventListener("click", copyProcessTaskPreview);
  window.round2Demo = {
    selectCase,
    generateTaskPreview,
    getModelPayload: getCompactPayload,
    getDossierText: dossierText,
    getProcessCard: processCard,
    getProcessTaskPreview: () => ({ card_id: processCard()?.card_id || null, tasks: processCard()?.recommended_actions || [] }),
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
    payload.reports.forEach((report, index) => {
      const option = document.createElement("option");
      option.value = String(index);
      option.textContent = `${report.case.case_id} · ${report.case.title}`;
      select.append(option);
    });
    const requested = Number(new URLSearchParams(location.search).get("case"));
    const initialIndex = Number.isInteger(requested) && requested >= 0 && requested < payload.reports.length ? requested : 0;
    select.addEventListener("change", () => selectCase(select.value));
    selectCase(initialIndex);
    connectInteractions();
    await loadProcessAssets();
  } catch (error) {
    $("#case-title").textContent = "公开演示资产加载失败";
    $("#case-meta").textContent = error instanceof Error ? error.message : "未知错误";
    $("#case-feedback").textContent = "页面未写入任何外部系统，可点击“重试读取”。";
    $("#case-feedback").className = "case-feedback error";
    $("#retry-data").hidden = false;
    const processFeedback = $("#process-boundary-note");
    if (processFeedback) processFeedback.textContent = error instanceof Error ? error.message : "过程分析资产读取失败";
  }
}

init();
