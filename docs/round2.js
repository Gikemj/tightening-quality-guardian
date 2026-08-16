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

function evidenceItem(item) {
  return `<li><strong>${escapeHtml(item.label)}</strong><span>${escapeHtml(item.detail)}</span><small>${escapeHtml(strengthLabel[item.strength])} · ${escapeHtml(item.evidence_id)}</small></li>`;
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
  window.round2Demo = {
    selectCase,
    generateTaskPreview,
    getModelPayload: getCompactPayload,
    getDossierText: dossierText,
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
  } catch (error) {
    $("#case-title").textContent = "公开演示资产加载失败";
    $("#case-meta").textContent = error instanceof Error ? error.message : "未知错误";
    $("#case-feedback").textContent = "页面未写入任何外部系统，可点击“重试读取”。";
    $("#case-feedback").className = "case-feedback error";
    $("#retry-data").hidden = false;
  }
}

init();
