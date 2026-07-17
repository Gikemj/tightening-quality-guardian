import { analyzeSeries, pathFor } from "./risk-engine.js";

const state = {
  series: [],
  card: null,
  metrics: null,
  graph: null,
  mode: "risk",
  tasksCreated: false,
};

const $ = (selector) => document.querySelector(selector);

const text = (selector, value) => {
  const element = $(selector);
  if (element) element.textContent = value;
};

function formatPercent(value, digits = 0) {
  return `${(value * 100).toFixed(digits)}%`;
}

function levelLabel(level) {
  return { high: "高风险", medium: "中风险", low: "低风险" }[level] || level;
}

function confidenceLabel(value) {
  return { high: "高", "medium-high": "中高", medium: "中" }[value] || value;
}

function activeSeries() {
  return state.mode === "risk" ? state.series : state.series.slice(0, 84);
}

function updateHeader(result) {
  const riskChip = $("#risk-chip");
  riskChip.className = `risk-chip risk-${result.level}`;
  riskChip.textContent = levelLabel(result.level);
  text("#risk-score", String(result.score));
  text("#metric-state", state.mode === "risk" ? "需工程师复核" : "基线稳定");
  text("#metric-shift", `${result.meanShiftSigma.toFixed(2)}σ`);
  text("#metric-in-spec", formatPercent(result.inSpecRate));
  text("#metric-retry", `${result.retryRecent.toFixed(3)} 次/循环`);
  text("#signal-summary", state.mode === "risk"
    ? `最近 24 次扭矩均值偏移 ${result.meanShiftSigma.toFixed(2)}σ，角度离散为基线的 ${result.angleRatio.toFixed(2)} 倍，重试均值升至 ${result.retryRecent.toFixed(3)} 次/循环。测量值仍有 ${formatPercent(result.inSpecRate)} 位于规格内。`
    : `当前窗口均值偏移 ${result.meanShiftSigma.toFixed(2)}σ，未形成设备与质量风险的组合证据。`);

  const mainTitle = $("#main-title");
  mainTitle.textContent = state.mode === "risk" ? "规格内，也可能正在失稳" : "基线窗口：过程处于稳定状态";
  text("#main-lead", state.mode === "risk"
    ? "P03 紧固点没有出现批量越限，但扭矩中心、角度离散和重试行为同时变化。数字员工已关联 PFMEA、控制计划和相似案例，等待工程师确认。"
    : "使用前 84 条正常记录重放基线。当前没有形成需要派单的组合信号。点击“重放风险识别”查看主动发现过程。");
}

function renderChart(series) {
  const chartWidth = 880;
  const chartHeight = 260;
  const minimum = 43;
  const maximum = 53;
  const values = series.map((row) => Number(row.torque_nm));
  const path = pathFor(values, chartWidth, chartHeight, minimum, maximum);
  $("#torque-line").setAttribute("d", path);

  const baselineY = chartHeight - ((48 - minimum) / (maximum - minimum)) * chartHeight;
  $("#baseline-line").setAttribute("y1", baselineY);
  $("#baseline-line").setAttribute("y2", baselineY);

  const riskStart = series.findIndex((row) => row.scenario_label === "hidden_risk");
  const riskZone = $("#risk-zone");
  if (state.mode === "risk" && riskStart >= 0) {
    const x = (riskStart / Math.max(series.length - 1, 1)) * chartWidth;
    riskZone.setAttribute("x", x);
    riskZone.setAttribute("width", chartWidth - x);
    riskZone.hidden = false;
  } else {
    riskZone.hidden = true;
  }

  const recent = series.slice(-24);
  const latest = recent[recent.length - 1];
  text("#chart-window", `${recent[0].timestamp.slice(11, 16)}–${latest.timestamp.slice(11, 16)}`);
  text("#chart-latest", `${Number(latest.torque_nm).toFixed(2)} N·m`);
}

function renderBreakdown(result) {
  const labels = {
    process: "过程稳定性",
    equipment: "设备健康",
    quality: "质量影响",
    context: "知识与上下文",
  };
  const maximums = { process: 35, equipment: 25, quality: 25, context: 15 };
  const container = $("#score-breakdown");
  container.replaceChildren();
  Object.entries(result.breakdown).forEach(([key, value]) => {
    const row = document.createElement("div");
    row.className = "score-row";
    row.innerHTML = `
      <div class="score-label"><span>${labels[key]}</span><strong>${value}/${maximums[key]}</strong></div>
      <div class="score-track"><span style="width:${(value / maximums[key]) * 100}%"></span></div>`;
    container.appendChild(row);
  });
}

function renderEvidence() {
  const container = $("#evidence-list");
  container.replaceChildren();
  state.card.evidence.forEach((item, index) => {
    const detail = document.createElement("details");
    detail.className = "evidence-item";
    if (index === 0) detail.open = true;
    detail.innerHTML = `
      <summary>
        <span class="evidence-type">${item.category}</span>
        <span class="evidence-heading"><strong>${item.title}</strong><small>${item.observation}</small></span>
        <span class="source-tag">${item.evidence_id}</span>
      </summary>
      <div class="evidence-detail">
        <dl>
          <div><dt>来源</dt><dd>${item.source}</dd></div>
          <div><dt>定位</dt><dd>${item.locator}</dd></div>
          <div><dt>证据强度</dt><dd>${item.strength}</dd></div>
        </dl>
        <pre>${JSON.stringify(item.data, null, 2)}</pre>
      </div>`;
    container.appendChild(detail);
  });
}

function renderCauses() {
  const body = $("#cause-table-body");
  body.replaceChildren();
  state.card.candidate_causes.forEach((item, index) => {
    const row = document.createElement("tr");
    row.innerHTML = `
      <td data-label="候选原因"><span class="rank">${index + 1}</span>${item.cause}</td>
      <td data-label="置信度"><span class="confidence confidence-${item.confidence.replace("-", "")}">${confidenceLabel(item.confidence)}</span></td>
      <td data-label="依据">${item.basis.join("；")}</td>
      <td data-label="现场验证">${item.verification}</td>`;
    body.appendChild(row);
  });
}

function renderActions() {
  const body = $("#task-table-body");
  body.replaceChildren();
  state.card.recommended_actions.forEach((item) => {
    const row = document.createElement("tr");
    const status = state.tasksCreated ? "已生成" : "待确认";
    row.innerHTML = `
      <td class="mono" data-label="编号">${item.action_id}</td>
      <td data-label="任务"><strong>${item.title}</strong><small>${item.acceptance_criteria}</small></td>
      <td data-label="责任角色">${item.owner_role}</td>
      <td data-label="时限">${item.due_minutes} 分钟</td>
      <td data-label="状态"><span class="task-status ${state.tasksCreated ? "created" : "pending"}">${status}</span></td>`;
    body.appendChild(row);
  });
  text("#workflow-status", state.tasksCreated ? "任务预览已生成，未发送到外部系统" : "等待工程师确认");
  text("#create-task", state.tasksCreated ? "已生成任务预览" : "人工确认并生成任务预览");
  $("#create-task").disabled = state.tasksCreated;
}

function renderAgentTrace() {
  const container = $("#agent-trace");
  container.replaceChildren();
  state.card.agent_trace.forEach((item, index) => {
    const node = document.createElement("li");
    node.innerHTML = `<span class="trace-index">${index + 1}</span><div><strong>${item.step} · ${item.tool}</strong><p>${item.result}</p></div>`;
    container.appendChild(node);
  });
}

function renderGraph() {
  if (!state.graph) return;
  const nodes = $("#graph-nodes");
  nodes.replaceChildren();
  const typeLabels = {
    Equipment: "设备",
    FasteningPoint: "工艺对象",
    FailureMode: "失效模式",
    Cause: "候选原因",
    Action: "验证动作",
    QualityCharacteristic: "质量特性",
    Role: "责任角色",
    Station: "工位",
  };
  state.graph.nodes.forEach((node) => {
    const item = document.createElement("li");
    item.innerHTML = `<span>${typeLabels[node.type] || node.type}</span><strong>${node.name}</strong><small>${node.id}</small>`;
    nodes.appendChild(item);
  });
  text("#graph-count", `${state.graph.nodes.length} 个节点 · ${state.graph.edges.length} 条关系`);
}

function renderMetrics() {
  if (!state.metrics) return;
  text("#eval-recall", formatPercent(state.metrics.recall, 1));
  text("#eval-fpr", formatPercent(state.metrics.false_positive_rate, 1));
  text("#eval-trace", formatPercent(state.metrics.evidence_traceability));
  text("#eval-samples", `${state.metrics.samples} 个滚动窗口`);
}

function render() {
  const series = activeSeries();
  const baselineCount = state.mode === "risk" ? 60 : 60;
  const result = analyzeSeries(series, baselineCount, 24);
  updateHeader(result);
  renderChart(series);
  renderBreakdown(result);
  $("#risk-analysis").classList.toggle("baseline-mode", state.mode !== "risk");
}

function bindInteractions() {
  $("#inject-risk").addEventListener("click", () => {
    state.mode = "risk";
    state.tasksCreated = false;
    render();
    renderActions();
    $("#risk-analysis").scrollIntoView({ behavior: "smooth", block: "start" });
  });

  $("#reset-baseline").addEventListener("click", () => {
    state.mode = "baseline";
    state.tasksCreated = false;
    render();
    renderActions();
  });

  $("#create-task").addEventListener("click", () => {
    state.tasksCreated = true;
    renderActions();
    $("#workflow-status").focus();
  });

  $("#copy-card").addEventListener("click", async () => {
    await navigator.clipboard.writeText(JSON.stringify(state.card, null, 2));
    text("#copy-card", "风险卡 JSON 已复制");
    setTimeout(() => text("#copy-card", "复制风险卡 JSON"), 1800);
  });
}

async function load() {
  try {
    const [seriesResponse, cardResponse, metricsResponse, graphResponse] = await Promise.all([
      fetch("./data/demo_series.json"),
      fetch("./data/risk_card.json"),
      fetch("./data/metrics.json"),
      fetch("./data/subgraph.json"),
    ]);
    state.series = await seriesResponse.json();
    state.card = await cardResponse.json();
    state.metrics = metricsResponse.ok ? await metricsResponse.json() : null;
    state.graph = graphResponse.ok ? await graphResponse.json() : null;
    renderEvidence();
    renderCauses();
    renderActions();
    renderAgentTrace();
    renderGraph();
    renderMetrics();
    bindInteractions();
    render();
    document.documentElement.classList.add("ready");
  } catch (error) {
    text("#load-state", `演示数据加载失败：${error.message}`);
    $("#load-state").classList.add("error");
  }
}

load();
