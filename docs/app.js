import { authoritativeAnalysis, escapeHtml, pathFor, traceMode } from "./risk-engine.js";
import {
  ADMIN_BOUNDARY_NOTICE,
  buildAdminSnapshot,
  reproductionCommands,
  serializeAdminSnapshot,
} from "./admin-console.js";

const state = {
  series: { risk: [], baseline: [] },
  cards: { risk: null, baseline: null },
  results: { risk: null, baseline: null },
  metrics: null,
  scenarioMetrics: null,
  graph: null,
  graphError: null,
  relationReports: [],
  relationIndex: 0,
  mode: "risk",
  previewPrepared: { risk: false, baseline: false },
  adminDemoStep: 0,
  selectedMetricId: null,
  adminReturnFocus: null,
};

let sidebarNavigationLock = null;
let sidebarNavigationTimer = 0;
let liveAnalysisTimer = null;
const isPublicPreview = typeof window !== "undefined"
  && (window.location.hostname.endsWith("github.io") || window.location.protocol === "file:");
let liveBackendMode = isPublicPreview ? "static" : "unknown";
let relationRunToken = 0;
const localLiveState = {
  running: false,
  sequence: 0,
  payload: null,
};

const $ = (selector) => document.querySelector(selector);

const LIVE_ANALYSIS_API = "http://127.0.0.1:8010/api/live-analysis";
const LIVE_SIMULATOR_API = "http://127.0.0.1:8010/api/simulator";

function cloneJson(value) {
  return value == null ? value : JSON.parse(JSON.stringify(value));
}

function activeRelationReport() {
  return state.relationReports[state.relationIndex] || null;
}

function relationReportText(report = activeRelationReport()) {
  if (!report) return "暂无公开案卷";
  const record = report.case || {};
  const direct = (report.facts || []).filter((item) => item.strength === "direct").map((item) => item.label).join("、") || "无";
  const candidate = (report.facts || []).filter((item) => item.strength === "candidate").map((item) => item.label).join("、") || "无";
  const gaps = (report.gaps || []).map((item) => item.label).join("、") || "无";
  const tasks = (report.tasks || []).map((item) => item.title).join("；") || "无";
  return [
    `案卷：${record.case_id || "—"}｜${record.title || "—"}`,
    `关系层级：${record.relation_tier || "—"}；完整度：${record.completeness_grade || "—"}`,
    `直接事实：${direct}`,
    `待核验关联：${candidate}`,
    `信息缺口：${gaps}`,
    `任务草案：${tasks}`,
    "公开演示：只生成浏览器预览，不创建外部任务。",
  ].join("\n");
}

function renderRelationCase() {
  const report = activeRelationReport();
  if (!report) return;
  const record = report.case || {};
  text("#relation-case-title", `${record.case_id || "—"} · ${record.title || "公开合成案卷"}`);
  text("#relation-case-tier", record.relation_tier || "关系待核验");
  text("#relation-case-description", `${report.confidence_label || "当前关系状态待核验"}。${record.structure_link_status || "页面只展示受控字段关系。"}`);
  text("#relation-case-equipment", record.equipment_family || "—");
  text("#relation-case-component", record.component_family || "—");
  text("#relation-case-state", `${record.status_group || "—"} · ${record.outcome_group || "—"}`);
  text("#relation-case-completeness", record.completeness_grade || "—");
  text("#relation-case-disposition", report.disposition || "—");
  const facts = document.querySelector("#relation-case-facts");
  const gaps = document.querySelector("#relation-case-gaps");
  const evidence = [...(report.facts || []), ...(report.gaps || [])];
  text("#relation-case-evidence-count", `${evidence.length} 条字段级记录 · ${report.tasks?.length || 0} 项待审批任务`);
  if (facts) {
    const rows = (report.facts || []).map((item) => `<li><strong>${escapeHtml(item.label)}</strong><span>${escapeHtml(item.detail)}</span><small>${escapeHtml(item.strength === "candidate" ? "关联候选" : "直接事实")} · ${escapeHtml(item.evidence_id)}</small></li>`);
    facts.innerHTML = rows.length ? rows.join("") : "<li>当前没有直接事实记录。</li>";
  }
  if (gaps) {
    const rows = (report.gaps || []).map((item) => `<li><strong>${escapeHtml(item.label)}</strong><span>${escapeHtml(item.detail)}</span><small>需补证 · ${escapeHtml(item.evidence_id)}</small>`);
    gaps.innerHTML = rows.length ? rows.join("") : "<li>当前没有登记的信息缺口。</li>";
  }
  const tasks = document.querySelector("#relation-case-tasks");
  if (tasks) {
    tasks.innerHTML = (report.tasks || []).map((item) => `<tr><td><strong>${escapeHtml(item.title)}</strong><br><code>${escapeHtml(item.task_id)}</code></td><td>${escapeHtml(item.owner_role)}</td><td>${escapeHtml(item.due_hint)}</td><td>${escapeHtml(item.acceptance_criteria)}</td><td>${(item.evidence_ids || []).map((id) => `<code>${escapeHtml(id)}</code>`).join("<br>")}</td></tr>`).join("") || `<tr><td colspan="5">当前案卷没有任务草案。</td></tr>`;
  }
  const output = $("#relation-case-output");
  if (output && !output.hidden) output.textContent = JSON.stringify({ ...report, public_demo_boundary: "browser_static_read_only" }, null, 2);
}

async function loadRelationCases() {
  const response = await fetch("./data/round2_reports.json", { cache: "no-store" });
  if (!response.ok) throw new Error(`关系案卷读取失败（HTTP ${response.status}）`);
  const payload = await response.json();
  if (payload.schema_version !== "2.0" || payload.data_boundary !== "schema_and_relationship_reference_only" || !Array.isArray(payload.reports)) {
    throw new Error("关系案卷不符合公开数据边界合同");
  }
  state.relationReports = payload.reports;
  const select = $("#relation-case-select");
  if (select) {
    select.replaceChildren(...payload.reports.map((report, index) => {
      const option = document.createElement("option");
      option.value = String(index);
      option.textContent = `${report.case.case_id} · ${report.case.title}`;
      return option;
    }));
  }
  renderRelationCase();
}

function bindRelationCaseInteractions() {
  const select = $("#relation-case-select");
  select?.addEventListener("change", () => {
    state.relationIndex = Math.max(0, Math.min(Number(select.value) || 0, state.relationReports.length - 1));
    $("#relation-case-output")?.setAttribute("hidden", "");
    text("#relation-case-status", "已切换案卷，等待关系核验");
    renderRelationCase();
  });
  $("#relation-case-rerun")?.addEventListener("click", () => {
    const token = ++relationRunToken;
    const stages = ["读取对象与工单字段", "区分直接事实与关联候选", "检查缺口并生成任务草案"];
    let index = 0;
    const tick = () => {
      if (token !== relationRunToken) return;
      if (index >= stages.length) {
        text("#relation-case-status", "关系核验完成：页面未写入外部系统");
        return;
      }
      text("#relation-case-status", `正在${stages[index]}…`);
      index += 1;
      window.setTimeout(tick, 180);
    };
    tick();
  });
  $("#relation-case-copy")?.addEventListener("click", async () => {
    try {
      if (!navigator.clipboard?.writeText) throw new Error("Clipboard API unavailable");
      await navigator.clipboard.writeText(relationReportText());
      text("#relation-case-status", "案卷摘要已复制");
    } catch {
      text("#relation-case-status", "浏览器未授权剪贴板，可直接查看页面字段");
    }
  });
  $("#relation-case-preview")?.addEventListener("click", () => {
    const report = activeRelationReport();
    const output = $("#relation-case-output");
    if (!report || !output) return;
    output.hidden = false;
    output.textContent = JSON.stringify({
      case: report.case,
      evidence_ids: [...(report.facts || []), ...(report.gaps || [])].map((item) => item.evidence_id),
      task_ids: (report.tasks || []).map((item) => item.task_id),
      human_approval_required: true,
      external_write: false,
      public_demo_mode: report.terra?.mode || "deterministic",
    }, null, 2);
    text("#relation-case-status", `任务预览已更新，共 ${(report.tasks || []).length} 项，需具名审批后才能执行`);
  });
}

function buildLocalLivePayload(running = localLiveState.running) {
  const baseCard = cloneJson(state.cards.risk);
  const baseSeries = cloneJson(state.series.risk || []);
  if (!baseCard || !baseSeries.length) return { active: false, running: false, lastError: "公开演示数据尚未载入。" };

  const sequence = Math.max(1, localLiveState.sequence || 1);
  const offset = ((sequence % 5) - 2) * 0.015;
  const series = baseSeries.map((event, index) => ({
    ...event,
    torque_nm: Number((Number(event.torque_nm) + offset + ((index + sequence) % 7 === 0 ? 0.04 : 0)).toFixed(3)),
  }));
  const card = {
    ...baseCard,
    card_id: `${baseCard.card_id}-BROWSER-${String(sequence).padStart(2, "0")}`,
    created_at: "2026-08-17T09:00:00+08:00",
  };
  const latestEvents = series.slice(Math.max(0, series.length - 24));
  return {
    active: true,
    running,
    source: "browser_static_replay",
    scenario: "规格内扭矩漂移（浏览器回放）",
    sequence,
    card,
    series,
    history: series,
    latestEvents,
    boundary: "浏览器回放。数据为比赛合成样例，不代表真实工厂数据，也不会访问外部系统或执行生产动作。",
  };
}

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

const workflowStatusLabels = {
  monitoring_only: "稳定监控",
  awaiting_engineer_review: "等待工程师复核",
  approved: "已批准，待创建任务",
  rejected: "已驳回",
  tasks_created: "任务已创建",
  verification_in_progress: "现场验证中",
  verified: "验证已通过",
  closed: "已关闭",
};

function calibrationLabel(days) {
  if (days < 0) return `标定已逾期 ${Math.abs(days)} 天`;
  if (days === 0) return "标定今天到期";
  return `距标定到期 ${days} 天`;
}

function evidenceByCategory(card, category) {
  return card.evidence?.find((item) => item.category === category) || null;
}

function activeSeries() {
  return state.series[state.mode];
}

function activeCard() {
  return state.cards[state.mode];
}

function activeAnalysis() {
  return authoritativeAnalysis(activeCard(), state.results[state.mode]);
}

const metricAvailabilityLabels = {
  current_a: "工具电流",
  cycle_time_s: "循环时间",
};

const evidenceCategoryLabels = {
  spc: "SPC 过程证据",
  equipment: "设备状态证据",
  pfmea: "PFMEA 知识",
  control_plan: "控制计划",
  history: "历史案例",
};

const comparisonMetricOrder = [
  "torque_mean_nm",
  "angle_dispersion_ratio",
  "retry_mean",
  "in_spec_rate",
];

const comparisonRuleLabels = {
  "MW-24": "最近窗口滚动均值偏移",
  angle_dispersion_and_retry_increase: "角度离散与重试同步上升",
};

function comparisonDigits(unit) {
  return unit === "次/循环" ? 3 : unit === "%" ? 1 : 2;
}

function formatComparisonNumber(value, unit, signed = false) {
  const normalized = Math.abs(value) < 0.0005 ? 0 : value;
  const prefix = signed && normalized > 0 ? "+" : "";
  return `${prefix}${normalized.toFixed(comparisonDigits(unit))}${unit === "%" ? "%" : ` ${unit}`}`;
}

function comparisonRuleLabel(ruleId) {
  const label = comparisonRuleLabels[ruleId];
  return label ? `${label}（${ruleId}）` : `${ruleId}（待人工核对）`;
}

export function comparisonViewModel(card, generatedResult) {
  const result = authoritativeAnalysis(card, generatedResult);
  const stratum = card.analysis_provenance?.analysis_stratum || {};
  const inputFile = card.agent_trace
    ?.map((item) => item?.input_summary?.file)
    .find((value) => typeof value === "string" && value.trim());
  const rows = [...result.comparisons]
    .sort((left, right) => comparisonMetricOrder.indexOf(left.metricId) - comparisonMetricOrder.indexOf(right.metricId))
    .map((item) => ({
      ...item,
      baselineLabel: formatComparisonNumber(item.baseline, item.unit),
      currentLabel: formatComparisonNumber(item.current, item.unit),
      deltaLabel: formatComparisonNumber(item.delta, item.unit, true),
      judgment: item.status === "triggered"
        ? `已触发：${item.ruleIds.map(comparisonRuleLabel).join("；")}`
        : "未触发 · 保持监控",
    }));

  return {
    question: `在 ${stratum.model_code} / ${stratum.program_id} / ${card.fastening_point} 的相同分层内，最近 ${result.recentCount} 条相对前 ${result.baselineCount} 条发生了什么变化？`,
    source: `${inputFile || "风险卡记录的数据源"} · ${card.station_id} · ${card.tool_id} · ${card.fastening_point} · 比赛合成数据`,
    window: `前 ${result.baselineCount} 条基线 vs 最近 ${result.recentCount} 条当前窗口`,
    rows,
    nextStep: result.attributionRequired
      ? `先由具名工程师复核证据，再查看 ${card.recommended_actions.length} 项浏览器处置任务预览；网页不会直接派单。`
      : "当前没有触发异常规则，继续按相同分层滚动监控，无需归因或创建处置任务。",
  };
}

export function linkedDecisionPath(card, comparison) {
  const evidenceIds = new Set(comparison.evidenceIds || []);
  const causes = comparison.status === "triggered"
    ? (card.candidate_causes || []).filter((cause) =>
      (cause.evidence_ids || []).some((evidenceId) => evidenceIds.has(evidenceId)))
    : [];
  const causeNames = new Set(causes.map((cause) => cause.cause));
  const actions = comparison.status === "triggered"
    ? (card.recommended_actions || []).filter((action) =>
      (action.evidence_ids || []).some((evidenceId) => evidenceIds.has(evidenceId))
      && (action.candidate_causes || []).some((cause) => causeNames.has(cause)))
    : [];
  return {
    ruleIds: [...(comparison.ruleIds || [])],
    evidenceIds: [...evidenceIds],
    causes,
    actions,
  };
}

export function analysisOverview(card, result, series) {
  if (!card || typeof card !== "object" || !result || typeof result !== "object" || !Array.isArray(series)) {
    throw new TypeError("分析说明需要风险卡、权威结果和时序列表");
  }
  const stratum = card.analysis_provenance?.analysis_stratum || {};
  const availability = card.analysis_provenance?.metric_availability || {};
  const signalItems = [
    `扭矩：基线均值 ${result.baselineMean.toFixed(2)} N·m，最近窗口均值 ${result.recentMean.toFixed(2)} N·m，偏移 ${result.meanShiftSigma.toFixed(2)}σ`,
    `规格边界：当前窗口规格内比例 ${formatPercent(result.inSpecRate)}，SPC 规则 ${result.ruleIds.length ? result.ruleIds.join("、") : "未触发"}`,
    `角度与重试：角度离散为基线的 ${result.angleRatio.toFixed(2)} 倍，最近重试 ${result.retryRecent.toFixed(3)} 次/循环`,
    `展示曲线：已载入 ${series.length} 条；评分合同固定使用 ${result.baselineCount} 条基线与 ${result.recentCount} 条当前窗口`,
  ];
  Object.entries(availability).forEach(([name, metric]) => {
    const label = metricAvailabilityLabels[name] || name;
    signalItems.push(
      metric?.available === true
        ? `${label}：可用（基线 ${metric.baseline_sample_count}/${metric.baseline_required_count}，最近 ${metric.recent_sample_count}/${metric.recent_required_count}）`
        : `${label}：不可用（${metric?.reason || "风险卡未说明原因"}）`,
    );
  });

  const categoryCounts = new Map();
  (card.evidence || []).forEach((item) => {
    const category = item.category || "uncategorized";
    categoryCounts.set(category, (categoryCounts.get(category) || 0) + 1);
  });
  const knowledgeItems = [...categoryCounts.entries()].map(
    ([category, count]) => `${evidenceCategoryLabels[category] || category}：${count} 条`,
  );

  return {
    object: `${card.station_id} · ${card.tool_id} · ${card.fastening_point}`,
    question: "当前窗口是否相对基线出现规格内风险？",
    scope: `${stratum.model_code || "未声明车型"} / ${stratum.program_id || "未声明程序"} / ${stratum.fastening_point || card.fastening_point}`,
    window: `前 ${result.baselineCount} 条基线 vs 最近 ${result.recentCount} 条当前窗口`,
    signalItems,
    knowledgeItems: knowledgeItems.length ? knowledgeItems : ["当前风险卡没有知识证据"],
    output: `${levelLabel(result.level)} ${result.score} 分 · ${workflowStatusLabels[result.status] || result.status}`,
    progress: {
      value: 5,
      maximum: 5,
      label: result.attributionRequired
        ? "5/5 已完成：组合信号已进入人工研判工作流"
        : "5/5 已完成：未触发异常归因，保持稳定监控",
    },
  };
}

function renderTextList(selector, items) {
  const container = $(selector);
  if (!container) return;
  container.replaceChildren();
  items.forEach((value) => {
    const item = document.createElement("li");
    item.textContent = value;
    container.appendChild(item);
  });
}

function renderAnalysisOverview() {
  const overview = analysisOverview(activeCard(), activeAnalysis(), activeSeries());
  text("#analysis-object", overview.object);
  text("#analysis-question", overview.question);
  text("#analysis-scope", overview.scope);
  text("#analysis-window", overview.window);
  renderTextList("#analysis-signal-list", overview.signalItems);
  renderTextList("#analysis-knowledge-list", overview.knowledgeItems);
  text("#analysis-output", overview.output);
  const progress = $("#analysis-progress");
  if (progress) {
    progress.textContent = overview.progress.label;
    progress.setAttribute("role", "progressbar");
    progress.setAttribute("aria-valuemin", "0");
    progress.setAttribute("aria-valuemax", String(overview.progress.maximum));
    progress.setAttribute("aria-valuenow", String(overview.progress.value));
    if (progress.tagName === "PROGRESS") {
      progress.max = overview.progress.maximum;
      progress.value = overview.progress.value;
    }
  }
}

function tableCell(row, label, value, className = "") {
  const cell = document.createElement("td");
  cell.dataset.label = label;
  if (className) cell.className = className;
  cell.textContent = value;
  row.appendChild(cell);
  return cell;
}

function applyDecisionHighlights(viewModel, comparison) {
  const card = activeCard();
  const path = linkedDecisionPath(card, comparison);
  const evidenceSet = new Set(path.evidenceIds);
  const causeSet = new Set(path.causes);
  const actionSet = new Set(path.actions);

  [...$("#evidence-list").children].forEach((element, index) => {
    const evidence = card.evidence[index];
    element.classList.toggle("is-linked", evidenceSet.has(evidence?.evidence_id));
  });
  [...$("#cause-table-body").children].forEach((element, index) => {
    element.classList.toggle("is-linked", causeSet.has(card.candidate_causes[index]));
  });
  [...$("#task-table-body").children].forEach((element, index) => {
    element.classList.toggle("is-linked", actionSet.has(card.recommended_actions[index]));
  });
  $("#signal-rules")?.classList.toggle("is-linked", path.ruleIds.length > 0);

  const stageDetails = [
    `${comparison.label}：${comparison.baselineLabel} → ${comparison.currentLabel}`,
    path.ruleIds.length ? path.ruleIds.map(comparisonRuleLabel).join("；") : "未触发规则",
    path.evidenceIds.length ? path.evidenceIds.join(" · ") : "没有关联证据",
    path.causes.length ? path.causes.map((cause) => cause.cause).join("；") : "无需形成候选原因",
    path.actions.length ? path.actions.map((action) => action.action_id).join(" · ") : "无需生成处置任务",
  ];
  const activeStages = [
    true,
    path.ruleIds.length > 0,
    path.evidenceIds.length > 0,
    path.causes.length > 0,
    path.actions.length > 0,
  ];
  [...$("#dependency-flow").children].forEach((node, index) => {
    node.classList.toggle("is-active", activeStages[index]);
    node.classList.toggle("is-disabled", !activeStages[index]);
    node.setAttribute("aria-disabled", String(!activeStages[index]));
    const detail = node.querySelector("small");
    if (detail) detail.textContent = stageDetails[index];
  });

  const dependencyDetail = $("#dependency-detail");
  dependencyDetail.setAttribute("aria-live", "polite");
  dependencyDetail.textContent = comparison.status === "triggered"
    ? `${comparison.label}已连接 ${path.ruleIds.length} 条规则、${path.evidenceIds.length} 条证据、${path.causes.length} 项待验证原因和 ${path.actions.length} 项人工门禁任务。页面仅展示引用关系，不表示根因已确认。`
    : `${comparison.label}未触发异常规则；${path.evidenceIds.join("、") || "当前证据"}仅作为本次窗口记录留档，不继续生成原因或任务。`;
  text("#comparison-next-step", viewModel.nextStep);
}

function renderComparison() {
  const viewModel = comparisonViewModel(activeCard(), state.results[state.mode]);
  const body = $("#comparison-table-body");
  const selectedStillExists = viewModel.rows.some((row) => row.metricId === state.selectedMetricId);
  if (!selectedStillExists) {
    state.selectedMetricId = viewModel.rows.find((row) => row.status === "triggered")?.metricId
      || viewModel.rows[0]?.metricId
      || null;
  }

  text("#comparison-question", viewModel.question);
  text("#comparison-source", viewModel.source);
  text("#comparison-window", viewModel.window);
  body.replaceChildren();

  viewModel.rows.forEach((item) => {
    const selected = item.metricId === state.selectedMetricId;
    const row = document.createElement("tr");
    row.classList.toggle("is-selected", selected);

    const metricCell = document.createElement("td");
    metricCell.dataset.label = "观察指标";
    const controls = document.createElement("div");
    controls.className = "comparison-metric-controls";
    const button = document.createElement("button");
    button.className = "comparison-metric-button";
    button.type = "button";
    button.dataset.metricId = item.metricId;
    button.setAttribute("aria-pressed", String(selected));
    button.setAttribute("aria-label", `查看${item.label}对应的规则、证据、候选原因和任务`);
    const label = document.createElement("strong");
    label.textContent = item.label;
    const hint = document.createElement("small");
    hint.textContent = selected ? "已选择 · 依据链见右侧" : "选择查看依据链";
    button.append(label, hint);
    controls.appendChild(button);
    metricCell.appendChild(controls);
    row.appendChild(metricCell);

    tableCell(row, "基线", item.baselineLabel, "comparison-value");
    tableCell(row, "最近窗口", item.currentLabel, "comparison-value");
    tableCell(row, "变化", item.deltaLabel, `comparison-delta comparison-${item.status}`);
    const judgment = tableCell(row, "分析判定", item.judgment, `comparison-judgment comparison-${item.status}`);
    judgment.setAttribute("data-status", item.status === "triggered" ? "已触发" : "未触发");
    body.appendChild(row);
  });

  const selected = viewModel.rows.find((row) => row.metricId === state.selectedMetricId);
  if (selected) applyDecisionHighlights(viewModel, selected);
}

function selectComparisonMetric(metricId, restoreFocus = false) {
  const viewModel = comparisonViewModel(activeCard(), state.results[state.mode]);
  if (!viewModel.rows.some((row) => row.metricId === metricId)) return;
  state.selectedMetricId = metricId;
  renderComparison();
  if (restoreFocus) {
    requestAnimationFrame(() => {
      const button = [...document.querySelectorAll("#comparison-table-body [data-metric-id]")]
        .find((item) => item.dataset.metricId === metricId);
      button?.focus();
    });
  }
}

const adminStatusLabels = {
  healthy: "审计健康",
  attention: "审计通过 · 等待人工审批",
  warning: "存在非阻断提醒",
  blocked: "存在阻断项",
};

const adminDemoSteps = [
  { target: "#overview", label: "确认数据对象、风险等级与窗口口径" },
  { target: "#comparison", label: "查看基线与最近窗口的同口径变化" },
  { target: "#risk-analysis", label: "核对规则触发与风险分项依据" },
  { target: "#evidence", label: "核对证据出处与待验证原因引用" },
  { target: "#workflow", label: "确认处置任务、人工门禁与同步边界" },
  { target: "#evaluation", label: "查看合成场景评估并声明效果边界" },
];

function installAdminWorkbench() {
  if ($("#admin-workbench")) return;

  const backdrop = document.createElement("button");
  backdrop.className = "admin-drawer-backdrop";
  backdrop.id = "admin-drawer-backdrop";
  backdrop.type = "button";
  backdrop.tabIndex = -1;
  backdrop.setAttribute("aria-label", "关闭管理员审计台");

  const drawer = document.createElement("aside");
  drawer.className = "admin-drawer";
  drawer.id = "admin-drawer";
  drawer.setAttribute("role", "dialog");
  drawer.setAttribute("aria-modal", "true");
  drawer.setAttribute("aria-hidden", "true");
  drawer.setAttribute("aria-labelledby", "admin-workbench-title");

  const closeButton = document.createElement("button");
  closeButton.className = "admin-drawer-close";
  closeButton.id = "admin-close";
  closeButton.type = "button";
  closeButton.setAttribute("aria-label", "关闭管理员审计台");
  closeButton.textContent = "关闭";

  const section = document.createElement("section");
  section.className = "section-block";
  section.id = "admin-workbench";
  section.setAttribute("aria-labelledby", "admin-workbench-title");
  section.innerHTML = `
    <div class="section-heading">
      <div><span class="section-kicker">PUBLIC READ-ONLY ADMIN</span><h2 id="admin-workbench-title">管理员审计台</h2></div>
      <div class="workflow-action">
        <span id="admin-overall" tabindex="-1" aria-live="polite">正在核对</span>
        <button class="button secondary" id="admin-copy-command" type="button">复制复现命令</button>
        <button class="button primary" id="admin-export-summary" type="button">导出当前分析摘要</button>
      </div>
    </div>
    <div class="agent-audit-grid" aria-label="管理员数据与治理摘要">
      <article class="panel audit-panel">
        <div class="audit-panel-heading"><div><span class="section-kicker">Data Steward</span><h3>数据与证据健康</h3></div><span class="audit-status" id="admin-data-health">载入中</span></div>
        <dl class="audit-kv audit-kv-wide">
          <div><dt>证据引用</dt><dd id="admin-evidence-health">—</dd></div>
          <div><dt>分析轨迹</dt><dd id="admin-trace-health">—</dd></div>
          <div><dt>修订指纹</dt><dd id="admin-revision-health">—</dd></div>
          <div><dt>当前卡号</dt><dd><code id="admin-card-id">—</code></dd></div>
        </dl>
      </article>
      <article class="panel audit-panel">
        <div class="audit-panel-heading"><div><span class="section-kicker">Safety Gate</span><h3>审批与同步边界</h3></div><span class="gate-status" id="admin-gate-health">载入中</span></div>
        <dl class="audit-kv audit-kv-wide">
          <div><dt>工作流门禁</dt><dd id="admin-workflow-gate">—</dd></div>
          <div><dt>外部同步</dt><dd id="admin-sync-health">—</dd></div>
          <div><dt>网页写权限</dt><dd id="admin-external-write">明确禁用</dd></div>
          <div><dt>阻断原因</dt><dd id="admin-blocking-reasons">—</dd></div>
        </dl>
      </article>
    </div>
    <article class="panel audit-panel" aria-labelledby="admin-demo-title">
      <div class="audit-panel-heading">
        <div><span class="section-kicker">Demo Runbook</span><h3 id="admin-demo-title">管理员演示步骤</h3></div>
        <div class="workflow-action"><span id="admin-step-state">步骤 1/6</span><button class="button text-button" id="admin-next-step" type="button">下一步</button></div>
      </div>
      <ol class="agent-trace" id="admin-demo-steps"></ol>
      <p class="integration-note" id="admin-boundary"></p>
    </article>`;

  drawer.append(closeButton, section);
  document.body.append(backdrop, drawer);

  const stepList = $("#admin-demo-steps");
  adminDemoSteps.forEach((step, index) => {
    const item = document.createElement("li");
    const button = document.createElement("button");
    button.className = "button text-button";
    button.type = "button";
    button.dataset.adminTarget = step.target;
    button.dataset.adminStep = String(index);
    button.textContent = `${index + 1}. ${step.label}`;
    item.appendChild(button);
    stepList.appendChild(item);
  });

  if (!$("#admin-open")) {
    const openButton = document.createElement("button");
    openButton.className = "button secondary admin-launch";
    openButton.id = "admin-open";
    openButton.type = "button";
    openButton.setAttribute("aria-controls", "admin-drawer");
    openButton.setAttribute("aria-expanded", "false");
    openButton.textContent = "打开管理员审计台";
    $(".hero-actions")?.appendChild(openButton);
  }
}

function renderAdminWorkbench() {
  const snapshot = buildAdminSnapshot(activeCard(), activeAnalysis(), { mode: state.mode });
  const checks = new Map(snapshot.dataHealth.checks.map((check) => [check.id, check]));
  const evidence = snapshot.evidence;
  const traceCheck = checks.get("agent_trace");
  const revisionCheck = checks.get("provenance_revisions");

  text("#admin-overall", adminStatusLabels[snapshot.overallStatus] || snapshot.overallStatus);
  $("#admin-overall")?.classList.toggle("sync-error", snapshot.overallStatus === "blocked");
  text("#admin-data-health", snapshot.dataHealth.status === "pass" ? "全部通过" : snapshot.dataHealth.status === "warning" ? "存在提醒" : "未通过");
  text(
    "#admin-evidence-health",
    evidence.requirement === "not_required"
      ? `${evidence.storedCount} 条留档 · 稳定窗口无需归因引用`
      : `${evidence.referencedCount} 个唯一引用 · 完整率 ${(evidence.referenceCompleteness * 100).toFixed(0)}%`,
  );
  text("#admin-trace-health", traceCheck?.detail || "未返回轨迹检查");
  text("#admin-revision-health", revisionCheck?.detail || "未返回修订检查");
  text("#admin-card-id", snapshot.case.cardId);

  const gateLabels = {
    awaiting_named_approval: "等待具名工程师审批",
    approval_recorded_or_in_progress: "审批已记录或处置进行中",
    blocked_for_reconciliation: "同步异常，已阻断并要求对账",
    not_required: "稳定监控，无异常审批",
  };
  text("#admin-gate-health", snapshot.approval.automaticStopDisabled ? "自动停线已禁止" : "边界异常");
  text("#admin-workflow-gate", gateLabels[snapshot.approval.gateStatus] || snapshot.approval.gateStatus);
  const syncLabels = {
    not_attempted: "未尝试 · 公开只读预览",
    succeeded: "风险卡记录同步成功回执",
    partial: "部分成功 · 必须人工对账",
    failed: "同步失败 · 禁止继续",
  };
  text("#admin-sync-health", syncLabels[snapshot.sync.status] || snapshot.sync.status);
  text("#admin-external-write", "禁用 · 页面不会上传、派单或写入飞书");
  text("#admin-blocking-reasons", snapshot.blockingReasons.length ? snapshot.blockingReasons.join("；") : "无数据完整性阻断项");
  text("#admin-boundary", ADMIN_BOUNDARY_NOTICE);
}

function adminDrawerFocusableElements() {
  const drawer = $("#admin-drawer");
  if (!drawer) return [];
  return [...drawer.querySelectorAll("button:not([disabled]), a[href], [tabindex]:not([tabindex=\"-1\"])")]
    .filter((element) => !element.hidden && element.getAttribute("aria-hidden") !== "true");
}

function setAdminDrawerOpen(open, restoreFocus = true) {
  const drawer = $("#admin-drawer");
  const backdrop = $("#admin-drawer-backdrop");
  const openButton = $("#admin-open");
  if (!drawer || !backdrop || !openButton) return;

  drawer.classList.toggle("open", open);
  backdrop.classList.toggle("open", open);
  drawer.style.visibility = open ? "visible" : "hidden";
  drawer.setAttribute("aria-hidden", String(!open));
  openButton.setAttribute("aria-expanded", String(open));
  document.body.classList.toggle("admin-drawer-open", open);

  if (open) {
    state.adminReturnFocus = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : openButton;
    drawer.getBoundingClientRect();
    window.setTimeout(() => $("#admin-close")?.focus({ preventScroll: true }), 80);
    return;
  }

  if (restoreFocus) {
    const returnTarget = state.adminReturnFocus;
    if (returnTarget instanceof HTMLElement && returnTarget.isConnected) returnTarget.focus({ preventScroll: true });
    else openButton.focus({ preventScroll: true });
  }
  state.adminReturnFocus = null;
}

function selectAdminDemoStep(index, scroll = true) {
  state.adminDemoStep = ((index % adminDemoSteps.length) + adminDemoSteps.length) % adminDemoSteps.length;
  const step = adminDemoSteps[state.adminDemoStep];
  document.querySelectorAll("[data-admin-step]").forEach((button) => {
    const current = Number(button.dataset.adminStep) === state.adminDemoStep;
    if (current) button.setAttribute("aria-current", "step");
    else button.removeAttribute("aria-current");
  });
  text("#admin-step-state", `步骤 ${state.adminDemoStep + 1}/${adminDemoSteps.length}：${step.label}`);
  if (scroll) {
    setAdminDrawerOpen(false, false);
    activateSidebarTarget(step.target, true);
    holdSidebarTarget(step.target);
    $(step.target)?.scrollIntoView({ behavior: "smooth", block: "start" });
  }
}

function sidebarTargets() {
  return [...document.querySelectorAll('.sidebar nav a[href^="#"]')]
    .map((link) => {
      const section = document.querySelector(link.hash);
      return {
        link,
        section,
        top: section ? section.getBoundingClientRect().top + window.scrollY : 0,
      };
    })
    .filter((item) => item.section)
    .sort((left, right) => left.top - right.top);
}

function activateSidebarTarget(hash, reveal = false) {
  const targets = sidebarTargets();
  const selected = targets.find((item) => item.link.hash === hash);
  if (!selected) return;

  targets.forEach(({ link }) => {
    const active = link === selected.link;
    link.classList.toggle("active", active);
    if (active) link.setAttribute("aria-current", "location");
    else link.removeAttribute("aria-current");
  });

  if (reveal) {
    const nav = selected.link.closest("nav");
    if (nav && nav.scrollWidth > nav.clientWidth) {
      nav.scrollTo({
        left: selected.link.offsetLeft - ((nav.clientWidth - selected.link.offsetWidth) / 2),
        behavior: "auto",
      });
    }
  }
}

function holdSidebarTarget(hash) {
  sidebarNavigationLock = hash;
  if (sidebarNavigationTimer) window.clearTimeout(sidebarNavigationTimer);
  sidebarNavigationTimer = window.setTimeout(() => {
    sidebarNavigationLock = null;
    sidebarNavigationTimer = 0;
    syncSidebarToScroll();
  }, 1200);
}

function syncSidebarToScroll() {
  if (sidebarNavigationLock) {
    activateSidebarTarget(sidebarNavigationLock, true);
    return;
  }
  const targets = sidebarTargets();
  if (!targets.length) return;
  const probe = window.scrollY + Math.min(window.innerHeight * 0.28, 240);
  let selected = targets[0];
  targets.forEach((item) => {
    if (item.top <= probe) selected = item;
  });
  activateSidebarTarget(selected.link.hash, true);
}

function bindSidebarNavigation() {
  const nav = $(".sidebar nav");
  if (!nav || nav.dataset.navigationBound === "true") return;
  nav.dataset.navigationBound = "true";

  nav.addEventListener("click", (event) => {
    const link = event.target.closest('a[href^="#"]');
    if (link && nav.contains(link)) {
      activateSidebarTarget(link.hash, true);
      holdSidebarTarget(link.hash);
    }
  });

  let pendingFrame = 0;
  const scheduleSync = () => {
    if (pendingFrame) cancelAnimationFrame(pendingFrame);
    pendingFrame = requestAnimationFrame(() => {
      pendingFrame = 0;
      syncSidebarToScroll();
    });
  };
  window.addEventListener("scroll", scheduleSync, { passive: true });
  window.addEventListener("resize", scheduleSync);

  const initialHash = window.location.hash;
  if (initialHash && sidebarTargets().some((item) => item.link.hash === initialHash)) {
    activateSidebarTarget(initialHash, true);
  } else {
    syncSidebarToScroll();
  }
}

function updateHeader(result) {
  const card = activeCard();
  const riskChip = $("#risk-chip");
  riskChip.className = `risk-chip risk-${result.level}`;
  riskChip.textContent = levelLabel(result.level);
  text("#risk-score", String(result.score));
  text("#metric-state", workflowStatusLabels[result.status] || result.status);
  text("#metric-state-note", card.workflow?.human_approval_required ? "状态变更必须具名审批" : "保持常规监控，无异常审批");
  text("#metric-shift", `${result.meanShiftSigma.toFixed(2)}σ`);
  text("#metric-in-spec", formatPercent(result.inSpecRate));
  text("#metric-retry", `${result.retryRecent.toFixed(3)} 次/循环`);
  text("#metric-baseline-note", `基线：前 ${result.baselineCount} 次`);
  text("#metric-spec-note", result.inSpecRate === 1 ? "当前窗口未出现规格越限" : "当前窗口存在规格越限记录");
  text("#metric-retry-note", `角度离散为基线的 ${result.angleRatio.toFixed(2)} 倍`);
  text(".breadcrumb", `风险台账 / ${card.card_id}`);

  const ruleSummary = result.ruleIds.length
    ? `触发规则 ${result.ruleIds.join("、")}`
    : "未触发 SPC 或设备组合异常规则";
  text(
    "#signal-summary",
    `最近 ${result.recentCount} 次扭矩均值偏移 ${result.meanShiftSigma.toFixed(2)}σ，角度离散为基线的 ${result.angleRatio.toFixed(2)} 倍，重试均值为 ${result.retryRecent.toFixed(3)} 次/循环；${ruleSummary}，规格内比例为 ${formatPercent(result.inSpecRate)}。`,
  );
  text("#signal-rules", result.ruleIds.length ? result.ruleIds.join("、") : "未触发规则，保持稳定监控");
  text("#signal-calibration", calibrationLabel(result.calibrationDaysRemaining));
  const pfmeaEvidence = evidenceByCategory(card, "pfmea");
  text(
    "#signal-product-impact",
    result.attributionRequired && pfmeaEvidence
      ? `${pfmeaEvidence.title}：${pfmeaEvidence.observation}`
      : "当前窗口稳定，未升级为产品影响研判",
  );
  text(
    "#signal-required-evidence",
    card.candidate_causes.length
      ? `${card.candidate_causes.length} 项候选原因需按现场验证路径补证`
      : "稳定窗口，无需归因或补充异常证据",
  );

  text("#main-title", result.attributionRequired ? "检测到需要工程师研判的组合信号" : "当前窗口稳定，保持常规监控");
  text("#main-lead", card.observed_facts?.at(-1) || card.inference);
  text("#signals-title", result.attributionRequired ? "组合信号形成可追溯的风险触发" : "当前窗口未形成异常组合信号");
  text(
    "#signals-description",
    result.attributionRequired
      ? `当前 ${result.recentCount} 条窗口由规则、设备信号和知识证据共同解释，结论仍需工程师验证。`
      : `当前 ${result.recentCount} 条窗口沿用相同 RiskAnalyzer 口径，未达到异常归因和派单条件。`,
  );
}

function renderChart(series, result) {
  const chartWidth = 880;
  const chartHeight = 260;
  const minimum = 43;
  const maximum = 53;
  if (!Array.isArray(series) || series.length < result.recentCount) {
    throw new Error("时序数据少于权威结果声明的最近窗口数量");
  }
  const values = series.map((row) => Number(row.torque_nm));
  if (values.some((value) => !Number.isFinite(value))) throw new Error("时序数据包含无效扭矩值");
  const path = pathFor(values, chartWidth, chartHeight, minimum, maximum);
  $("#torque-line").setAttribute("d", path);

  const baselineY = chartHeight - ((result.baselineMean - minimum) / (maximum - minimum)) * chartHeight;
  $("#baseline-line").setAttribute("y1", baselineY);
  $("#baseline-line").setAttribute("y2", baselineY);
  text("#chart-baseline-label", `基线 ${result.baselineMean.toFixed(2)} N·m`);

  const riskStart = series.findIndex((row) => row.scenario_label === "hidden_risk");
  const riskZone = $("#risk-zone");
  if (result.attributionRequired && riskStart >= 0) {
    const x = (riskStart / Math.max(series.length - 1, 1)) * chartWidth;
    riskZone.setAttribute("x", x);
    riskZone.setAttribute("width", chartWidth - x);
    riskZone.setAttribute("display", "block");
    riskZone.style.display = "block";
  } else {
    riskZone.setAttribute("display", "none");
    riskZone.style.display = "none";
  }

  const recent = series.slice(-result.recentCount);
  const latest = recent[recent.length - 1];
  if (typeof recent[0]?.timestamp !== "string" || typeof latest?.timestamp !== "string") {
    throw new Error("时序数据缺少最近窗口时间戳");
  }
  text("#chart-window", `${recent[0].timestamp.slice(11, 16)}–${latest.timestamp.slice(11, 16)}`);
  text("#chart-latest", `${Number(latest.torque_nm).toFixed(2)} N·m`);
  text("#chart-heading", `扭矩趋势 · ${activeCard().fastening_point}`);
  text("#chart-title", `${activeCard().fastening_point} 紧固点最近 ${result.recentCount} 次扭矩趋势`);
  text(
    "#chart-desc",
    result.attributionRequired
      ? `最近 ${result.recentCount} 次扭矩的均值较基线偏移 ${result.meanShiftSigma.toFixed(2)} 个标准差，规格内比例 ${formatPercent(result.inSpecRate)}，风险着色区标出触发窗口。`
      : `最近 ${result.recentCount} 次扭矩的均值较基线偏移 ${result.meanShiftSigma.toFixed(2)} 个标准差，未形成需要异常归因的组合信号。`,
  );
}

function renderBreakdown(result) {
  const labels = {
    process_stability: "过程稳定性",
    equipment_health: "设备健康",
    quality_impact: "质量影响",
    context: "知识与上下文",
  };
  const maximums = { process_stability: 35, equipment_health: 25, quality_impact: 25, context: 15 };
  const container = $("#score-breakdown");
  container.replaceChildren();
  Object.keys(labels).forEach((key) => {
    const value = result.breakdown[key];
    const row = document.createElement("div");
    row.className = "score-row";
    row.innerHTML = `
      <div class="score-label"><span>${escapeHtml(labels[key])}</span><strong>${escapeHtml(value)}/${escapeHtml(maximums[key])}</strong></div>
      <div class="score-track"><span style="width:${(value / maximums[key]) * 100}%"></span></div>`;
    container.appendChild(row);
  });
}

function renderEvidence() {
  const container = $("#evidence-list");
  container.replaceChildren();
  activeCard().evidence.forEach((item, index) => {
    const detail = document.createElement("details");
    detail.className = "evidence-item";
    detail.dataset.evidenceId = item.evidence_id;
    if (index === 0) detail.open = true;
    detail.innerHTML = `
      <summary>
        <span class="evidence-type">${escapeHtml(item.category)}</span>
        <span class="evidence-heading"><strong>${escapeHtml(item.title)}</strong><small>${escapeHtml(item.observation)}</small></span>
        <span class="source-tag">${escapeHtml(item.evidence_id)}</span>
      </summary>
      <div class="evidence-detail">
        <dl>
          <div><dt>来源</dt><dd>${escapeHtml(item.source)}</dd></div>
          <div><dt>定位</dt><dd>${escapeHtml(item.locator)}</dd></div>
          <div><dt>证据强度</dt><dd>${escapeHtml(item.strength)}</dd></div>
        </dl>
        <pre>${escapeHtml(JSON.stringify(item.data, null, 2))}</pre>
      </div>`;
    container.appendChild(detail);
  });
}

function renderInference() {
  const card = activeCard();
  const result = activeAnalysis();
  const reasoning = card.reasoning || {};
  const supported = reasoning.decision === "supported";
  text("#inference-label", supported ? "待验证推断" : "稳定性判定");
  text(
    "#inference-title",
    supported
      ? "证据支持形成待验证假设，不能直接确认根因"
      : result.attributionRequired
        ? "证据不足，系统拒绝输出原因归因"
        : "当前窗口稳定，无需启动原因归因",
  );
  text(
    "#inference-body",
    card.inference
      || reasoning.conclusion?.text
      || reasoning.refusal_reason
      || "当前卡片未输出推断正文。",
  );
  text("#inference-uncertainty-label", result.attributionRequired ? "不能直接下结论：" : "监控边界：");
  text(
    "#inference-uncertainty",
    card.uncertainty
      || reasoning.uncertainty
      || reasoning.refusal_reason
      || "结论仅适用于当前分析分层与窗口。",
  );
}

function renderCauses() {
  const body = $("#cause-table-body");
  body.replaceChildren();
  const card = activeCard();
  const result = activeAnalysis();
  const causes = card.candidate_causes;
  text(
    "#cause-table-state",
    causes.length ? `${causes.length} 项假设 · 根因待现场验证` : "稳定窗口 · 无需归因",
  );
  if (!causes.length) {
    const row = document.createElement("tr");
    row.className = "empty-row";
    row.innerHTML = `<td class="empty-table-cell" colspan="4"><strong>${escapeHtml(
      result.attributionRequired ? "证据不足，未输出候选原因" : "当前窗口稳定，无需原因归因",
    )}</strong><span>${escapeHtml(
      result.attributionRequired ? "继续补充证据后再由工程师研判。" : "系统不会为稳定窗口制造根因假设。",
    )}</span></td>`;
    body.appendChild(row);
    return;
  }
  causes.forEach((item, index) => {
    const row = document.createElement("tr");
    row.dataset.causeName = item.cause;
    const confidenceClass = { high: "high", "medium-high": "mediumhigh", medium: "medium" }[item.confidence] || "medium";
    row.innerHTML = `
      <td data-label="候选原因"><span class="rank">${index + 1}</span>${escapeHtml(item.cause)}</td>
      <td data-label="置信度"><span class="confidence confidence-${confidenceClass}">${escapeHtml(confidenceLabel(item.confidence))}</span></td>
      <td data-label="依据">${escapeHtml(item.basis.join("；"))}<small class="cause-citations">引用：${escapeHtml(item.evidence_ids?.join(" · ") || "待结构化推理")}</small></td>
      <td data-label="现场验证">${escapeHtml(item.verification)}</td>`;
    body.appendChild(row);
  });
}

function workflowPresentation(card, result) {
  const hasActions = card.recommended_actions.length > 0;
  if (!result.attributionRequired || !hasActions) {
    return {
      title: "当前窗口稳定，无需归因或派单",
      workflowStatus: "保持常规监控；未创建异常处置任务",
      rowStatus: "无需执行",
      rowClass: "created",
      buttonText: "稳定窗口，无需任务预览",
      buttonDisabled: true,
      syncLabel: "不适用（稳定监控）",
      blocked: false,
    };
  }

  if (result.externalSyncStatus === "partial") {
    return {
      title: "外部同步不完整，必须先完成人工对账",
      workflowStatus: "部分记录状态不确定；禁止继续派单或自动重试",
      rowStatus: "需人工对账",
      rowClass: "blocked",
      buttonText: "同步不完整，禁止继续",
      buttonDisabled: true,
      syncLabel: "部分成功 · 需人工对账",
      blocked: true,
    };
  }
  if (result.externalSyncStatus === "failed") {
    return {
      title: "外部同步失败，任务创建状态未确认",
      workflowStatus: "同步失败；禁止把任务显示为已创建或继续执行",
      rowStatus: "同步失败",
      rowClass: "blocked",
      buttonText: "同步失败，禁止继续",
      buttonDisabled: true,
      syncLabel: "失败 · 创建状态未确认",
      blocked: true,
    };
  }
  if (result.externalTasksConfirmed) {
    return {
      title: "外部任务记录已核验并与公开工作流一致",
      workflowStatus: "外部同步成功；远端记录与公开任务状态均已核验",
      rowStatus: "外部已同步",
      rowClass: "created",
      buttonText: "外部任务已同步",
      buttonDisabled: true,
      syncLabel: "成功 · 远端记录已核验",
      blocked: false,
    };
  }

  const previewPrepared = state.previewPrepared[state.mode];
  return {
    title: "工程师确认后查看点检与追溯任务预览",
    workflowStatus: previewPrepared
      ? "浏览器任务预览已就绪，未发送到任何外部系统"
      : "等待具名工程师确认；当前未创建外部任务",
    rowStatus: previewPrepared ? "浏览器预览" : "待确认",
    rowClass: previewPrepared ? "created" : "pending",
    buttonText: previewPrepared ? "浏览器预览已就绪" : "人工确认并查看任务预览",
    buttonDisabled: previewPrepared,
    syncLabel: result.externalSyncPersisted ? "未尝试外部同步" : "公开预览 · 无外部同步记录",
    blocked: false,
  };
}

function renderActions() {
  const body = $("#task-table-body");
  body.replaceChildren();
  const card = activeCard();
  const result = activeAnalysis();
  const presentation = workflowPresentation(card, result);
  text("#workflow-title", presentation.title);
  if (!card.recommended_actions.length) {
    const row = document.createElement("tr");
    row.className = "empty-row";
    row.innerHTML = `<td class="empty-table-cell" colspan="5"><strong>当前窗口稳定，无需派单</strong><span>没有推荐动作，也不会创建空任务或异常审批。</span></td>`;
    body.appendChild(row);
  }
  card.recommended_actions.forEach((item) => {
    const row = document.createElement("tr");
    row.dataset.actionId = item.action_id;
    row.innerHTML = `
      <td class="mono" data-label="编号">${escapeHtml(item.action_id)}</td>
      <td data-label="任务"><strong>${escapeHtml(item.title)}</strong><small>生成依据：${escapeHtml(item.why)}</small><small>关联证据：${escapeHtml(item.evidence_ids.join(" · "))}</small><small>支持待验证假设：${escapeHtml(item.candidate_causes.join("；"))}</small><small>验收标准：${escapeHtml(item.acceptance_criteria)}</small></td>
      <td data-label="责任角色">${escapeHtml(item.owner_role)}</td>
      <td data-label="时限">${escapeHtml(item.due_minutes)} 分钟</td>
      <td data-label="状态"><span class="task-status ${escapeHtml(presentation.rowClass)}">${escapeHtml(presentation.rowStatus)}</span></td>`;
    body.appendChild(row);
  });
  text("#workflow-status", presentation.workflowStatus);
  $("#workflow-status").classList.toggle("sync-error", presentation.blocked);
  text("#create-task", presentation.buttonText);
  $("#create-task").disabled = presentation.buttonDisabled;
  text("#external-sync-state", presentation.syncLabel);
}

function renderAgentTrace() {
  const container = $("#agent-trace");
  const card = activeCard();
  const trace = card.agent_trace;
  const activeTraceMode = traceMode(card);
  const publicBuild = activeTraceMode === "deterministic_public_build";
  const runtimeAudit = !publicBuild
    && trace.length > 0
    && trace.every((item) => item.call_id && item.status && Number.isFinite(item.duration_ms));
  text("#trace-title", publicBuild ? "公开可复现调用轨迹" : runtimeAudit ? "本次真实工具调用轨迹" : "基线回放步骤");
  text("#trace-description", publicBuild
    ? "步骤与状态来自 deterministic_public_build 资产；为保证跨机器复现，本构建不比较墙钟耗时，0 ms 不代表瞬时完成。"
    : runtimeAudit
      ? "状态和耗时来自运行期审计记录，不是预先写好的流程文案。"
      : "基线卡沿用同一 RiskAnalyzer 口径；以下是资产生成步骤摘要，不冒充运行期工具调用审计。");
  text("#trace-source", publicBuild ? "REPRODUCIBLE BUILD" : runtimeAudit ? "AUDIT TRAIL" : "REPLAY SUMMARY");
  container.replaceChildren();
  trace.forEach((item, index) => {
    const node = document.createElement("li");
    const status = item.status || "recorded";
    const statusLabel = { succeeded: "成功", failed: "失败", recorded: "回放记录" }[status] || status;
    const duration = publicBuild
      ? "公开构建 · 未计时"
      : Number.isFinite(item.duration_ms) && item.duration_ms > 0
        ? `${item.duration_ms.toFixed(3)} ms`
        : "未计时";
    const callId = item.call_id || "BASELINE-REPLAY";
    const statusClass = status === "failed" ? "failed" : status === "succeeded" ? "succeeded" : "recorded";
    node.className = `trace-${statusClass}`;
    node.innerHTML = `
      <span class="trace-index">${escapeHtml(item.sequence || index + 1)}</span>
      <div class="trace-body">
        <div class="trace-title"><strong>${escapeHtml(item.step)} · ${escapeHtml(item.tool)}</strong><span class="trace-status">${escapeHtml(statusLabel)} · ${escapeHtml(duration)}</span></div>
        <p>${escapeHtml(item.result)}</p>
        <code>${escapeHtml(callId)}</code>
      </div>`;
    container.appendChild(node);
  });
}

function renderReasoningGovernance() {
  const card = activeCard();
  const reasoning = card.reasoning || {};
  const provenance = reasoning.provenance || {};
  const workflow = card.workflow || {};
  const mode = provenance.reasoner_mode;
  const modeLabel = mode === "deterministic" ? "确定性" : mode?.startsWith("external:") ? "受控外部模型" : "未运行";

  text("#reasoner-mode", modeLabel);
  text("#prompt-version", reasoning.prompt_version ? `v${reasoning.prompt_version}` : "—");
  text("#schema-version", reasoning.schema_version ? `v${reasoning.schema_version}` : "—");
  const decisionLabel = reasoning.disposition === "no_attribution_required"
    ? "窗口稳定，无需归因"
    : reasoning.disposition === "insufficient_evidence"
      ? "证据不足，拒绝归因"
      : reasoning.decision === "supported"
        ? "证据支持待验证假设"
        : "未执行结构化推理";
  text("#reasoning-decision", decisionLabel);

  const analysisProvenance = card.analysis_provenance || {};
  const shortRevision = (value) => {
    if (!value) return "—";
    const digest = String(value).replace(/^sha256:/, "");
    return digest.length > 16 ? `sha256:${digest.slice(0, 10)}…${digest.slice(-4)}` : String(value);
  };
  text("#risk-policy-version", analysisProvenance.risk_policy_version || "—");
  text("#knowledge-revision", shortRevision(analysisProvenance.knowledge_revision));
  text("#input-revision", shortRevision(analysisProvenance.input_window_revision));
  $("#knowledge-revision").title = analysisProvenance.knowledge_revision || "";
  $("#input-revision").title = analysisProvenance.input_window_revision || "";

  const citations = reasoning.conclusion?.evidence_ids || [];
  const citationContainer = $("#reasoning-citations");
  citationContainer.replaceChildren();
  if (citations.length) {
    citations.forEach((evidenceId) => {
      const item = document.createElement("code");
      item.textContent = evidenceId;
      citationContainer.appendChild(item);
    });
  } else {
    const empty = document.createElement("span");
    empty.textContent = "本场景没有结构化推理引用";
    citationContainer.appendChild(empty);
  }

  const publicBuild = traceMode(card) === "deterministic_public_build";
  if (mode === "deterministic" && !provenance.model) {
    text("#model-boundary", publicBuild
      ? "默认确定性推理器；这是公开可复现构建，本次未调用任何外部模型，轨迹中的 0 ms 仅表示未采集墙钟耗时。"
      : "默认确定性推理器；本次运行未调用任何外部模型。外部模型能力只有在显式配置、Schema 校验和失败回退生效时才可启用。");
  } else if (mode?.startsWith("external:")) {
    text("#model-boundary", `本次审计记录显示调用 ${provenance.model || mode}；输出仍受证据引用、Schema 与人工审批约束。`);
  } else {
    text("#model-boundary", "当前基线回放未运行结构化推理器，也未调用外部模型。");
  }

  const approvalRequired = workflow.human_approval_required ?? reasoning.safety?.requires_human_approval;
  text("#approval-gate", approvalRequired === true ? "必须具名审批" : "无需异常审批");
  text("#workflow-state", workflowStatusLabels[workflow.status] || workflow.status || "未建立工作流");
  text("#allowed-actions", workflow.allowed_actions?.length ? workflow.allowed_actions.join(" / ") : "仅观察");
  text("#automatic-stop-line", workflow.automatic_stop_line_allowed === false ? "禁止" : "未授权");
}

function renderKnowledgeSummary(result) {
  const card = activeCard();
  const stratum = card.analysis_provenance?.analysis_stratum || {};
  const documentEvidence = (card.evidence || [])
    .filter((item) => ["pfmea", "control_plan", "history"].includes(item.category))
    .map((item) => item.title);
  text(
    "#graph-use-description",
    result.attributionRequired
      ? `检索从当前分析分层出发，关系路径与最近 ${result.recentCount} 次时序结果共同写入风险卡；知识只用于形成待验证假设。`
      : `当前窗口未触发归因；仍保留分析分层和 ${result.recentCount} 次窗口口径，知识关系不被用于制造原因假设。`,
  );
  text("#graph-query-start", `${stratum.fastening_point || card.fastening_point} + ${stratum.tool_id || card.tool_id}`);
  text("#graph-document-evidence", documentEvidence.length ? documentEvidence.join("、") : "当前卡片未引用知识文档");
  text(
    "#graph-action-boundary",
    card.workflow?.human_approval_required
      ? "来源可追溯；任何处置需具名人工审批"
      : "稳定监控；无需归因、审批或派单",
  );
}

const graphTypeLabels = {
  Equipment: "设备",
  FasteningPoint: "工艺对象",
  FailureMode: "失效模式",
  Cause: "候选原因",
  Action: "验证动作",
  QualityCharacteristic: "质量特性",
  Role: "责任角色",
  Station: "工位",
};

const graphRelationLabels = {
  has_equipment: "配备",
  executes: "执行",
  controls: "控制",
  may_cause: "可能导致",
  affects: "影响",
  verifies: "验证",
};

function createRelationNode(node, keyNode = false) {
  const element = document.createElement("div");
  element.className = `relation-node${keyNode ? " key-node" : ""}`;

  const type = document.createElement("span");
  type.textContent = graphTypeLabels[node.type] || node.type;
  const name = document.createElement("strong");
  name.textContent = node.name;
  const id = document.createElement("code");
  id.textContent = node.id;

  element.append(type, name, id);
  return element;
}

function createRelationGroup(nodes, keyNode = false) {
  const group = document.createElement("div");
  group.className = "relation-node-group";
  nodes.filter(Boolean).forEach((node) => group.appendChild(createRelationNode(node, keyNode)));
  return group;
}

function createRelationLink(relation) {
  const link = document.createElement("div");
  link.className = "relation-link";

  const label = document.createElement("strong");
  label.textContent = graphRelationLabels[relation] || relation;
  const code = document.createElement("code");
  code.textContent = relation;
  const arrow = document.createElement("span");
  arrow.className = "relation-arrow";
  arrow.setAttribute("aria-hidden", "true");
  arrow.textContent = "→";

  link.append(label, code, arrow);
  return link;
}

function appendRelationLane(container, title, note, segments) {
  const lane = document.createElement("section");
  lane.className = "relation-lane";

  const heading = document.createElement("header");
  const headingTitle = document.createElement("strong");
  headingTitle.textContent = title;
  const headingNote = document.createElement("span");
  headingNote.textContent = note;
  heading.append(headingTitle, headingNote);

  const path = document.createElement("div");
  path.className = "relation-path";
  segments.forEach((segment) => {
    if (segment.relation) {
      path.appendChild(createRelationLink(segment.relation));
    } else {
      path.appendChild(createRelationGroup(segment.nodes, segment.keyNode));
    }
  });

  lane.append(heading, path);
  container.appendChild(lane);
}

function renderGraph() {
  const container = $("#relationship-chain");
  container.replaceChildren();
  if (!state.graph) {
    const empty = document.createElement("p");
    empty.className = "relation-empty error";
    empty.textContent = `知识子图暂不可用：${state.graphError || "未返回可用数据"}。风险卡与时序结果仍可独立查看。`;
    container.appendChild(empty);
    text("#graph-count", "知识子图加载失败 · 已降级为风险卡证据视图");
    return;
  }
  if (!Array.isArray(state.graph.nodes) || !Array.isArray(state.graph.edges)) {
    const empty = document.createElement("p");
    empty.className = "relation-empty error";
    empty.textContent = "知识子图结构非法，已停止生成关系路径；风险卡证据仍可查看。";
    container.appendChild(empty);
    text("#graph-count", "知识子图结构非法 · 已安全降级");
    return;
  }

  const nodesById = new Map(state.graph.nodes.map((node) => [node.id, node]));
  const edgesByRelation = (relation) => state.graph.edges.filter((edge) => edge.relation === relation);
  const firstEdge = (relation) => edgesByRelation(relation)[0];
  const hasEquipment = firstEdge("has_equipment");
  const executes = firstEdge("executes");
  const controls = edgesByRelation("controls");
  const mayCause = edgesByRelation("may_cause");
  const affects = firstEdge("affects");
  const verifies = edgesByRelation("verifies");

  const requiredEdges = [hasEquipment, executes, affects];
  if (requiredEdges.some((edge) => !edge) || !controls.length || !mayCause.length || !verifies.length) {
    const empty = document.createElement("p");
    empty.className = "relation-empty";
    empty.textContent = "当前子图缺少完整关系，已停止生成推理路径。";
    container.appendChild(empty);
    text("#graph-count", "知识子图结构不完整 · 未生成关系路径");
    return;
  }

  appendRelationLane(container, "生产对象", "设备、工艺与质量特性", [
    { nodes: [nodesById.get(hasEquipment.source)] },
    { relation: hasEquipment.relation },
    { nodes: [nodesById.get(hasEquipment.target)] },
    { relation: executes.relation },
    { nodes: [nodesById.get(executes.target)], keyNode: true },
    { relation: controls[0].relation },
    { nodes: controls.map((edge) => nodesById.get(edge.target)), keyNode: true },
  ]);

  appendRelationLane(container, "风险解释", "候选原因、失效模式与质量影响", [
    { nodes: mayCause.map((edge) => nodesById.get(edge.source)) },
    { relation: mayCause[0].relation },
    { nodes: [nodesById.get(mayCause[0].target)], keyNode: true },
    { relation: affects.relation },
    { nodes: [nodesById.get(affects.target)], keyNode: true },
  ]);

  appendRelationLane(container, "现场验证", "工具调用与待确认原因一一对应", [
    { nodes: verifies.map((edge) => nodesById.get(edge.source)), keyNode: true },
    { relation: verifies[0].relation },
    { nodes: verifies.map((edge) => nodesById.get(edge.target)) },
  ]);

  text("#graph-count", `${state.graph.nodes.length} 个节点 · ${state.graph.edges.length} 条关系 · 3 条当前路径`);
}

function renderMetrics() {
  if (!state.metrics) return;
  text("#eval-recall", formatPercent(state.metrics.recall, 1));
  text("#eval-fpr", formatPercent(state.metrics.false_positive_rate, 1));
  text("#eval-trace", formatPercent(state.metrics.evidence_traceability));
  text("#eval-samples", `${state.metrics.samples} 个滚动窗口`);
}

function renderLiveAnalysis(payload) {
  const chip = $("#live-analysis-state");
  const card = payload?.card || null;
  const latest = payload?.latestEvents?.[payload.latestEvents.length - 1] || {};
  if (!chip) return;
  if (!payload?.active || !card) {
    const browserReplay = payload?.source === "browser_static_replay" || liveBackendMode === "static";
    chip.textContent = browserReplay ? "浏览器回放待启动" : payload?.lastError ? "后台连接异常" : "未启动";
    chip.className = `live-state-chip ${payload?.lastError && !browserReplay ? "error" : ""}`;
    text("#live-analysis-note", browserReplay ? "公开页面默认使用浏览器回放，点击开始测试后会推进一批确定性合成数据。" : payload?.lastError || "等待读取临时数据分析。");
    text("#live-analysis-score", "—");
    text("#live-analysis-level", "等待模拟数据");
    text("#live-analysis-scenario", "—");
    text("#live-analysis-sequence", "—");
    text("#live-analysis-torque", "—");
    text("#live-analysis-time", "—");
    updateLiveTestControls(false, Boolean(payload?.lastError && !browserReplay));
    renderLiveTerminal(payload);
    renderAiSnapshot(payload);
    return;
  }
  chip.textContent = payload.running ? "实时运行中" : "已收到一批";
  chip.className = `live-state-chip ${payload.running ? "running" : ""}`;
  text("#live-analysis-note", `浏览器合成流 · ${payload.scenario || "—"} · 第 ${payload.sequence || 0} 批；结果来自 Python RiskAnalyzer。`);
  text("#live-analysis-score", `${card.risk_score ?? "—"}`);
  text("#live-analysis-level", `${levelLabel(card.risk_level)} · ${workflowStatusLabels[card.status] || card.status || "—"}`);
  text("#live-analysis-scenario", payload.scenario || "—");
  text("#live-analysis-sequence", String(payload.sequence || 0));
  text("#live-analysis-torque", latest.torque_nm == null ? "—" : `${latest.torque_nm} N·m`);
  text("#live-analysis-time", card.created_at ? new Date(card.created_at).toLocaleString("zh-CN", { hour12: false }) : "—");
  updateLiveTestControls(Boolean(payload.running), false);
  renderLiveDashboard(payload);
  renderLiveTerminal(payload);
  renderAiSnapshot(payload);
  // Keep the physical workcell replay and the tabular risk view on the same
  // deterministic payload. The replay listens for this event and never
  // receives raw customer or production data.
  window.dispatchEvent(new CustomEvent("qg:live-update", { detail: payload }));
}

function updateLiveTestControls(running, hasError = false) {
  const start = $("#start-live-test");
  const stop = $("#stop-live-test");
  const help = $("#live-test-help");
  if (start) start.disabled = running;
  if (stop) stop.disabled = !running;
  if (!help) return;
  if (hasError) {
    help.textContent = "当前使用浏览器回放，评委无需启动服务；风险卡、曲线和证据摘要仍会完整更新。";
    help.classList.remove("error");
  } else if (running) {
    help.textContent = liveBackendMode === "backend"
      ? "实时测试运行中：页面每 2 秒推进一个确定性合成批次。"
      : "浏览器回放运行中：每 2 秒推进一个确定性合成批次。";
    help.classList.remove("error");
  } else {
    help.textContent = "点击“开始实时测试”后，页面会回放合成批次，风险卡、曲线和证据摘要会同步更新。";
    help.classList.remove("error");
  }
}

function liveEvidence(card, evidenceId) {
  return card?.evidence?.find((item) => item.evidence_id === evidenceId)?.data || {};
}

function animateLivePath(pathElement, values) {
  if (!pathElement || values.length < 2) return;
  const target = pathFor(values, 880, 260, 43, 53);
  const parsePath = (value) => [...String(value || "").matchAll(/[ML](-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)/g)].map((match) => [Number(match[1]), Number(match[2])]);
  const nextPoints = parsePath(target);
  const currentPoints = parsePath(pathElement.getAttribute("d"));
  if (currentPoints.length !== nextPoints.length) {
    pathElement.setAttribute("d", target);
    return;
  }
  window.cancelAnimationFrame(pathElement._liveAnimationFrame || 0);
  const started = performance.now();
  const duration = 420;
  const tick = (now) => {
    const progress = Math.min(1, (now - started) / duration);
    const eased = 1 - ((1 - progress) ** 3);
    const d = nextPoints.map((point, index) => {
      const from = currentPoints[index];
      const x = from[0] + ((point[0] - from[0]) * eased);
      const y = from[1] + ((point[1] - from[1]) * eased);
      return `${index === 0 ? "M" : "L"}${x.toFixed(2)},${y.toFixed(2)}`;
    }).join(" ");
    pathElement.setAttribute("d", d);
    if (progress < 1) pathElement._liveAnimationFrame = window.requestAnimationFrame(tick);
  };
  pathElement._liveAnimationFrame = window.requestAnimationFrame(tick);
}

function renderLiveDashboard(payload) {
  const card = payload.card;
  const series = Array.isArray(payload.history) && payload.history.length
    ? payload.history
    : (Array.isArray(payload.series) ? payload.series : []);
  const spc = liveEvidence(card, "E-SPC-01");
  const equipment = liveEvidence(card, "E-EQP-02");
  const latest = series.at(-1) || payload.latestEvents?.at(-1) || {};
  const riskLevel = card.risk_level || "low";
  const riskChip = $("#risk-chip");
  if (riskChip) {
    riskChip.className = `risk-chip risk-${riskLevel}`;
    riskChip.textContent = levelLabel(riskLevel);
  }
  text("#risk-score", String(card.risk_score ?? "—"));
  text(".breadcrumb", `实时测试 / 第 ${payload.sequence || 0} 批 / ${card.card_id}`);
  text("#main-title", `${riskLevel === "high" ? "实时测试发现高风险信号" : riskLevel === "medium" ? "实时测试发现中风险信号" : "实时测试窗口稳定"}`);
  text("#main-lead", `${payload.scenario || "浏览器合成场景"} · ${card.observed_facts?.at(-1) || card.inference || "RiskAnalyzer 已完成本批分析"}`);
  text("#metric-state", workflowStatusLabels[card.status] || card.status || "实时分析完成");
  text("#metric-state-note", payload.running ? "持续测试中，等待下一批" : "已完成一批实时测试");
  const shift = Number(spc.mean_shift_sigma);
  text("#metric-shift", Number.isFinite(shift) ? `${shift.toFixed(2)}σ` : "—");
  text("#metric-in-spec", Number.isFinite(Number(spc.in_spec_rate)) ? formatPercent(Number(spc.in_spec_rate)) : "—");
  text("#metric-retry", Number.isFinite(Number(equipment.retry_recent)) ? `${Number(equipment.retry_recent).toFixed(3)} 次/循环` : "—");
  text("#metric-baseline-note", `第 ${payload.sequence || 0} 批 · 基线 ${spc.baseline_mean_nm != null ? Number(spc.baseline_mean_nm).toFixed(2) : "—"} N·m`);
  text("#metric-spec-note", "来自当前实时批次的 24 条窗口");
  text("#metric-retry-note", "设备信号同步分析");
  text("#chart-heading", `实时扭矩趋势 · ${card.fastening_point || "P03"}`);
  text("#chart-latest", latest.torque_nm == null ? "—" : `${Number(latest.torque_nm).toFixed(2)} N·m`);
  text("#chart-window", series.length ? `${series[0].timestamp.slice(11, 16)}–${latest.timestamp.slice(11, 16)}` : "—");
  text("#chart-baseline-label", spc.baseline_mean_nm != null ? `实时基线 ${Number(spc.baseline_mean_nm).toFixed(2)} N·m` : "实时基线");
  text("#chart-title", `${card.fastening_point || "P03"} 第 ${payload.sequence || 0} 批扭矩趋势`);
  text("#signal-summary", `实时第 ${payload.sequence || 0} 批：风险 ${card.risk_score ?? "—"} 分，${spc.rule_ids?.length ? `触发 ${spc.rule_ids.join("、")}` : "未触发 SPC 规则"}。`);
  text("#signal-rules", spc.rule_ids?.length ? spc.rule_ids.join("、") : "未触发规则");
  text("#signal-calibration", "来自实时模拟批次");
  text("#signal-product-impact", card.affected_scope?.summary || "合成数据，仅用于演示分析");
  text("#signal-required-evidence", `${card.evidence?.length || 0} 条证据已关联到本批风险卡`);
  if (series.length >= 2) {
    const values = series.map((row) => Number(row.torque_nm));
    if (values.every(Number.isFinite)) {
      animateLivePath($("#torque-line"), values);
      const chart = $(".trend-chart");
      if (chart) {
        chart.classList.remove("live-updating");
        void chart.offsetWidth;
        chart.classList.add("live-updating");
      }
      const baseline = Number(spc.baseline_mean_nm);
      if (Number.isFinite(baseline)) {
        const baselineY = 260 - ((baseline - 43) / 10) * 260;
        $("#baseline-line")?.setAttribute("y1", baselineY);
        $("#baseline-line")?.setAttribute("y2", baselineY);
      }
      const marker = $("#live-latest-marker");
      const latestValue = Number(latest.torque_nm);
      if (marker && Number.isFinite(latestValue)) {
        marker.setAttribute("cx", "880");
        marker.setAttribute("cy", String(260 - ((latestValue - 43) / 10) * 260));
      }
      $("#risk-zone")?.setAttribute("display", "none");
    }
  }
}

function renderLiveTerminal(payload) {
  const output = $("#live-terminal-output");
  const status = $("#live-terminal-status");
  const relations = $("#live-relations-grid");
  if (!output || !status || !relations) return;
  const card = payload?.card;
  if (!card) {
    const browserReplay = payload?.source === "browser_static_replay" || liveBackendMode === "static";
    status.textContent = browserReplay ? "浏览器回放" : payload?.lastError ? "连接异常" : "等待启动";
    status.className = payload?.lastError && !browserReplay ? "error" : "";
    output.textContent = browserReplay ? "公开页面尚未启动回放。点击开始测试后，这里会显示批次、指标变化和证据关系。" : payload?.lastError || "等待实时测试。点击开始测试后，这里会显示批次、指标变化和证据关系。";
    return;
  }
  const series = Array.isArray(payload.series) ? payload.series : (payload.latestEvents || []);
  const recent = series.slice(Math.max(0, series.length - 24));
  const latest = recent.at(-1) || {};
  const spc = liveEvidence(card, "E-SPC-01");
  const equipment = liveEvidence(card, "E-EQP-02");
  const baseline = Number(spc.baseline_mean_nm);
  const torque = Number(latest.torque_nm);
  const torqueError = Number.isFinite(torque) && Number.isFinite(baseline) ? torque - baseline : NaN;
  const ruleText = spc.rule_ids?.length ? spc.rule_ids.join(", ") : "none";
  const lines = [
    `[${new Date().toLocaleTimeString("zh-CN", { hour12: false })}] BATCH #${payload.sequence || 0}  ${payload.scenario || "unknown"}`,
    `events=${series.length}  recent_window=${recent.length}  card=${card.card_id}`,
    `torque  latest=${Number.isFinite(torque) ? torque.toFixed(3) : "—"} N·m  baseline=${Number.isFinite(baseline) ? baseline.toFixed(3) : "—"} N·m  error=${Number.isFinite(torqueError) ? `${torqueError >= 0 ? "+" : ""}${torqueError.toFixed(3)} N·m` : "—"}`,
    `angle   dispersion_ratio=${Number(equipment.angle_std_ratio || 0).toFixed(3)}x  retry=${Number(equipment.retry_recent || 0).toFixed(3)}/cycle  retry_delta=${(Number(equipment.retry_recent || 0) - Number(equipment.retry_baseline || 0)).toFixed(3)}`,
    `signals current_shift=${Number(equipment.current_shift_sigma || 0).toFixed(2)}σ  cycle_shift=${Number(equipment.cycle_time_shift_sigma || 0).toFixed(2)}σ  in_spec=${Number(spc.in_spec_rate || 0).toFixed(3)}`,
    `rules   ${ruleText}  =>  risk=${String(card.risk_level || "unknown").toUpperCase()} ${card.risk_score ?? "—"}/100`,
    `trace   E-SPC-01 → E-EQP-02 → ${card.evidence?.length || 0} evidence items → workflow:${card.status || "unknown"}`,
    "",
    "recent samples:"
  ];
  recent.slice(-6).forEach((event) => {
    lines.push(`  ${String(event.timestamp || "").slice(11, 19)}  torque=${Number(event.torque_nm).toFixed(3)}  angle=${Number(event.angle_deg).toFixed(2)}  retry=${event.retry_count ?? 0}  ${event.result || "—"}`);
  });
  output.textContent = lines.join("\n");
  status.textContent = payload.running ? "运行中" : "已完成一批";
  status.className = payload.running ? "running" : "";
  const relationItems = [
    `原始事件 ${series.length} 条`,
    "→",
    `SPC ${ruleText}`,
    "→",
    `${card.evidence?.length || 0} 条证据`,
    "→",
    `风险卡 ${String(card.risk_level || "").toUpperCase()} ${card.risk_score ?? "—"}`,
  ];
  relations.replaceChildren();
  relationItems.forEach((value) => {
    const item = document.createElement("span");
    item.textContent = value;
    relations.appendChild(item);
  });
}

function renderAiSnapshot(payload) {
  const stateChip = $("#ai-connection-state");
  const summary = $("#ai-live-summary");
  const triggerList = $("#ai-trigger-list");
  if (!stateChip || !summary || !triggerList) return;
  const card = payload?.card;
  if (!card) {
    const browserReplay = payload?.source === "browser_static_replay" || liveBackendMode === "static";
    stateChip.textContent = browserReplay ? "浏览器摘要" : payload?.lastError ? "后台异常" : "等待数据";
    stateChip.className = `ai-connection-chip ${payload?.lastError && !browserReplay ? "error" : ""}`;
    summary.textContent = browserReplay ? "等待开始回放。回答将基于公开风险卡和合成数据。" : payload?.lastError || "等待实时测试启动。";
    triggerList.replaceChildren();
    const item = document.createElement("li");
    item.textContent = "尚无实时批次";
    triggerList.appendChild(item);
    return;
  }
  const reasons = card.analysis_provenance?.trigger_reasons || [];
  const facts = card.observed_facts || [];
  const causes = (card.candidate_causes || []).slice(0, 3).map((item) => item.cause).filter(Boolean);
  stateChip.textContent = payload.running ? "实时跟随" : "已更新";
  stateChip.className = `ai-connection-chip ${payload.running ? "running" : ""}`;
  summary.textContent = [
    `第 ${payload.sequence || 0} 批 · ${String(card.risk_level || "unknown").toUpperCase()} ${card.risk_score ?? "—"}/100`,
    facts.at(-1) || "RiskAnalyzer 已完成本批分析。",
    `评估：${causes.length ? `优先核对 ${causes.join("、")}` : "当前没有足够证据形成候选原因"}。`,
  ].join("\n");
  triggerList.replaceChildren();
  [...reasons, ...(causes.length ? [`候选原因：${causes.join("、")}`] : [])].forEach((reason) => {
    const item = document.createElement("li");
    item.textContent = reason;
    triggerList.appendChild(item);
  });
  if (!reasons.length && !causes.length) {
    const item = document.createElement("li");
    item.textContent = "未触发异常规则，保持监控";
    triggerList.appendChild(item);
  }
}

function appendAiMessage(kind, value) {
  const log = $("#ai-chat-log");
  if (!log) return null;
  const message = document.createElement("div");
  message.className = `ai-message ${kind}`;
  message.textContent = value;
  log.appendChild(message);
  log.scrollTop = log.scrollHeight;
  return message;
}

function localAnalystAnswer(question) {
  const payload = localLiveState.payload;
  const card = payload?.card || activeCard();
  const result = payload?.card
    ? null
    : activeAnalysis();
  const evidence = card?.evidence || [];
  const triggers = card?.analysis_provenance?.trigger_reasons || [];
  const causes = (card?.candidate_causes || []).slice(0, 2).map((item) => item.cause).filter(Boolean);
  const lower = String(question || "").toLowerCase();
  if (lower.includes("下一步") || lower.includes("怎么") || lower.includes("核对")) {
    const action = card?.recommended_actions?.[0];
    return `建议先由质量工程师复核${action?.title || "当前风险卡的首项现场验证"}。依据为 ${action?.evidence_ids?.join("、") || evidence.slice(0, 2).map((item) => item.evidence_id).join("、") || "当前证据"}。系统只生成任务预览，不替代现场判定。`;
  }
  if (lower.includes("原因") || lower.includes("根因")) {
    return `${causes.length ? `当前仅形成候选假设：${causes.join("、")}。` : "当前没有足够证据形成候选原因。"} 候选关联必须通过点检、抽检或批次核对确认，不能直接当作根因。`;
  }
  if (lower.includes("证据") || lower.includes("依据")) {
    return `本批风险卡保留 ${evidence.length} 条证据，其中直接数据、设备信号和受控知识分别记录来源与定位。${triggers.length ? `触发说明：${triggers.join("；")}` : "当前未触发异常规则，保持监控。"}`;
  }
  const score = payload?.card?.risk_score ?? result?.score ?? "—";
  const level = payload?.card?.risk_level ? levelLabel(payload.card.risk_level) : result ? levelLabel(result.level) : "当前风险";
  const observations = (card?.observed_facts || []).slice(0, 3).join("；");
  return `当前${level}，处理排序指数 ${score}/100。${observations || card?.inference || "系统已完成同口径窗口对照"} 所有处置动作仍需具名工程师审批。`;
}

async function simulatorRequest(path) {
  if (liveBackendMode !== "static") {
    try {
      const response = await fetch(`${LIVE_SIMULATOR_API}/${path}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
        signal: AbortSignal.timeout?.(1200),
      });
      if (!response.ok) throw new Error(`后台请求失败（HTTP ${response.status}）`);
      liveBackendMode = "backend";
      return response.json();
    } catch (error) {
      liveBackendMode = "static";
    }
  }

  if (path === "start") {
    localLiveState.running = true;
    localLiveState.sequence += 1;
  } else if (path === "stop") {
    localLiveState.running = false;
  } else if (path === "generate") {
    localLiveState.sequence += 1;
  }
  localLiveState.payload = buildLocalLivePayload(localLiveState.running);
  return localLiveState.payload;
}

async function pollLiveAnalysis() {
  if (liveBackendMode === "static" && localLiveState.payload) {
    renderLiveAnalysis(localLiveState.payload);
    return localLiveState.payload;
  }
  try {
    const response = await fetch(LIVE_ANALYSIS_API, { cache: "no-store", signal: AbortSignal.timeout?.(1200) });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    liveBackendMode = "backend";
    const payload = await response.json();
    renderLiveAnalysis(payload);
    return payload;
  } catch (error) {
    liveBackendMode = "static";
    if (localLiveState.payload) {
      renderLiveAnalysis(localLiveState.payload);
      return localLiveState.payload;
    }
    const payload = { active: false, running: false, source: "browser_static_replay", lastError: "当前使用浏览器回放模式。" };
    localLiveState.payload = payload;
    renderLiveAnalysis(payload);
    return payload;
  }
}

function scheduleLiveAnalysisPoll() {
  if (typeof window === "undefined" || typeof window.setTimeout !== "function") return;
  liveAnalysisTimer = window.setTimeout(async () => {
    if (liveBackendMode === "static" && localLiveState.running) {
      localLiveState.sequence += 1;
      localLiveState.payload = buildLocalLivePayload(true);
    }
    await pollLiveAnalysis();
    scheduleLiveAnalysisPoll();
  }, 2000);
}

const scenarioLabels = {
  hidden_torque_drift: "规格内扭矩漂移",
  sensor_zero_drift: "传感器零漂",
  repeated_alarm: "重复报警",
};

function formatWilson(interval) {
  if (!Array.isArray(interval) || interval.length !== 2) return "—";
  return `${formatPercent(interval[0], 2)}–${formatPercent(interval[1], 2)}`;
}

function renderScenarioMetrics() {
  const metrics = state.scenarioMetrics;
  if (!metrics) return;
  const abnormalScenarios = Object.entries(metrics.scenarios || {}).filter(([key]) => key !== "normal");
  text("#scenario-case-count", `${metrics.cases} 个独立合成案例 · 每场景 ${metrics.cases_per_scenario} 例`);
  text("#scenario-total", String(metrics.cases));
  text("#scenario-coverage-count", String(abnormalScenarios.length));
  text("#scenario-recall", formatPercent(metrics.recall));
  text("#scenario-fpr", formatPercent(metrics.false_positive_rate));
  text("#recall-wilson", `95% Wilson：${formatWilson(metrics.recall_95pct_wilson)}`);
  text("#fpr-wilson", `95% Wilson：${formatWilson(metrics.false_positive_rate_95pct_wilson)}`);

  const scenarioGrid = $("#scenario-grid");
  scenarioGrid.replaceChildren();
  abnormalScenarios.forEach(([key, scenario]) => {
    const card = document.createElement("article");
    const counts = scenario.level_counts || {};
    const levelSummary = [
      counts.high ? `高风险 ${counts.high}` : null,
      counts.medium ? `中风险 ${counts.medium}` : null,
      counts.low ? `低风险 ${counts.low}` : null,
    ].filter(Boolean).join(" · ");
    card.className = "scenario-card";
    card.innerHTML = `
      <header><span>${escapeHtml(scenario.cases)} 个独立案例</span><strong>${escapeHtml(formatPercent(scenario.detection_rate))}</strong></header>
      <h3>${escapeHtml(scenarioLabels[key] || key)}</h3>
      <p>${escapeHtml(levelSummary || "无等级记录")}</p>
      <small>预设首要原因 Top-1：${escapeHtml(scenario.primary_cause_accuracy == null ? "不适用" : formatPercent(scenario.primary_cause_accuracy))}</small>`;
    scenarioGrid.appendChild(card);
  });

  const guardianRecall = metrics.recall;
  const conventionalRecall = metrics.conventional_alarm_recall;
  text("#guardian-recall", formatPercent(guardianRecall, 1));
  text("#traditional-recall", formatPercent(conventionalRecall, 1));
  text("#recall-uplift", `+${((guardianRecall - conventionalRecall) * 100).toFixed(1)} 个百分点`);
  $("#guardian-bar").style.width = formatPercent(guardianRecall);
  $("#traditional-bar").style.width = formatPercent(conventionalRecall);
}

function render() {
  const series = activeSeries();
  const result = activeAnalysis();
  updateHeader(result);
  renderAnalysisOverview();
  renderChart(series, result);
  renderBreakdown(result);
  renderEvidence();
  renderInference();
  renderCauses();
  renderActions();
  renderComparison();
  renderAgentTrace();
  renderReasoningGovernance();
  renderKnowledgeSummary(result);
  renderAdminWorkbench();
  text("#load-state", `权威口径：Python RiskAnalyzer · 前 ${result.baselineCount} 条基线 + 最近 ${result.recentCount} 条窗口`);
  $("#main-content").classList.toggle("baseline-mode", !result.attributionRequired);
  $("#inject-risk").setAttribute("aria-pressed", String(state.mode === "risk"));
  $("#reset-baseline").setAttribute("aria-pressed", String(state.mode === "baseline"));
}

function announceScenario() {
  const result = activeAnalysis();
  text(
    "#scenario-announcement",
    `已切换到${state.mode === "risk" ? "风险识别" : "稳定基线"}场景：${levelLabel(result.level)} ${result.score} 分，${workflowStatusLabels[result.status] || result.status}。`,
  );
}

function installAiWindow() {
  const panel = $("#ai-assistant");
  const handle = $("#ai-drag-handle");
  const dockButton = $("#ai-panel-dock");
  if (!panel || !handle || !dockButton || typeof window === "undefined") return;
  if (panel.dataset.staticPanel === "true") return;
  const storageKey = "torque-guard.ai-window.v1";
  let saved = null;
  try { saved = JSON.parse(window.localStorage.getItem(storageKey) || "null"); } catch { saved = null; }
  const clamp = (value, minimum, maximum) => Math.min(Math.max(value, minimum), maximum);
  const viewportBounds = () => ({
    maxLeft: Math.max(8, window.innerWidth - panel.offsetWidth - 8),
    maxTop: Math.max(72, window.innerHeight - panel.offsetHeight - 8),
  });
  let floatingPosition = {
    left: Number.isFinite(saved?.floatingLeft) ? Number(saved.floatingLeft) : Number(saved?.left),
    top: Number.isFinite(saved?.floatingTop) ? Number(saved.floatingTop) : Number(saved?.top),
  };
  let floatingSize = {
    width: Number.isFinite(saved?.floatingWidth) ? Number(saved.floatingWidth) : Number(saved?.width),
    height: Number.isFinite(saved?.floatingHeight) ? Number(saved.floatingHeight) : Number(saved?.height),
  };
  const save = () => {
    try {
      const rect = panel.getBoundingClientRect();
      window.localStorage.setItem(storageKey, JSON.stringify({
        left: rect.left,
        top: rect.top,
        width: panel.offsetWidth,
        height: panel.offsetHeight,
        docked: panel.classList.contains("is-docked"),
        dockSide: panel.dataset.dockSide || "right",
        floatingLeft: floatingPosition.left,
        floatingTop: floatingPosition.top,
        floatingWidth: floatingSize.width,
        floatingHeight: floatingSize.height,
      }));
    } catch { /* localStorage may be unavailable in private contexts */ }
  };
  const positionFloating = (left, top, persist = true) => {
    panel.classList.remove("is-docked");
    delete panel.dataset.dockSide;
    panel.style.right = "auto";
    if (Number.isFinite(floatingSize.width)) panel.style.width = `${floatingSize.width}px`;
    if (Number.isFinite(floatingSize.height)) panel.style.height = `${floatingSize.height}px`;
    const bounds = viewportBounds();
    const requestedLeft = Number.isFinite(Number(left)) ? Number(left) : 12;
    const requestedTop = Number.isFinite(Number(top)) ? Number(top) : 76;
    floatingPosition = {
      left: clamp(requestedLeft, 8, bounds.maxLeft),
      top: clamp(requestedTop, 72, bounds.maxTop),
    };
    panel.style.left = `${floatingPosition.left}px`;
    panel.style.top = `${floatingPosition.top}px`;
    dockButton.textContent = "收进侧边";
    dockButton.title = "收进侧边";
    dockButton.setAttribute("aria-label", "收进侧边");
    dockButton.setAttribute("aria-expanded", "true");
    if (persist) save();
  };
  const dock = (side = "right") => {
    if (!panel.classList.contains("is-docked")) {
      const rect = panel.getBoundingClientRect();
      floatingPosition = { left: rect.left, top: rect.top };
      floatingSize = { width: rect.width, height: rect.height };
    }
    panel.classList.add("is-docked");
    panel.dataset.dockSide = side === "left" ? "left" : "right";
    panel.style.right = side === "left" ? "auto" : "0px";
    panel.style.left = side === "left" ? "0px" : "auto";
    panel.style.top = `${clamp(panel.offsetTop || floatingPosition.top || 88, 76, Math.max(76, window.innerHeight - 170))}px`;
    dockButton.textContent = "展开";
    dockButton.title = "展开 AI 窗口";
    dockButton.setAttribute("aria-label", "展开 AI 窗口");
    dockButton.setAttribute("aria-expanded", "false");
    save();
  };
  if (Number.isFinite(floatingSize.width)) {
    floatingSize.width = clamp(floatingSize.width, 320, Math.min(560, window.innerWidth - 36));
    panel.style.width = `${floatingSize.width}px`;
  }
  if (Number.isFinite(floatingSize.height)) {
    floatingSize.height = clamp(floatingSize.height, 360, Math.max(360, window.innerHeight - 112));
    panel.style.height = `${floatingSize.height}px`;
  }
  if (saved?.docked) dock(saved.dockSide);
  else if (Number.isFinite(floatingPosition.left) && Number.isFinite(floatingPosition.top)) positionFloating(floatingPosition.left, floatingPosition.top);
  else positionFloating(panel.getBoundingClientRect().left, panel.getBoundingClientRect().top, false);

  let drag = null;
  handle.addEventListener("pointerdown", (event) => {
    if (event.target.closest("button") || panel.classList.contains("is-docked")) return;
    const rect = panel.getBoundingClientRect();
    drag = { startX: event.clientX, startY: event.clientY, left: rect.left, top: rect.top };
    panel.classList.add("is-dragging");
    handle.setPointerCapture(event.pointerId);
  });
  handle.addEventListener("pointermove", (event) => {
    if (!drag) return;
    const left = drag.left + event.clientX - drag.startX;
    const top = drag.top + event.clientY - drag.startY;
    panel.style.right = "auto";
    panel.style.left = `${clamp(left, 8, Math.max(8, window.innerWidth - panel.offsetWidth - 8))}px`;
    panel.style.top = `${clamp(top, 72, Math.max(72, window.innerHeight - panel.offsetHeight - 8))}px`;
    floatingPosition = { left: panel.getBoundingClientRect().left, top: panel.getBoundingClientRect().top };
    if (event.clientX > window.innerWidth - 42) panel.dataset.edgeHint = "right";
    else if (event.clientX < 42) panel.dataset.edgeHint = "left";
    else delete panel.dataset.edgeHint;
  });
  const finishDrag = (event) => {
    if (!drag) return;
    if (event.type === "pointerup" && event.clientX > window.innerWidth - 42) dock("right");
    else if (event.type === "pointerup" && event.clientX < 42) dock("left");
    else save();
    drag = null;
    panel.classList.remove("is-dragging");
    delete panel.dataset.edgeHint;
    if (handle.hasPointerCapture?.(event.pointerId)) handle.releasePointerCapture(event.pointerId);
  };
  handle.addEventListener("pointerup", finishDrag);
  handle.addEventListener("pointercancel", finishDrag);
  dockButton.addEventListener("click", () => {
    if (panel.classList.contains("is-docked")) positionFloating(floatingPosition.left || Math.max(12, window.innerWidth - 444), floatingPosition.top || 88);
    else dock();
  });
  if (typeof ResizeObserver === "function") {
    new ResizeObserver(() => {
      if (panel.classList.contains("is-docked")) return;
      floatingSize = { width: panel.offsetWidth, height: panel.offsetHeight };
      save();
    }).observe(panel);
  }
  window.addEventListener("resize", () => {
    if (panel.classList.contains("is-docked")) return;
    const rect = panel.getBoundingClientRect();
    positionFloating(rect.left, rect.top);
  });
}

function bindInteractions() {
  installAiWindow();
  bindSidebarNavigation();

  $("#admin-open")?.addEventListener("click", () => {
    setAdminDrawerOpen(true);
  });
  $("#admin-close")?.addEventListener("click", () => setAdminDrawerOpen(false));
  $("#admin-drawer-backdrop")?.addEventListener("click", () => setAdminDrawerOpen(false));

  document.addEventListener("keydown", (event) => {
    const drawer = $("#admin-drawer");
    if (!drawer?.classList.contains("open")) return;
    if (event.key === "Escape") {
      event.preventDefault();
      setAdminDrawerOpen(false);
      return;
    }
    if (event.key !== "Tab") return;
    const focusable = adminDrawerFocusableElements();
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  });

  document.querySelectorAll("[data-admin-step]").forEach((button) => {
    button.addEventListener("click", () => selectAdminDemoStep(Number(button.dataset.adminStep)));
  });

  $("#admin-next-step")?.addEventListener("click", () => {
    selectAdminDemoStep(state.adminDemoStep + 1);
  });

  $("#admin-copy-command")?.addEventListener("click", async () => {
    const button = $("#admin-copy-command");
    button.setAttribute("aria-live", "polite");
    try {
      if (!navigator.clipboard?.writeText) throw new Error("Clipboard API unavailable");
      await navigator.clipboard.writeText(reproductionCommands().join("\n"));
      text("#admin-copy-command", "复现命令已复制");
    } catch (error) {
      text("#admin-copy-command", "复制失败，请检查浏览器权限");
      button.title = error instanceof Error ? error.message : "复制失败";
    } finally {
      setTimeout(() => {
        text("#admin-copy-command", "复制复现命令");
        button.title = "";
      }, 2200);
    }
  });

  $("#admin-export-summary")?.addEventListener("click", () => {
    const button = $("#admin-export-summary");
    try {
      const snapshot = buildAdminSnapshot(activeCard(), activeAnalysis(), { mode: state.mode });
      const blob = new Blob([serializeAdminSnapshot(snapshot)], { type: "application/json;charset=utf-8" });
      const objectUrl = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = objectUrl;
      link.download = `torque-guard-admin-${state.mode}-${snapshot.case.cardId.replace(/[^A-Za-z0-9_-]/g, "-")}.json`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(objectUrl);
      text("#admin-export-summary", "当前摘要已导出");
    } catch (error) {
      text("#admin-export-summary", "导出失败");
      button.title = error instanceof Error ? error.message : "导出失败";
    } finally {
      setTimeout(() => {
        text("#admin-export-summary", "导出当前分析摘要");
        button.title = "";
      }, 2200);
    }
  });

  $("#comparison-table-body")?.addEventListener("click", (event) => {
    const button = event.target.closest("[data-metric-id]");
    if (button) selectComparisonMetric(button.dataset.metricId, true);
  });

  $("#comparison-table-body")?.addEventListener("keydown", (event) => {
    if (!["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    const button = event.target.closest("[data-metric-id]");
    if (!button) return;
    const viewModel = comparisonViewModel(activeCard(), state.results[state.mode]);
    const currentIndex = viewModel.rows.findIndex((row) => row.metricId === button.dataset.metricId);
    if (currentIndex < 0) return;
    event.preventDefault();
    const backwards = event.key === "ArrowUp" || event.key === "ArrowLeft";
    const targetIndex = event.key === "Home"
      ? 0
      : event.key === "End"
        ? viewModel.rows.length - 1
        : (currentIndex + (backwards ? -1 : 1) + viewModel.rows.length) % viewModel.rows.length;
    selectComparisonMetric(viewModel.rows[targetIndex].metricId, true);
  });

  $("#inject-risk").addEventListener("click", () => {
    state.mode = "risk";
    state.selectedMetricId = null;
    render();
    announceScenario();
    $("#risk-analysis").scrollIntoView({ behavior: "smooth", block: "start" });
  });

  $("#ai-chat-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const input = form.elements.question;
    const question = input.value.trim();
    if (!question) return;
    const submit = form.querySelector("button[type=submit]");
    input.value = "";
    appendAiMessage("user", question);
    const pending = appendAiMessage("assistant pending", "正在整理当前风险卡…");
    submit.disabled = true;
    try {
      let result;
      if (liveBackendMode === "static") {
        result = { answer: localAnalystAnswer(question), usedExternalApi: false };
      } else {
        try {
          const response = await fetch("http://127.0.0.1:8010/api/ai/chat", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ question }),
            signal: AbortSignal.timeout?.(2200),
          });
          const payload = await response.json().catch(() => ({}));
          if (!response.ok) throw new Error(payload.error || `AI 请求失败（HTTP ${response.status}）`);
          result = payload;
          liveBackendMode = "backend";
        } catch (error) {
          liveBackendMode = "static";
          result = { answer: localAnalystAnswer(question), usedExternalApi: false, externalError: error instanceof Error ? error.message : "外部服务未连接" };
        }
      }
      if (pending) {
        pending.className = "ai-message assistant";
        pending.textContent = result.answer || "后台未返回回答。";
      }
      const stateChip = $("#ai-connection-state");
      if (stateChip) {
        stateChip.textContent = result.usedExternalApi ? "外部 API 已回答" : "浏览器规则摘要";
        stateChip.className = `ai-connection-chip ${result.usedExternalApi ? "external" : ""}`;
      }
      const note = $("#ai-side-note");
      if (note) note.textContent = result.externalError && !result.usedExternalApi
        ? `外部服务不可用，已用浏览器内的确定性规则摘要回答。`
        : result.usedExternalApi ? "回答来自后台连接的外部 API，实时风险上下文已随问题发送。" : "回答来自当前风险卡的确定性规则摘要。";
    } catch (error) {
      if (pending) {
        pending.className = "ai-message assistant";
        pending.textContent = error instanceof Error ? error.message : "AI 请求失败";
      }
    } finally {
      submit.disabled = false;
      input.focus();
    }
  });

  $("#start-live-test")?.addEventListener("click", async () => {
    const button = $("#start-live-test");
    button.disabled = true;
    text("#live-test-help", "正在启动实时测试并生成第一批数据…");
    try {
      await simulatorRequest("start");
      await pollLiveAnalysis();
      $("#live-analysis")?.scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (error) {
      updateLiveTestControls(false, true);
      text("#live-test-help", error instanceof Error ? error.message : "后台启动失败");
      $("#live-test-help")?.classList.add("error");
    } finally {
      button.disabled = Boolean($("#live-analysis-state")?.classList.contains("running"));
    }
  });

  $("#stop-live-test")?.addEventListener("click", async () => {
    const button = $("#stop-live-test");
    button.disabled = true;
    try {
      await simulatorRequest("stop");
      await pollLiveAnalysis();
    } catch (error) {
      updateLiveTestControls(true, true);
      text("#live-test-help", error instanceof Error ? error.message : "后台停止失败");
      $("#live-test-help")?.classList.add("error");
    }
  });

  $("#reset-baseline").addEventListener("click", () => {
    state.mode = "baseline";
    state.selectedMetricId = null;
    render();
    announceScenario();
  });

  $("#create-task").addEventListener("click", () => {
    const result = activeAnalysis();
    if (!result.attributionRequired
      || result.externalTasksConfirmed
      || ["partial", "failed"].includes(result.externalSyncStatus)) return;
    state.previewPrepared[state.mode] = true;
    renderActions();
    $("#workflow-status").focus();
  });

  $("#copy-card").addEventListener("click", async () => {
    const copyButton = $("#copy-card");
    copyButton.setAttribute("aria-live", "polite");
    try {
      if (!navigator.clipboard?.writeText) throw new Error("Clipboard API unavailable");
      await navigator.clipboard.writeText(JSON.stringify(activeCard(), null, 2));
      text("#copy-card", "风险卡 JSON 已复制");
    } catch (error) {
      text("#copy-card", "复制失败，请检查浏览器权限");
      copyButton.title = error instanceof Error ? error.message : "复制失败";
    } finally {
      setTimeout(() => {
        text("#copy-card", "复制风险卡 JSON");
        copyButton.title = "";
      }, 2200);
    }
  });
}

async function optionalJson(name) {
  try {
    const response = await fetch(`./data/${name}`);
    if (!response.ok) return { data: null, error: `HTTP ${response.status}` };
    return { data: await response.json(), error: null };
  } catch (error) {
    return { data: null, error: error instanceof Error ? error.message : "未知加载错误" };
  }
}

async function load() {
  try {
    const requiredNames = [
      "demo_series.json",
      "baseline_series.json",
      "risk_card.json",
      "baseline_card.json",
      "risk_result.json",
      "baseline_result.json",
      "scenario_metrics.json",
    ];
    const [responses, optionalAssets] = await Promise.all([
      Promise.all(requiredNames.map((name) => fetch(`./data/${name}`))),
      Promise.all([optionalJson("metrics.json"), optionalJson("subgraph.json")]),
    ]);
    responses.forEach((response, index) => {
      if (!response.ok) throw new Error(`${requiredNames[index]} 加载失败（HTTP ${response.status}）`);
    });
    const [riskSeries, baselineSeries, riskCard, baselineCard, riskResult, baselineResult, scenarioMetrics] = await Promise.all(
      responses.map((response) => response.json()),
    );
    state.series = { risk: riskSeries, baseline: baselineSeries };
    state.cards = { risk: riskCard, baseline: baselineCard };
    state.results = { risk: riskResult, baseline: baselineResult };
    state.scenarioMetrics = scenarioMetrics;
    state.metrics = optionalAssets[0].data;
    state.graph = optionalAssets[1].data;
    state.graphError = optionalAssets[1].error;

    // Validate both scenarios before rendering either one. A stale generated
    // asset must never silently produce a mixed Python/JavaScript conclusion.
    authoritativeAnalysis(state.cards.risk, state.results.risk);
    authoritativeAnalysis(state.cards.baseline, state.results.baseline);
    try {
      await loadRelationCases();
      bindRelationCaseInteractions();
    } catch (relationError) {
      text("#relation-case-status", relationError instanceof Error ? relationError.message : "关系案卷读取失败");
      text("#relation-case-boundary", "关系案卷暂不可用，主风险分析仍可复现；请检查公开数据资产。");
    }
    installAdminWorkbench();
    renderGraph();
    renderMetrics();
    renderScenarioMetrics();
    bindInteractions();
    render();
    await pollLiveAnalysis();
    scheduleLiveAnalysisPoll();
    selectAdminDemoStep(0, false);
    document.documentElement.classList.add("ready");
    if (window.location.hash) {
      requestAnimationFrame(() => {
        document.querySelector(window.location.hash)?.scrollIntoView({ block: "start" });
      });
    }
  } catch (error) {
    text("#load-state", `演示数据加载失败：${error.message}`);
    $("#load-state").classList.add("error");
  }
}

if (typeof document !== "undefined") load();
