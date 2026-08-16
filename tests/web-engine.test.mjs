import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { comparisonViewModel, linkedDecisionPath } from "../docs/app.js";
import { authoritativeAnalysis, escapeHtml, pathFor, traceMode } from "../docs/risk-engine.js";

async function fixture(name) {
  return JSON.parse(await readFile(new URL(`../docs/data/${name}`, import.meta.url), "utf8"));
}

const [riskCard, baselineCard, riskResult, baselineResult, riskSeries, baselineSeries, scenarioMetrics] = await Promise.all([
  fixture("risk_card.json"),
  fixture("baseline_card.json"),
  fixture("risk_result.json"),
  fixture("baseline_result.json"),
  fixture("demo_series.json"),
  fixture("baseline_series.json"),
  fixture("scenario_metrics.json"),
]);
const indexHtml = await readFile(new URL("../docs/index.html", import.meta.url), "utf8");
const appSource = await readFile(new URL("../docs/app.js", import.meta.url), "utf8");
const engineSource = await readFile(new URL("../docs/risk-engine.js", import.meta.url), "utf8");
const stylesSource = await readFile(new URL("../docs/styles.css", import.meta.url), "utf8");

test("risk replay consumes the Python RiskAnalyzer result without rescoring", () => {
  const analysis = authoritativeAnalysis(riskCard, riskResult);
  assert.equal(analysis.generatedBy, "torque_guard.risk.RiskAnalyzer");
  assert.equal(analysis.score, riskCard.risk_score);
  assert.equal(analysis.level, "high");
  assert.equal(analysis.baselineCount, 100);
  assert.equal(analysis.recentCount, 24);
  assert.equal(analysis.meanShiftSigma, 1.56);
  assert.equal(analysis.inSpecRate, 1);
  assert.deepEqual(analysis.breakdown, riskCard.score_breakdown);
});

test("baseline replay is a second authoritative card using the identical window contract", () => {
  const analysis = authoritativeAnalysis(baselineCard, baselineResult);
  assert.equal(analysis.generatedBy, "torque_guard.risk.RiskAnalyzer");
  assert.equal(analysis.score, baselineCard.risk_score);
  assert.equal(analysis.level, "low");
  assert.equal(analysis.status, "monitoring_only");
  assert.equal(analysis.attributionRequired, false);
  assert.ok(analysis.score < 45);
  assert.equal(analysis.baselineCount, riskResult.analysis_window.baseline_count);
  assert.equal(analysis.recentCount, riskResult.analysis_window.recent_count);
  assert.equal(baselineSeries.length, analysis.baselineCount + analysis.recentCount);
  assert.ok(baselineSeries.every((row) => row.scenario_label === "normal"));
  assert.ok(riskSeries.some((row) => row.scenario_label === "hidden_risk"));
  assert.deepEqual(baselineCard.candidate_causes, []);
  assert.deepEqual(baselineCard.recommended_actions, []);
  assert.equal(baselineCard.reasoning.decision, "refused");
  assert.equal(baselineCard.reasoning.disposition, "no_attribution_required");
  assert.equal(baselineCard.workflow.human_approval_required, false);
  assert.deepEqual(baselineCard.workflow.allowed_actions, []);
});

test("comparison view model exposes the four authoritative rows for risk and stable windows", () => {
  const expectedMetricIds = [
    "torque_mean_nm",
    "angle_dispersion_ratio",
    "retry_mean",
    "in_spec_rate",
  ];
  const riskView = comparisonViewModel(riskCard, riskResult);
  const stableView = comparisonViewModel(baselineCard, baselineResult);

  assert.deepEqual(riskView.rows.map((row) => row.metricId), expectedMetricIds);
  assert.deepEqual(stableView.rows.map((row) => row.metricId), expectedMetricIds);
  assert.equal(riskView.rows.length, 4);
  assert.equal(stableView.rows.length, 4);
  assert.deepEqual(
    riskView.rows.map((row) => row.status),
    ["triggered", "triggered", "triggered", "normal"],
  );
  assert.ok(stableView.rows.every((row) => row.status === "normal"));
  assert.ok(stableView.rows.every((row) => row.ruleIds.length === 0));

  assert.deepEqual(
    riskView.rows.map(({ metricId, baseline, current, delta }) => ({ metricId, baseline, current, delta })),
    [
      { metricId: "torque_mean_nm", baseline: 48.126, current: 49.086, delta: 0.96 },
      { metricId: "angle_dispersion_ratio", baseline: 1, current: 2.17, delta: 1.17 },
      { metricId: "retry_mean", baseline: 0.01, current: 0.25, delta: 0.24 },
      { metricId: "in_spec_rate", baseline: 100, current: 100, delta: 0 },
    ],
  );
  assert.match(riskView.nextStep, /具名工程师复核/);
  assert.match(stableView.nextStep, /无需归因或创建处置任务/);
});

test("recommended tasks explain why and reference only evidence and candidates in the same card", () => {
  const knownEvidence = new Set(riskCard.evidence.map((item) => item.evidence_id));
  const knownCandidates = new Set(riskCard.candidate_causes.map((item) => item.cause));

  assert.ok(riskCard.recommended_actions.length > 0);
  for (const action of riskCard.recommended_actions) {
    assert.ok(action.why.trim(), `${action.action_id} must explain why it was generated`);
    assert.ok(action.evidence_ids.length > 0, `${action.action_id} must cite evidence`);
    assert.ok(action.candidate_causes.length > 0, `${action.action_id} must cite candidates`);
    assert.ok(
      action.evidence_ids.every((evidenceId) => knownEvidence.has(evidenceId)),
      `${action.action_id} contains a dangling evidence reference`,
    );
    assert.ok(
      action.candidate_causes.every((cause) => knownCandidates.has(cause)),
      `${action.action_id} contains a dangling candidate reference`,
    );
  }

  const danglingEvidence = structuredClone(riskCard);
  danglingEvidence.recommended_actions[0].evidence_ids.push("E-NOT-FOUND");
  assert.throws(
    () => authoritativeAnalysis(danglingEvidence, riskResult),
    /recommended_actions\[0\] 引用了未知 evidence_id/,
  );

  const danglingCandidate = structuredClone(riskCard);
  danglingCandidate.recommended_actions[0].candidate_causes.push("已确认的虚构根因");
  assert.throws(
    () => authoritativeAnalysis(danglingCandidate, riskResult),
    /recommended_actions\[0\] 引用了未知候选原因/,
  );
});

test("linked decision path follows risk citations and never invents stable causes or tasks", () => {
  const riskView = comparisonViewModel(riskCard, riskResult);
  const torqueComparison = riskView.rows.find((row) => row.metricId === "torque_mean_nm");
  const riskPath = linkedDecisionPath(riskCard, torqueComparison);

  assert.deepEqual(riskPath.ruleIds, ["MW-24"]);
  assert.deepEqual(riskPath.evidenceIds, ["E-SPC-01"]);
  assert.deepEqual(
    riskPath.causes.map((cause) => cause.cause),
    ["工具标定漂移", "连接件批次摩擦特性变化"],
  );
  assert.deepEqual(
    riskPath.actions.map((action) => action.action_id),
    ["A-01", "A-02", "A-03", "A-04"],
  );

  const stableView = comparisonViewModel(baselineCard, baselineResult);
  for (const comparison of stableView.rows) {
    const stablePath = linkedDecisionPath(baselineCard, comparison);
    assert.deepEqual(stablePath.ruleIds, []);
    assert.deepEqual(stablePath.causes, []);
    assert.deepEqual(stablePath.actions, []);
  }
});

test("comparison tampering and dangling references fail closed before reaching the view", () => {
  const changedValue = structuredClone(riskResult);
  changedValue.comparisons[0].current += 0.5;
  changedValue.comparisons[0].delta += 0.5;
  assert.throws(
    () => comparisonViewModel(riskCard, changedValue),
    /与权威 metrics 不一致/,
  );

  const danglingEvidence = structuredClone(riskResult);
  danglingEvidence.comparisons[0].evidence_ids = ["E-NOT-FOUND"];
  assert.throws(
    () => comparisonViewModel(riskCard, danglingEvidence),
    /引用了未知 evidence_id/,
  );

  const unrecordedRule = structuredClone(riskResult);
  unrecordedRule.comparisons[0].rule_ids = ["UNRECORDED-RULE"];
  assert.throws(
    () => comparisonViewModel(riskCard, unrecordedRule),
    /包含未记录的触发规则/,
  );
});

test("mixed or stale generated assets fail instead of displaying inconsistent scores", () => {
  assert.throws(
    () => authoritativeAnalysis(riskCard, baselineResult),
    /card_id 不一致/,
  );
  assert.throws(
    () => authoritativeAnalysis(riskCard, { ...riskResult, risk_score: riskResult.risk_score - 1 }),
    /分数不一致/,
  );
  assert.throws(
    () => authoritativeAnalysis(
      { ...riskCard, score_breakdown: { ...riskCard.score_breakdown, context: 999 } },
      { ...riskResult, score_breakdown: { ...riskResult.score_breakdown, context: 999 } },
    ),
    /分项越界/,
  );
  assert.throws(
    () => authoritativeAnalysis(
      { ...riskCard, risk_level: "low" },
      { ...riskResult, risk_level: "low" },
    ),
    /等级与评分阈值不一致/,
  );
});

test("identity, provenance, scope and metric availability tampering fail closed", () => {
  const malformedIdCard = structuredClone(riskCard);
  const malformedIdResult = structuredClone(riskResult);
  malformedIdCard.card_id = riskCard.card_id.toLowerCase();
  malformedIdResult.card_id = malformedIdCard.card_id;
  assert.throws(() => authoritativeAnalysis(malformedIdCard, malformedIdResult), /card_id 格式非法/);

  const wrongIdentity = structuredClone(riskCard);
  wrongIdentity.analysis_provenance.card_identity_revision = `sha256:${"0".repeat(64)}`;
  assert.throws(() => authoritativeAnalysis(wrongIdentity, riskResult), /card_id 与 card_identity_revision 不一致/);

  const malformedRevision = structuredClone(riskCard);
  malformedRevision.analysis_provenance.knowledge_revision = `sha256:${"A".repeat(64)}`;
  assert.throws(() => authoritativeAnalysis(malformedRevision, riskResult), /不是有效 SHA-256 revision/);

  const wrongWindow = structuredClone(riskCard);
  wrongWindow.analysis_provenance.recent_count += 1;
  assert.throws(() => authoritativeAnalysis(wrongWindow, riskResult), /窗口数量不一致/);

  const wrongScope = structuredClone(riskCard);
  wrongScope.affected_scope.event_count -= 1;
  assert.throws(() => authoritativeAnalysis(wrongScope, riskResult), /event_count 与 recent_count 不一致/);

  const missingMetric = structuredClone(riskCard);
  delete missingMetric.analysis_provenance.metric_availability.cycle_time_s;
  assert.throws(() => authoritativeAnalysis(missingMetric, riskResult), /metric_availability 结构不完整/);

  const inconsistentAvailability = structuredClone(riskCard);
  inconsistentAvailability.analysis_provenance.metric_availability.current_a.recent_sample_count -= 1;
  assert.throws(() => authoritativeAnalysis(inconsistentAvailability, riskResult), /available 与样本数量不一致/);
});

test("workflow and persisted external-sync contracts reject unsafe states and legacy aliases", () => {
  const wrongActions = structuredClone(riskCard);
  wrongActions.workflow.allowed_actions = ["create_tasks"];
  assert.throws(() => authoritativeAnalysis(wrongActions, riskResult), /allowed_actions 与当前 status 不一致/);

  const failedSync = structuredClone(riskCard);
  failedSync.workflow.external_sync = {
    schema_version: "1.0",
    mode: "live",
    card_id: failedSync.card_id,
    sync_status: "failed",
    failure_stage: "configuration",
    request_ids: {},
    remote_ids: { risk: [], tasks: [] },
    reconciliation_required: false,
    automatic_retry_safe: true,
    error: { type: "ConfigurationError", message: "missing tenant configuration" },
  };
  assert.equal(authoritativeAnalysis(failedSync, riskResult).externalSyncStatus, "failed");

  const legacyAlias = structuredClone(failedSync);
  legacyAlias.workflow.external_sync.manual_reconciliation_required = false;
  delete legacyAlias.workflow.external_sync.reconciliation_required;
  assert.throws(() => authoritativeAnalysis(legacyAlias, riskResult), /字段不完整或包含未知字段/);

  const unknownStatus = structuredClone(failedSync);
  unknownStatus.workflow.external_sync.sync_status = "maybe";
  assert.throws(() => authoritativeAnalysis(unknownStatus, riskResult), /sync_status 非法/);

  const unsafePartial = structuredClone(failedSync);
  Object.assign(unsafePartial.workflow.external_sync, {
    sync_status: "partial",
    failure_stage: "task_create",
    request_ids: { risk_create: "req-risk" },
    reconciliation_required: false,
    automatic_retry_safe: false,
  });
  assert.throws(() => authoritativeAnalysis(unsafePartial, riskResult), /partial 外部同步必须要求人工对账/);

  const stableWithSync = structuredClone(baselineCard);
  stableWithSync.workflow.external_sync = structuredClone(failedSync.workflow.external_sync);
  stableWithSync.workflow.external_sync.card_id = stableWithSync.card_id;
  assert.throws(() => authoritativeAnalysis(stableWithSync, baselineResult), /稳定监控 workflow 不得包含 external_sync/);
});

test("trace mode is explicit, non-empty and consistent for both generated scenarios", () => {
  assert.equal(traceMode(riskCard), "deterministic_public_build");
  assert.equal(traceMode(baselineCard), "deterministic_public_build");
  assert.throws(() => traceMode({ agent_trace: [] }), /不能为空/);
  const mixed = structuredClone(riskCard);
  mixed.agent_trace[1].trace_mode = "runtime_audit";
  assert.throws(() => traceMode(mixed), /不一致/);
});

test("dynamic card and knowledge values are HTML-escaped before template rendering", () => {
  const malicious = `<img src=x onerror="alert('x')">&`;
  assert.equal(
    escapeHtml(malicious),
    "&lt;img src=x onerror=&quot;alert(&#39;x&#39;)&quot;&gt;&amp;",
  );
  assert.equal(escapeHtml(null), "");
  assert.match(appSource, /escapeHtml\(JSON\.stringify\(item\.data/);
  assert.doesNotMatch(appSource, /\$\{item\.(?:title|observation|source|locator|strength|cause|verification|action_id|owner_role|result|call_id)\}/);
});

test("published risk card exposes truthful reasoning provenance, citations and human gates", () => {
  const knownEvidence = new Set(riskCard.evidence.map((item) => item.evidence_id));
  const reasoning = riskCard.reasoning;
  assert.equal(reasoning.schema_version, "1.0");
  assert.equal(reasoning.prompt_version, "1.0");
  assert.equal(reasoning.provenance.reasoner_mode, "deterministic");
  assert.equal(reasoning.provenance.model, null);
  assert.ok(reasoning.conclusion.evidence_ids.length > 0);
  assert.ok(reasoning.conclusion.evidence_ids.every((evidenceId) => knownEvidence.has(evidenceId)));
  assert.equal(reasoning.safety.requires_human_approval, true);
  assert.equal(reasoning.safety.automatic_action_allowed, false);
  assert.equal(riskCard.workflow.human_approval_required, true);
  assert.equal(riskCard.workflow.automatic_stop_line_allowed, false);
  assert.ok(riskCard.agent_trace.length >= 4);
  assert.ok(riskCard.agent_trace.every((call) => call.status === "succeeded"));
  assert.ok(riskCard.agent_trace.every((call) => Number.isFinite(call.duration_ms) && call.duration_ms >= 0));
  assert.deepEqual([...new Set(riskCard.agent_trace.map((call) => call.trace_mode))], ["deterministic_public_build"]);
});

test("published audit trails are deterministic and platform-neutral", () => {
  for (const [card, scope] of [[riskCard, "RISK"], [baselineCard, "BASELINE"]]) {
    assert.deepEqual(
      card.agent_trace.map((call) => call.call_id),
      card.agent_trace.map((_, index) => `CALL-PUBLIC-${scope}-${String(index + 1).padStart(3, "0")}`),
    );
    assert.ok(card.agent_trace.every((call) => call.trace_mode === "deterministic_public_build"));
    assert.ok(card.agent_trace.every((call) => call.started_at === "2026-07-20T00:00:00Z"));
    assert.ok(card.agent_trace.every((call) => call.completed_at === "2026-07-20T00:00:00Z"));
    assert.ok(card.agent_trace.every((call) => call.duration_ms === 0));
    assert.doesNotMatch(JSON.stringify(card.agent_trace), /[A-Za-z]:\\\\/);
  }
  assert.equal(riskCard.agent_trace[0].input_summary.file, "data/tightening_events_demo.csv");
});

test("multi-scenario metrics are independent, interval-qualified and beat the transparent alarm proxy", () => {
  const scenarios = Object.entries(scenarioMetrics.scenarios);
  const abnormal = scenarios.filter(([name]) => name !== "normal");
  assert.equal(scenarioMetrics.cases, 120);
  assert.equal(scenarios.reduce((total, [, item]) => total + item.cases, 0), scenarioMetrics.cases);
  assert.equal(abnormal.length, 3);
  assert.ok(abnormal.every(([, item]) => item.cases === 30));
  assert.deepEqual(scenarioMetrics.recall_95pct_wilson, [0.9591, 1]);
  assert.deepEqual(scenarioMetrics.false_positive_rate_95pct_wilson, [0, 0.1135]);
  assert.ok(scenarioMetrics.recall > scenarioMetrics.conventional_alarm_recall);
  assert.match(scenarioMetrics.evaluation_scope, /not factory evidence/);
});

test("public page states model, Feishu and factory-evidence boundaries and links pilot materials", () => {
  assert.match(indexHtml, /本次运行未调用任何外部模型|正在核对本次运行的模型调用记录/);
  assert.match(indexHtml, /本页面绝不表示已经真实发送/);
  assert.match(indexHtml, /非工厂效果/);
  assert.match(indexHtml, /business-value\.md/);
  assert.match(indexHtml, /pilot-plan\.md/);
  assert.match(appSource, /deterministic_public_build/);
  assert.match(appSource, /0 ms 不代表瞬时完成/);
  assert.match(appSource, /traceMode\(card\)/);
  assert.match(appSource, /复制失败，请检查浏览器权限/);
  assert.match(appSource, /catch \(error\)/);
});

test("scenario UI is result-driven, accessible and fails visibly when the graph is unavailable", () => {
  for (const id of [
    "signal-rules",
    "signal-calibration",
    "signal-product-impact",
    "inference-title",
    "inference-body",
    "inference-uncertainty",
    "scenario-announcement",
    "external-sync-state",
  ]) {
    assert.match(indexHtml, new RegExp(`id="${id}"`));
  }
  assert.match(indexHtml, /aria-pressed="true"/);
  assert.match(indexHtml, /aria-pressed="false"/);
  assert.match(indexHtml, /aria-live="polite" aria-atomic="true"/);
  assert.match(appSource, /series\.slice\(-result\.recentCount\)/);
  assert.match(appSource, /riskZone\.setAttribute\("display"/);
  assert.match(appSource, /知识子图暂不可用/);
  assert.match(appSource, /externalSyncStatus === "partial"/);
  assert.match(appSource, /externalSyncStatus === "failed"/);
  assert.doesNotMatch(appSource, /tasksCreated|series\.slice\(-24\)|riskZone\.hidden|最近 24|已生成/);
  assert.doesNotMatch(indexHtml, /MW-24 滚动均值偏移|标定到期前 9 天|PFMEA S=9/);
  assert.match(stylesSource, /\[tabindex="-1"\]:focus/);
  assert.match(stylesSource, /outline:\s*3px solid var\(--focus-ring\)/);
  assert.match(engineSource, /reconciliation_required/);
  assert.doesNotMatch(engineSource, /manual_reconciliation_required/);
});

test("sidebar navigation follows clicks and the currently visible section", () => {
  assert.match(appSource, /function bindSidebarNavigation\(\)/);
  assert.match(appSource, /activateSidebarTarget\(link\.hash, true\)/);
  assert.match(appSource, /window\.addEventListener\("scroll", scheduleSync/);
  assert.match(appSource, /setAttribute\("aria-current", "location"\)/);
  assert.match(appSource, /syncSidebarToScroll\(\)/);
});

test("primary sidebar navigation is exactly the six task-oriented sections", () => {
  const navMatch = indexHtml.match(/<aside class="sidebar"[\s\S]*?<nav>([\s\S]*?)<\/nav>/);
  assert.ok(navMatch, "sidebar navigation must exist");
  const links = [...navMatch[1].matchAll(/<a\b[^>]*href="([^"]+)"[^>]*>([^<]+)<\/a>/g)]
    .map((match) => ({ href: match[1], label: match[2].trim() }));

  assert.deepEqual(links, [
    { href: "#overview", label: "数据概览" },
    { href: "#comparison", label: "对比分析" },
    { href: "#risk-analysis", label: "触发原因" },
    { href: "#evidence", label: "证据来源" },
    { href: "#workflow", label: "处置任务" },
    { href: "#evaluation", label: "评估报告" },
  ]);
  assert.doesNotMatch(navMatch[1], /管理员|admin-workbench/);
  assert.doesNotMatch(indexHtml, /href="#admin-workbench"/);
  assert.doesNotMatch(appSource, /\.href\s*=\s*["']#admin-workbench["']/);
  assert.doesNotMatch(appSource, /insertAdminNavLink/);
  links.forEach(({ href }) => {
    assert.match(indexHtml, new RegExp(`id="${href.slice(1)}"`));
  });
});

test("chart path contains one coordinate per value", () => {
  const values = [43, 48, 53];
  const path = pathFor(values, 100, 100, 43, 53);
  assert.match(path, /^M/);
  assert.equal((path.match(/[ML]/g) || []).length, values.length);
});
