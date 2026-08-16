const SCORE_KEYS = ["process_stability", "equipment_health", "quality_impact", "context"];
const SCORE_LIMITS = {
  process_stability: 35,
  equipment_health: 25,
  quality_impact: 25,
  context: 15,
};
const CARD_ID_PATTERN = /^TG-[0-9A-F]{32}$/;
const SHA256_PATTERN = /^sha256:[0-9a-f]{64}$/;
const AVAILABILITY_KEYS = [
  "available",
  "baseline_sample_count",
  "baseline_required_count",
  "recent_sample_count",
  "recent_required_count",
  "reason",
];
const STATUS_ACTIONS = {
  monitoring_only: [],
  awaiting_engineer_review: ["approve", "reject"],
  approved: ["create_tasks"],
  rejected: ["resubmit"],
  tasks_created: ["start_verification"],
  verification_in_progress: ["pass_verification", "fail_verification"],
  verified: ["close"],
  closed: ["reopen"],
};
const SYNC_STATUSES = new Set(["not_attempted", "partial", "succeeded", "failed"]);
const POST_CREATION_STATUSES = new Set(["tasks_created", "verification_in_progress", "verified", "closed"]);
const COMPARISON_METRIC_IDS = [
  "torque_mean_nm",
  "angle_dispersion_ratio",
  "retry_mean",
  "in_spec_rate",
];
const COMPARISON_KEYS = [
  "metric_id",
  "label",
  "baseline",
  "current",
  "delta",
  "unit",
  "status",
  "rule_ids",
  "evidence_ids",
];
const COMPARISON_UNITS = {
  torque_mean_nm: "N·m",
  angle_dispersion_ratio: "倍",
  retry_mean: "次/循环",
  in_spec_rate: "%",
};

function requireObject(value, field) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`${field} 必须是对象`);
  }
  return value;
}

function requireFiniteNumber(value, field) {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new Error(`权威分析结果缺少有效数值：${field}`);
  }
  return value;
}

function requireInteger(value, field, minimum = 0) {
  if (!Number.isInteger(value) || value < minimum) {
    throw new Error(`${field} 必须是不小于 ${minimum} 的整数`);
  }
  return value;
}

function requireNonEmptyString(value, field) {
  if (typeof value !== "string" || !value.trim()) {
    throw new Error(`${field} 必须是非空字符串`);
  }
  return value;
}

function requireStringArray(value, field, { allowEmpty = true } = {}) {
  if (!Array.isArray(value) || (!allowEmpty && value.length === 0)) {
    throw new Error(`${field} 必须是${allowEmpty ? "" : "非空"}字符串列表`);
  }
  if (value.some((item) => typeof item !== "string" || !item.trim())) {
    throw new Error(`${field} 包含无效字符串`);
  }
  if (new Set(value).size !== value.length) {
    throw new Error(`${field} 不得包含重复值`);
  }
  return value;
}

function requireExactKeys(value, expected, field) {
  const actual = Object.keys(requireObject(value, field)).sort();
  const wanted = [...expected].sort();
  if (actual.length !== wanted.length || actual.some((key, index) => key !== wanted[index])) {
    throw new Error(`${field} 结构不完整`);
  }
}

function sameStringArray(left, right) {
  return Array.isArray(left)
    && left.length === right.length
    && left.every((value, index) => value === right[index]);
}

function validateMetricAvailability(provenance) {
  const availability = provenance.metric_availability;
  requireExactKeys(availability, ["current_a", "cycle_time_s"], "analysis_provenance.metric_availability");

  for (const metric of ["current_a", "cycle_time_s"]) {
    const field = `analysis_provenance.metric_availability.${metric}`;
    const record = availability[metric];
    requireExactKeys(record, AVAILABILITY_KEYS, field);
    if (typeof record.available !== "boolean") throw new Error(`${field}.available 必须是布尔值`);

    for (const key of AVAILABILITY_KEYS.filter((key) => key.endsWith("_count"))) {
      requireInteger(record[key], `${field}.${key}`);
    }
    if (record.baseline_required_count !== provenance.baseline_count) {
      throw new Error(`${field}.baseline_required_count 与分析窗口不一致`);
    }
    if (record.recent_required_count !== provenance.recent_count) {
      throw new Error(`${field}.recent_required_count 与分析窗口不一致`);
    }
    if (record.baseline_sample_count > record.baseline_required_count
      || record.recent_sample_count > record.recent_required_count) {
      throw new Error(`${field} 的样本数量超过需求数量`);
    }

    const complete = record.baseline_sample_count === record.baseline_required_count
      && record.recent_sample_count === record.recent_required_count;
    if (record.available !== complete) throw new Error(`${field}.available 与样本数量不一致`);
    if (complete && record.reason !== null) throw new Error(`${field} 可用时 reason 必须为 null`);
    if (!complete && (typeof record.reason !== "string" || !record.reason.trim())) {
      throw new Error(`${field} 不可用时必须给出原因`);
    }
  }
}

function validateExternalSync(card, workflow) {
  const sync = workflow.external_sync;
  if (sync === undefined) return null;
  requireObject(sync, "workflow.external_sync");
  const requiredKeys = [
    "schema_version",
    "mode",
    "card_id",
    "sync_status",
    "failure_stage",
    "request_ids",
    "remote_ids",
    "reconciliation_required",
    "automatic_retry_safe",
  ];
  const optionalKeys = ["workflow_status", "workflow_committed", "external_write_status", "error"];
  const actualKeys = Object.keys(sync);
  if (requiredKeys.some((key) => !Object.hasOwn(sync, key))
    || actualKeys.some((key) => !requiredKeys.includes(key) && !optionalKeys.includes(key))) {
    throw new Error("workflow.external_sync 字段不完整或包含未知字段");
  }
  if (sync.schema_version !== "1.0") throw new Error("workflow.external_sync schema_version 不受支持");
  if (sync.mode !== "live") throw new Error("workflow.external_sync.mode 必须为 live");
  if (sync.card_id !== card.card_id) throw new Error("workflow.external_sync.card_id 与风险卡不一致");
  if (!SYNC_STATUSES.has(sync.sync_status)) throw new Error("workflow.external_sync.sync_status 非法");
  if (sync.failure_stage !== null && (typeof sync.failure_stage !== "string" || !sync.failure_stage.trim())) {
    throw new Error("workflow.external_sync.failure_stage 必须为 null 或非空字符串");
  }
  if (typeof sync.failure_stage === "string" && !/^[a-z][a-z0-9_]{0,63}$/.test(sync.failure_stage)) {
    throw new Error("workflow.external_sync.failure_stage 格式非法");
  }
  const requestIds = requireObject(sync.request_ids, "workflow.external_sync.request_ids");
  const requestValues = [];
  for (const [stage, requestId] of Object.entries(requestIds)) {
    if (!/^[a-z][a-z0-9_]{0,63}$/.test(stage)) throw new Error("workflow.external_sync.request_ids 阶段名非法");
    requestValues.push(requireNonEmptyString(requestId, "workflow.external_sync.request_id"));
  }
  if (new Set(requestValues).size !== requestValues.length) {
    throw new Error("workflow.external_sync.request_id 不得重复");
  }
  const remoteIds = requireObject(sync.remote_ids, "workflow.external_sync.remote_ids");
  requireExactKeys(remoteIds, ["risk", "tasks"], "workflow.external_sync.remote_ids");
  const remoteRiskIds = requireStringArray(remoteIds.risk, "workflow.external_sync.remote_ids.risk");
  const remoteTaskIds = requireStringArray(remoteIds.tasks, "workflow.external_sync.remote_ids.tasks");
  if (remoteRiskIds.length > 1 || remoteTaskIds.length > card.recommended_actions.length) {
    throw new Error("workflow.external_sync.remote_ids 数量超出请求范围");
  }
  if (typeof sync.reconciliation_required !== "boolean") {
    throw new Error("workflow.external_sync.reconciliation_required 必须是布尔值");
  }
  if (typeof sync.automatic_retry_safe !== "boolean") {
    throw new Error("workflow.external_sync.automatic_retry_safe 必须是布尔值");
  }
  if (Object.hasOwn(sync, "workflow_committed") && typeof sync.workflow_committed !== "boolean") {
    throw new Error("workflow.external_sync.workflow_committed 必须是布尔值");
  }
  if (Object.hasOwn(sync, "workflow_status")) requireNonEmptyString(sync.workflow_status, "workflow.external_sync.workflow_status");
  if (Object.hasOwn(sync, "external_write_status")
    && !new Set(["not_attempted", "unverified", "partial", "succeeded", "failed"]).has(sync.external_write_status)) {
    throw new Error("workflow.external_sync.external_write_status 非法");
  }
  if (Object.hasOwn(sync, "error")) {
    requireExactKeys(sync.error, ["type", "message"], "workflow.external_sync.error");
    requireNonEmptyString(sync.error.type, "workflow.external_sync.error.type");
    requireNonEmptyString(sync.error.message, "workflow.external_sync.error.message");
  }

  if (sync.sync_status === "succeeded") {
    if (!POST_CREATION_STATUSES.has(workflow.status)
      || sync.workflow_committed !== true
      || sync.workflow_status !== "tasks_created"
      || sync.external_write_status !== "succeeded"
      || sync.reconciliation_required !== false
      || sync.automatic_retry_safe !== false
      || sync.failure_stage !== null
      || Object.hasOwn(sync, "error")
      || !Object.hasOwn(requestIds, "risk_create")
      || !Object.hasOwn(requestIds, "task_create")
      || remoteRiskIds.length !== 1
      || remoteTaskIds.length !== card.recommended_actions.length) {
      throw new Error("succeeded 外部同步缺少可核验的远端记录或本地工作流提交");
    }
    return sync;
  }

  if (sync.failure_stage === null) throw new Error("未成功 external_sync 必须记录 failure_stage");
  if (POST_CREATION_STATUSES.has(workflow.status)) {
    throw new Error("未成功 external_sync 不得伪称 tasks_created");
  }
  if (sync.workflow_committed === true) throw new Error("未成功 external_sync 不得标记 workflow_committed=true");
  if (POST_CREATION_STATUSES.has(sync.workflow_status)) {
    throw new Error("未成功 external_sync 不得记录已创建任务状态");
  }
  if (sync.sync_status === "partial") {
    if (sync.reconciliation_required !== true) throw new Error("partial 外部同步必须要求人工对账");
    if (!Object.keys(requestIds).length) throw new Error("partial 外部同步必须保留 request_id");
  }
  if (sync.sync_status === "failed" && (remoteRiskIds.length || remoteTaskIds.length)) {
    throw new Error("failed 外部同步不得包含已确认远端 ID");
  }
  if (sync.sync_status === "not_attempted" && (remoteRiskIds.length || remoteTaskIds.length)) {
    throw new Error("not_attempted 外部同步不得包含远端 ID");
  }
  if (sync.automatic_retry_safe
    && (!["not_attempted", "failed"].includes(sync.sync_status)
      || remoteRiskIds.length
      || remoteTaskIds.length)) {
    throw new Error("仅确认未写入远端的失败才可标记 automatic_retry_safe");
  }
  if (sync.external_write_status === "succeeded" && sync.sync_status !== "partial") {
    throw new Error("仅本地提交失败的 partial 可记录外部写入 succeeded");
  }
  return sync;
}

function validateWorkflow(card, attributionRequired) {
  const workflow = requireObject(card.workflow, "workflow");
  if (workflow.schema_version !== "1.0") throw new Error("workflow.schema_version 不受支持");
  if (workflow.case_id !== card.card_id) throw new Error("workflow.case_id 与 card_id 不一致");
  if (workflow.status !== card.status) throw new Error("workflow.status 与风险卡 status 不一致");
  if (workflow.automatic_stop_line_allowed !== false) throw new Error("workflow 不得允许自动停线");
  if (typeof workflow.human_approval_required !== "boolean") {
    throw new Error("workflow.human_approval_required 必须是布尔值");
  }
  const expectedActions = STATUS_ACTIONS[card.status];
  if (!sameStringArray(workflow.allowed_actions, expectedActions)) {
    throw new Error("workflow.allowed_actions 与当前 status 不一致");
  }
  if (!Array.isArray(workflow.events)) throw new Error("workflow.events 必须是列表");

  if (!attributionRequired) {
    if (workflow.human_approval_required !== false || workflow.allowed_actions.length || workflow.events.length) {
      throw new Error("稳定监控 workflow 不得包含异常审批、动作或事件");
    }
    if (workflow.external_sync !== undefined) throw new Error("稳定监控 workflow 不得包含 external_sync");
    return null;
  }
  if (workflow.human_approval_required !== true) throw new Error("异常处置 workflow 必须保留人工审批");
  return validateExternalSync(card, workflow);
}

function validateDecisionLinks(card) {
  if (!Array.isArray(card.evidence)) throw new Error("风险卡 evidence 必须是列表");
  const evidenceIds = card.evidence.map((item, index) => {
    requireObject(item, `evidence[${index}]`);
    return requireNonEmptyString(item.evidence_id, `evidence[${index}].evidence_id`);
  });
  if (new Set(evidenceIds).size !== evidenceIds.length) throw new Error("evidence_id 不得重复");
  const knownEvidence = new Set(evidenceIds);

  const causeNames = card.candidate_causes.map((cause, index) => {
    requireObject(cause, `candidate_causes[${index}]`);
    const name = requireNonEmptyString(cause.cause, `candidate_causes[${index}].cause`);
    const citations = requireStringArray(
      cause.evidence_ids,
      `candidate_causes[${index}].evidence_ids`,
      { allowEmpty: false },
    );
    if (citations.some((evidenceId) => !knownEvidence.has(evidenceId))) {
      throw new Error(`candidate_causes[${index}] 引用了未知 evidence_id`);
    }
    return name;
  });
  if (new Set(causeNames).size !== causeNames.length) throw new Error("候选原因名称不得重复");
  const knownCauses = new Set(causeNames);

  const actionIds = [];
  card.recommended_actions.forEach((action, index) => {
    requireObject(action, `recommended_actions[${index}]`);
    actionIds.push(requireNonEmptyString(action.action_id, `recommended_actions[${index}].action_id`));
    requireNonEmptyString(action.why, `recommended_actions[${index}].why`);
    if (action.approval_required !== true) {
      throw new Error(`recommended_actions[${index}] 必须保留人工审批`);
    }
    const evidence = requireStringArray(
      action.evidence_ids,
      `recommended_actions[${index}].evidence_ids`,
      { allowEmpty: false },
    );
    if (evidence.some((evidenceId) => !knownEvidence.has(evidenceId))) {
      throw new Error(`recommended_actions[${index}] 引用了未知 evidence_id`);
    }
    const causes = requireStringArray(
      action.candidate_causes,
      `recommended_actions[${index}].candidate_causes`,
      { allowEmpty: false },
    );
    if (causes.some((cause) => !knownCauses.has(cause))) {
      throw new Error(`recommended_actions[${index}] 引用了未知候选原因`);
    }
  });
  if (new Set(actionIds).size !== actionIds.length) throw new Error("action_id 不得重复");
  return knownEvidence;
}

function closeEnough(left, right) {
  return Math.abs(left - right) <= 0.002;
}

function validateComparisons(result, metrics, knownEvidence, triggerReasons, attributionRequired) {
  if (!Array.isArray(result.comparisons) || result.comparisons.length !== COMPARISON_METRIC_IDS.length) {
    throw new Error("result.comparisons 必须包含四项权威窗口对比");
  }
  const allowedRules = new Set(triggerReasons);
  triggerReasons.forEach((reason) => {
    if (reason.startsWith("spc_rule=")) allowedRules.add(reason.slice("spc_rule=".length));
  });
  requireStringArray(metrics.rule_ids, "metrics.rule_ids").forEach((ruleId) => allowedRules.add(ruleId));

  const expectedValues = {
    torque_mean_nm: [metrics.baseline_mean_nm, metrics.recent_mean_nm],
    angle_dispersion_ratio: [1, metrics.angle_std_ratio],
    retry_mean: [metrics.retry_baseline, metrics.retry_recent],
    in_spec_rate: [metrics.baseline_in_spec_rate * 100, metrics.in_spec_rate * 100],
  };
  const seen = new Set();
  const comparisons = result.comparisons.map((item, index) => {
    requireExactKeys(item, COMPARISON_KEYS, `result.comparisons[${index}]`);
    const metricId = requireNonEmptyString(item.metric_id, `result.comparisons[${index}].metric_id`);
    if (!COMPARISON_METRIC_IDS.includes(metricId) || seen.has(metricId)) {
      throw new Error("result.comparisons 的 metric_id 缺失、重复或非法");
    }
    seen.add(metricId);
    requireNonEmptyString(item.label, `result.comparisons[${index}].label`);
    if (item.unit !== COMPARISON_UNITS[metricId]) {
      throw new Error(`result.comparisons[${index}].unit 与指标不一致`);
    }
    const baseline = requireFiniteNumber(item.baseline, `result.comparisons[${index}].baseline`);
    const current = requireFiniteNumber(item.current, `result.comparisons[${index}].current`);
    const delta = requireFiniteNumber(item.delta, `result.comparisons[${index}].delta`);
    const [expectedBaseline, expectedCurrent] = expectedValues[metricId];
    if (!closeEnough(baseline, expectedBaseline)
      || !closeEnough(current, expectedCurrent)
      || !closeEnough(delta, current - baseline)) {
      throw new Error(`result.comparisons[${index}] 与权威 metrics 不一致`);
    }
    if (!new Set(["normal", "triggered"]).has(item.status)) {
      throw new Error(`result.comparisons[${index}].status 非法`);
    }
    const ruleIds = requireStringArray(item.rule_ids, `result.comparisons[${index}].rule_ids`);
    const evidenceIds = requireStringArray(
      item.evidence_ids,
      `result.comparisons[${index}].evidence_ids`,
      { allowEmpty: false },
    );
    if (ruleIds.some((ruleId) => !allowedRules.has(ruleId))) {
      throw new Error(`result.comparisons[${index}] 包含未记录的触发规则`);
    }
    if (evidenceIds.some((evidenceId) => !knownEvidence.has(evidenceId))) {
      throw new Error(`result.comparisons[${index}] 引用了未知 evidence_id`);
    }
    if ((item.status === "triggered") !== (ruleIds.length > 0)) {
      throw new Error(`result.comparisons[${index}] 的状态与规则引用不一致`);
    }
    if (!attributionRequired && (item.status !== "normal" || ruleIds.length)) {
      throw new Error("稳定窗口的 comparisons 不得保留触发状态");
    }
    return {
      metricId,
      label: item.label,
      baseline,
      current,
      delta,
      unit: item.unit,
      status: item.status,
      ruleIds: [...ruleIds],
      evidenceIds: [...evidenceIds],
    };
  });
  if (seen.size !== COMPARISON_METRIC_IDS.length) throw new Error("result.comparisons 指标不完整");
  return comparisons;
}

export function escapeHtml(value) {
  const entities = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
  return String(value ?? "").replace(/[&<>"']/g, (character) => entities[character]);
}

export function traceMode(card) {
  const trace = card?.agent_trace;
  if (!Array.isArray(trace) || trace.length === 0) {
    throw new Error("工具调用轨迹不能为空");
  }
  const modes = trace.map((item) => item?.trace_mode);
  if (modes.some((mode) => typeof mode !== "string" || !mode)) {
    throw new Error("工具调用轨迹的 trace_mode 不完整");
  }
  if (new Set(modes).size !== 1) {
    throw new Error("工具调用轨迹的 trace_mode 不一致");
  }
  return modes[0];
}

/**
 * Build the browser's read-only view model from Python-generated assets.
 *
 * Risk scoring intentionally does not happen in the browser. The browser
 * verifies identity, provenance, window, workflow and score invariants, then
 * displays the already-computed RiskAnalyzer conclusion.
 */
export function authoritativeAnalysis(card, result) {
  requireObject(card, "risk card");
  requireObject(result, "authoritative result");
  if (card.schema_version !== "1.0") throw new Error("风险卡的数据契约不受支持");
  if (result.schema_version !== "1.0" || result.display_contract !== "browser_read_only") {
    throw new Error("分析结果的数据契约不受支持");
  }
  if (result.generated_by !== "torque_guard.risk.RiskAnalyzer") {
    throw new Error("分析结果不是由 RiskAnalyzer 生成");
  }
  if (!CARD_ID_PATTERN.test(card.card_id)) throw new Error("风险卡 card_id 格式非法");
  if (result.card_id !== card.card_id) throw new Error("风险卡与分析结果的 card_id 不一致");
  if (!Object.hasOwn(STATUS_ACTIONS, card.status)) throw new Error("风险卡 status 非法");

  const provenance = requireObject(card.analysis_provenance, "analysis_provenance");
  if (provenance.generated_by !== result.generated_by) {
    throw new Error("风险卡缺少一致的分析来源记录");
  }
  for (const field of ["risk_policy_version", "knowledge_schema", "normalized_window_schema"]) {
    requireNonEmptyString(provenance[field], `analysis_provenance.${field}`);
  }
  for (const field of ["knowledge_revision", "input_window_revision", "card_identity_revision"]) {
    if (!SHA256_PATTERN.test(provenance[field])) {
      throw new Error(`analysis_provenance.${field} 不是有效 SHA-256 revision`);
    }
  }
  const expectedCardId = `TG-${provenance.card_identity_revision.slice(7, 39).toUpperCase()}`;
  if (card.card_id !== expectedCardId) throw new Error("card_id 与 card_identity_revision 不一致");

  const score = requireFiniteNumber(card.risk_score, "card.risk_score");
  if (!Number.isInteger(score) || score < 0 || score > 100) {
    throw new Error("风险评分必须是 0–100 的整数");
  }
  if (result.risk_score !== score) throw new Error("风险卡与分析结果的分数不一致");
  if (result.risk_level !== card.risk_level) throw new Error("风险卡与分析结果的等级不一致");
  let breakdownTotal = 0;
  for (const key of SCORE_KEYS) {
    const cardValue = requireFiniteNumber(card.score_breakdown?.[key], `card.score_breakdown.${key}`);
    const resultValue = requireFiniteNumber(result.score_breakdown?.[key], `result.score_breakdown.${key}`);
    if (cardValue !== resultValue) throw new Error(`风险卡与分析结果的 ${key} 分项不一致`);
    if (!Number.isInteger(cardValue) || cardValue < 0 || cardValue > SCORE_LIMITS[key]) {
      throw new Error(`风险评分分项越界：${key}`);
    }
    breakdownTotal += cardValue;
  }
  if (breakdownTotal !== score) throw new Error("风险评分与分项合计不一致");
  const expectedLevel = score >= 75 ? "high" : score >= 45 ? "medium" : "low";
  if (card.risk_level !== expectedLevel) throw new Error("风险等级与评分阈值不一致");

  const analysisWindow = requireObject(result.analysis_window, "result.analysis_window");
  const baselineCount = requireInteger(analysisWindow.baseline_count, "analysis_window.baseline_count", 1);
  const recentCount = requireInteger(analysisWindow.recent_count, "analysis_window.recent_count", 1);
  if (provenance.baseline_count !== baselineCount || provenance.recent_count !== recentCount) {
    throw new Error("analysis_provenance 与分析结果的窗口数量不一致");
  }

  const stratum = requireObject(provenance.analysis_stratum, "analysis_provenance.analysis_stratum");
  for (const field of ["station_id", "tool_id", "model_code", "program_id", "fastening_point"]) {
    requireNonEmptyString(stratum[field], `analysis_provenance.analysis_stratum.${field}`);
  }
  if (stratum.station_id !== card.station_id
    || stratum.tool_id !== card.tool_id
    || stratum.fastening_point !== card.fastening_point) {
    throw new Error("analysis_stratum 与风险卡主标识不一致");
  }
  const affectedScope = requireObject(card.affected_scope, "affected_scope");
  if (affectedScope.event_count !== recentCount) throw new Error("affected_scope.event_count 与 recent_count 不一致");
  if (!sameStringArray(affectedScope.model_code, [stratum.model_code])
    || !sameStringArray(affectedScope.program_id, [stratum.program_id])) {
    throw new Error("affected_scope 与 analysis_stratum 不一致");
  }
  if (affectedScope.window_end !== card.created_at) throw new Error("affected_scope.window_end 与 created_at 不一致");
  requireNonEmptyString(affectedScope.window_start, "affected_scope.window_start");
  requireNonEmptyString(affectedScope.window_end, "affected_scope.window_end");
  validateMetricAvailability(provenance);

  if (typeof provenance.attribution_required !== "boolean") {
    throw new Error("analysis_provenance.attribution_required 必须是布尔值");
  }
  const triggerReasons = requireStringArray(provenance.trigger_reasons, "analysis_provenance.trigger_reasons");
  if (!Array.isArray(card.candidate_causes) || !Array.isArray(card.recommended_actions)) {
    throw new Error("风险卡的候选原因和建议动作必须是列表");
  }
  const knownEvidence = validateDecisionLinks(card);
  const reasoning = requireObject(card.reasoning, "reasoning");
  if (!new Set(["supported", "refused"]).has(reasoning.decision)) throw new Error("reasoning.decision 非法");

  if (provenance.attribution_required) {
    if (provenance.analysis_disposition !== "investigation_required" || !triggerReasons.length) {
      throw new Error("需要归因时必须记录 investigation_required 和触发原因");
    }
    if (card.status === "monitoring_only") throw new Error("需要归因的风险卡不得处于 monitoring_only");
  } else {
    if (provenance.analysis_disposition !== "stable_monitoring" || triggerReasons.length) {
      throw new Error("稳定窗口必须使用 stable_monitoring 且不得记录触发原因");
    }
    if (card.risk_level !== "low" || card.status !== "monitoring_only") {
      throw new Error("无需归因的风险卡必须为 low/monitoring_only");
    }
    if (card.candidate_causes.length || card.recommended_actions.length) {
      throw new Error("稳定窗口不得生成候选原因或建议动作");
    }
    if (reasoning.decision !== "refused"
      || reasoning.disposition !== "no_attribution_required"
      || reasoning.conclusion !== null) {
      throw new Error("稳定窗口必须输出 no_attribution_required 拒绝归因契约");
    }
  }

  const externalSync = validateWorkflow(card, provenance.attribution_required);
  const metrics = requireObject(result.metrics, "result.metrics");
  const inSpecRate = requireFiniteNumber(metrics.in_spec_rate, "metrics.in_spec_rate");
  if (inSpecRate < 0 || inSpecRate > 1) throw new Error("规格内比例必须位于 0–1");
  const baselineInSpecRate = requireFiniteNumber(metrics.baseline_in_spec_rate, "metrics.baseline_in_spec_rate");
  if (baselineInSpecRate < 0 || baselineInSpecRate > 1) throw new Error("基线规格内比例必须位于 0–1");
  const meanShiftSigma = requireFiniteNumber(metrics.mean_shift_sigma, "metrics.mean_shift_sigma");
  const angleRatio = requireFiniteNumber(metrics.angle_std_ratio, "metrics.angle_std_ratio");
  const retryBaseline = requireFiniteNumber(metrics.retry_baseline, "metrics.retry_baseline");
  const retryRecent = requireFiniteNumber(metrics.retry_recent, "metrics.retry_recent");
  if (meanShiftSigma < 0 || angleRatio < 0 || retryBaseline < 0 || retryRecent < 0) {
    throw new Error("分析指标不能为负数");
  }
  const calibrationDaysRemaining = requireFiniteNumber(
    metrics.calibration_days_remaining,
    "metrics.calibration_days_remaining",
  );
  if (!Number.isInteger(calibrationDaysRemaining)) throw new Error("标定剩余天数必须是整数");
  const ruleIds = requireStringArray(metrics.rule_ids, "metrics.rule_ids");
  const comparisons = validateComparisons(
    result,
    metrics,
    knownEvidence,
    triggerReasons,
    provenance.attribution_required,
  );

  return {
    score,
    level: card.risk_level,
    status: card.status,
    attributionRequired: provenance.attribution_required,
    analysisDisposition: provenance.analysis_disposition,
    triggerReasons: [...triggerReasons],
    meanShiftSigma,
    angleRatio,
    retryBaseline,
    retryRecent,
    calibrationDaysRemaining,
    recentMean: requireFiniteNumber(metrics.recent_mean_nm, "metrics.recent_mean_nm"),
    baselineMean: requireFiniteNumber(metrics.baseline_mean_nm, "metrics.baseline_mean_nm"),
    inSpecRate,
    baselineInSpecRate,
    comparisons,
    breakdown: { ...card.score_breakdown },
    baselineCount,
    recentCount,
    ruleIds: [...ruleIds],
    generatedBy: result.generated_by,
    externalSyncStatus: externalSync?.sync_status ?? "not_attempted",
    externalSyncPersisted: externalSync !== null,
    externalTasksConfirmed: externalSync?.sync_status === "succeeded",
  };
}

export function pathFor(values, width, height, minimum, maximum) {
  const span = Math.max(maximum - minimum, 0.01);
  return values
    .map((value, index) => {
      const x = values.length === 1 ? 0 : (index / (values.length - 1)) * width;
      const y = height - ((value - minimum) / span) * height;
      return `${index === 0 ? "M" : "L"}${x.toFixed(2)},${y.toFixed(2)}`;
    })
    .join(" ");
}
