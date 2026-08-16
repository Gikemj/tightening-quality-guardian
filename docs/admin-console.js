const ADMIN_SCHEMA_VERSION = "1.0";
const SHA256_REVISION = /^sha256:[0-9a-f]{64}$/;

export const ADMIN_BOUNDARY_NOTICE =
  "本摘要由公开演示页面在浏览器本地生成，仅用于只读审计与复现；不授予公司生产权限，也不代表已向飞书或其他外部系统写入数据。";

const asArray = (value) => (Array.isArray(value) ? value : []);

function uniqueStrings(values) {
  return [...new Set(values.filter((value) => typeof value === "string" && value.trim()).map((value) => value.trim()))];
}

function evidenceReferences(card) {
  const references = [];
  const append = (values) => references.push(...asArray(values));

  asArray(card.candidate_causes).forEach((cause) => append(cause?.evidence_ids));
  asArray(card.recommended_actions).forEach((action) => append(action?.evidence_ids));
  append(card.reasoning?.conclusion?.evidence_ids);
  asArray(card.reasoning?.hypotheses).forEach((hypothesis) => append(hypothesis?.evidence_ids));
  asArray(card.workflow?.events).forEach((event) => append(event?.evidence_ids));
  return uniqueStrings(references);
}

function revisionHealth(card) {
  const provenance = card.analysis_provenance || {};
  const revisionFields = [
    "card_identity_revision",
    "input_window_revision",
    "knowledge_revision",
  ];
  const missing = revisionFields.filter((field) => !SHA256_REVISION.test(provenance[field] || ""));
  if (typeof provenance.risk_policy_version !== "string" || !provenance.risk_policy_version.trim()) {
    missing.push("risk_policy_version");
  }
  return { status: missing.length ? "block" : "pass", missing };
}

function metricHealth(card) {
  const availability = card.analysis_provenance?.metric_availability;
  if (!availability || typeof availability !== "object" || Array.isArray(availability)) {
    return { status: "block", available: 0, total: 0, unavailable: ["metric_availability"] };
  }
  const entries = Object.entries(availability);
  const unavailable = entries
    .filter(([, value]) => value?.available !== true)
    .map(([name]) => name);
  return {
    status: unavailable.length ? "warning" : "pass",
    available: entries.length - unavailable.length,
    total: entries.length,
    unavailable,
  };
}

function traceHealth(card) {
  const trace = asArray(card.agent_trace);
  const failed = trace.filter((item) => item?.status !== "succeeded").map((item) => item?.call_id || "unknown");
  if (!trace.length) return { status: "warning", total: 0, failed: [] };
  return { status: failed.length ? "block" : "pass", total: trace.length, failed };
}

function syncSummary(card, analysis) {
  const persisted = card.workflow?.external_sync || null;
  const status = persisted?.sync_status || analysis.externalSyncStatus || "not_attempted";
  const reconciliationRequired = persisted?.reconciliation_required === true;
  return {
    status,
    persisted: Boolean(persisted),
    mode: persisted?.mode || "public_preview",
    failureStage: persisted?.failure_stage || null,
    reconciliationRequired,
    automaticRetrySafe: persisted?.automatic_retry_safe === true,
    remoteRiskRecordCount: asArray(persisted?.remote_ids?.risk).length,
    remoteTaskRecordCount: asArray(persisted?.remote_ids?.tasks).length,
    networkWriteAvailableOnThisPage: false,
    pageClaim: persisted
      ? "仅展示风险卡中已持久化的同步回执；本页面不会发起外部写入"
      : "公开预览没有外部同步回执，本页面也不会发起外部写入",
  };
}

function approvalSummary(card, analysis, sync) {
  const workflow = card.workflow || {};
  const required = workflow.human_approval_required === true;
  const allowedActions = uniqueStrings(asArray(workflow.allowed_actions));
  const blockedBySync = ["partial", "failed"].includes(sync.status) || sync.reconciliationRequired;
  const automaticStopDisabled = workflow.automatic_stop_line_allowed === false;

  let gateStatus = "not_required";
  if (blockedBySync) gateStatus = "blocked_for_reconciliation";
  else if (required && card.status === "awaiting_engineer_review") gateStatus = "awaiting_named_approval";
  else if (required) gateStatus = "approval_recorded_or_in_progress";

  return {
    required,
    gateStatus,
    workflowStatus: card.status,
    allowedActions,
    automaticStopDisabled,
    localTaskPreviewAvailable:
      analysis.attributionRequired === true
      && asArray(card.recommended_actions).length > 0
      && !blockedBySync,
    externalWriteAllowedOnThisPage: false,
  };
}

export function reproductionCommands() {
  return [
    "python scripts/build_all.py",
    "python -m unittest discover -s tests -v",
    "node --test tests/web-engine.test.mjs tests/admin-console.test.mjs",
  ];
}

export function buildAdminSnapshot(card, analysis, options = {}) {
  if (!card || typeof card !== "object" || !analysis || typeof analysis !== "object") {
    throw new TypeError("管理员摘要需要已校验的风险卡和权威分析结果");
  }
  const generatedAt = options.generatedAt || new Date().toISOString();
  if (Number.isNaN(Date.parse(generatedAt))) throw new TypeError("generatedAt 必须是有效时间");

  const evidenceIds = uniqueStrings(asArray(card.evidence).map((item) => item?.evidence_id));
  const referencedIds = evidenceReferences(card);
  const knownEvidence = new Set(evidenceIds);
  const missingReferenceIds = referencedIds.filter((evidenceId) => !knownEvidence.has(evidenceId));
  const attributionRequired = analysis.attributionRequired === true;
  const evidenceStatus = missingReferenceIds.length || (attributionRequired && !referencedIds.length)
    ? "block"
    : "pass";
  const revision = revisionHealth(card);
  const metrics = metricHealth(card);
  const trace = traceHealth(card);
  const sync = syncSummary(card, analysis);
  const approval = approvalSummary(card, analysis, sync);

  const dataChecks = [
    {
      id: "authoritative_contract",
      label: "风险卡与分析结果合同",
      status: "pass",
      detail: "已通过浏览器端权威合同校验",
    },
    {
      id: "provenance_revisions",
      label: "规则、知识与输入修订",
      status: revision.status,
      detail: revision.missing.length ? `缺失或非法：${revision.missing.join("、")}` : "SHA-256 修订信息完整",
    },
    {
      id: "metric_availability",
      label: "可选指标可用性",
      status: metrics.status,
      detail: metrics.unavailable.length
        ? `不可用：${metrics.unavailable.join("、")}`
        : `${metrics.available}/${metrics.total} 项可用`,
    },
    {
      id: "agent_trace",
      label: "分析调用轨迹",
      status: trace.status,
      detail: trace.failed.length ? `异常调用：${trace.failed.join("、")}` : `${trace.total} 条轨迹可审计`,
    },
  ];

  const blockingReasons = [];
  if (dataChecks.some((check) => check.status === "block")) blockingReasons.push("数据健康检查未通过");
  if (evidenceStatus === "block") blockingReasons.push("证据引用不完整");
  if (!approval.automaticStopDisabled) blockingReasons.push("自动停线边界未明确禁止");
  if (["partial", "failed"].includes(sync.status) || sync.reconciliationRequired) {
    blockingReasons.push("外部同步状态需要人工对账");
  }
  const hasWarnings = dataChecks.some((check) => check.status === "warning");
  const overallStatus = blockingReasons.length
    ? "blocked"
    : hasWarnings
      ? "warning"
      : approval.gateStatus === "awaiting_named_approval"
        ? "attention"
        : "healthy";

  return {
    schemaVersion: ADMIN_SCHEMA_VERSION,
    generatedAt,
    boundary: ADMIN_BOUNDARY_NOTICE,
    overallStatus,
    blockingReasons,
    mode: options.mode || "risk",
    case: {
      cardId: card.card_id,
      stationId: card.station_id,
      toolId: card.tool_id,
      fasteningPoint: card.fastening_point,
      riskLevel: analysis.level,
      riskScore: analysis.score,
      workflowStatus: card.status,
      analysisDisposition: analysis.analysisDisposition,
      baselineCount: analysis.baselineCount,
      recentCount: analysis.recentCount,
    },
    dataHealth: {
      status: dataChecks.some((check) => check.status === "block")
        ? "block"
        : hasWarnings ? "warning" : "pass",
      checks: dataChecks,
    },
    evidence: {
      status: evidenceStatus,
      requirement: attributionRequired ? "required" : "not_required",
      storedCount: evidenceIds.length,
      referencedCount: referencedIds.length,
      referencedIds,
      missingReferenceIds,
      unusedEvidenceIds: evidenceIds.filter((evidenceId) => !referencedIds.includes(evidenceId)),
      referenceCompleteness: referencedIds.length
        ? (referencedIds.length - missingReferenceIds.length) / referencedIds.length
        : 1,
    },
    approval,
    sync,
    recommendedActions: asArray(card.recommended_actions).map((action) => ({
      actionId: action.action_id,
      title: action.title,
      ownerRole: action.owner_role,
      dueMinutes: action.due_minutes,
      approvalRequired: action.approval_required === true,
      why: action.why,
      evidenceIds: [...asArray(action.evidence_ids)],
      candidateCauses: [...asArray(action.candidate_causes)],
    })),
    reproduction: {
      execution: "copy_only",
      commands: reproductionCommands(),
    },
  };
}

export function serializeAdminSnapshot(snapshot) {
  return `${JSON.stringify(snapshot, null, 2)}\n`;
}
