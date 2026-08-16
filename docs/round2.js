const $ = (selector) => document.querySelector(selector);

const escapeHtml = (value) => String(value)
  .replaceAll("&", "&amp;")
  .replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;")
  .replaceAll("'", "&#039;");

const strengthLabel = { direct: "直接事实", candidate: "关联候选", gap: "信息缺口" };

function evidenceItem(item) {
  return `<li><strong>${escapeHtml(item.label)}</strong><span>${escapeHtml(item.detail)}</span><small>${escapeHtml(strengthLabel[item.strength])} · ${escapeHtml(item.evidence_id)}</small></li>`;
}

function renderCase(report) {
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
}

function connectInteractions() {
  $("#model-toggle").addEventListener("click", () => {
    const preview = $("#model-preview");
    const expanded = preview.hidden;
    preview.hidden = !expanded;
    $("#model-toggle").setAttribute("aria-expanded", String(expanded));
    $("#model-toggle").textContent = expanded ? "收起模型输入边界" : "查看模型输入边界";
  });
  $("#task-preview").addEventListener("click", () => {
    const feedback = $("#task-feedback");
    feedback.textContent = "已生成本地预览。公开演示没有发起飞书写入，正式写入仍需具名审批与授权租户。";
    feedback.className = "success";
  });
}

async function init() {
  try {
    const response = await fetch("./data/round2_reports.json", { cache: "no-store" });
    if (!response.ok) throw new Error(`无法读取公开演示资产：${response.status}`);
    const payload = await response.json();
    if (payload.schema_version !== "2.0" || payload.data_boundary !== "schema_and_relationship_reference_only" || !Array.isArray(payload.reports)) throw new Error("公开演示资产不符合第二轮数据边界合同");
    const select = $("#case-select");
    payload.reports.forEach((report, index) => {
      const option = document.createElement("option");
      option.value = String(index);
      option.textContent = `${report.case.case_id} · ${report.case.title}`;
      select.append(option);
    });
    const show = () => renderCase(payload.reports[Number(select.value)]);
    select.addEventListener("change", show);
    show();
    connectInteractions();
  } catch (error) {
    $("#case-title").textContent = "公开演示资产加载失败";
    $("#case-meta").textContent = error instanceof Error ? error.message : "未知错误";
  }
}

init();
