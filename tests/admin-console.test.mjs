import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  ADMIN_BOUNDARY_NOTICE,
  buildAdminSnapshot,
  reproductionCommands,
  serializeAdminSnapshot,
} from "../docs/admin-console.js";
import { analysisOverview } from "../docs/app.js";
import { authoritativeAnalysis } from "../docs/risk-engine.js";

async function fixture(name) {
  return JSON.parse(await readFile(new URL(`../docs/data/${name}`, import.meta.url), "utf8"));
}

const [riskCard, baselineCard, riskResult, baselineResult, riskSeries, baselineSeries, appSource] = await Promise.all([
  fixture("risk_card.json"),
  fixture("baseline_card.json"),
  fixture("risk_result.json"),
  fixture("baseline_result.json"),
  fixture("demo_series.json"),
  fixture("baseline_series.json"),
  readFile(new URL("../docs/app.js", import.meta.url), "utf8"),
]);

const generatedAt = "2026-08-06T00:00:00.000Z";

test("risk administrator snapshot aggregates health, evidence and the named approval gate", () => {
  const analysis = authoritativeAnalysis(riskCard, riskResult);
  const snapshot = buildAdminSnapshot(riskCard, analysis, { generatedAt, mode: "risk" });

  assert.equal(snapshot.schemaVersion, "1.0");
  assert.equal(snapshot.overallStatus, "attention");
  assert.equal(snapshot.dataHealth.status, "pass");
  assert.equal(snapshot.evidence.status, "pass");
  assert.equal(snapshot.evidence.referenceCompleteness, 1);
  assert.deepEqual(snapshot.evidence.missingReferenceIds, []);
  assert.equal(snapshot.approval.gateStatus, "awaiting_named_approval");
  assert.equal(snapshot.approval.automaticStopDisabled, true);
  assert.equal(snapshot.approval.externalWriteAllowedOnThisPage, false);
  assert.equal(snapshot.sync.status, "not_attempted");
  assert.equal(snapshot.sync.networkWriteAvailableOnThisPage, false);
  assert.equal(snapshot.recommendedActions.length, riskCard.recommended_actions.length);
  assert.ok(snapshot.recommendedActions.every((action) => action.approvalRequired));
  assert.ok(snapshot.recommendedActions.every((action) => action.why));
  assert.ok(snapshot.recommendedActions.every((action) => action.evidenceIds.length > 0));
  assert.ok(snapshot.recommendedActions.every((action) => action.candidateCauses.length > 0));
});

test("stable baseline is healthy without manufacturing citations, approvals or tasks", () => {
  const analysis = authoritativeAnalysis(baselineCard, baselineResult);
  const snapshot = buildAdminSnapshot(baselineCard, analysis, { generatedAt, mode: "baseline" });

  assert.equal(snapshot.overallStatus, "healthy");
  assert.equal(snapshot.evidence.requirement, "not_required");
  assert.equal(snapshot.evidence.referencedCount, 0);
  assert.equal(snapshot.evidence.referenceCompleteness, 1);
  assert.equal(snapshot.approval.gateStatus, "not_required");
  assert.equal(snapshot.approval.localTaskPreviewAvailable, false);
  assert.deepEqual(snapshot.recommendedActions, []);
});

test("missing evidence references and uncertain external sync fail the admin summary closed", () => {
  const analysis = authoritativeAnalysis(riskCard, riskResult);
  const missingEvidence = structuredClone(riskCard);
  missingEvidence.candidate_causes[0].evidence_ids.push("E-UNKNOWN");
  const missingSnapshot = buildAdminSnapshot(missingEvidence, analysis, { generatedAt });
  assert.equal(missingSnapshot.overallStatus, "blocked");
  assert.deepEqual(missingSnapshot.evidence.missingReferenceIds, ["E-UNKNOWN"]);
  assert.match(missingSnapshot.blockingReasons.join(" "), /证据引用不完整/);

  const partial = structuredClone(riskCard);
  partial.workflow.external_sync = {
    schema_version: "1.0",
    mode: "live",
    card_id: partial.card_id,
    sync_status: "partial",
    failure_stage: "task_create",
    request_ids: { risk_create: "req-risk", task_create: "req-task" },
    remote_ids: { risk: ["rec-risk"], tasks: [] },
    reconciliation_required: true,
    automatic_retry_safe: false,
  };
  const partialSnapshot = buildAdminSnapshot(partial, { ...analysis, externalSyncStatus: "partial" }, { generatedAt });
  assert.equal(partialSnapshot.overallStatus, "blocked");
  assert.equal(partialSnapshot.approval.gateStatus, "blocked_for_reconciliation");
  assert.equal(partialSnapshot.sync.reconciliationRequired, true);
  assert.equal(partialSnapshot.approval.localTaskPreviewAvailable, false);
});

test("export is a bounded local summary and reproduction controls only copy commands", () => {
  const analysis = authoritativeAnalysis(riskCard, riskResult);
  const snapshot = buildAdminSnapshot(riskCard, analysis, { generatedAt });
  const serialized = serializeAdminSnapshot(snapshot);

  assert.equal(snapshot.boundary, ADMIN_BOUNDARY_NOTICE);
  assert.equal(snapshot.reproduction.execution, "copy_only");
  assert.deepEqual(snapshot.reproduction.commands, reproductionCommands());
  assert.match(reproductionCommands()[0], /^python scripts\/build_all\.py$/);
  assert.ok(serialized.endsWith("\n"));
  assert.deepEqual(JSON.parse(serialized), snapshot);
  assert.doesNotMatch(serialized, /tenant_access_token|app_secret/i);
  assert.match(ADMIN_BOUNDARY_NOTICE, /不授予公司生产权限/);
  assert.match(ADMIN_BOUNDARY_NOTICE, /不代表已向飞书/);
});

test("the page dynamically exposes honest admin controls without adding a production action", () => {
  assert.match(appSource, /id = "admin-workbench"/);
  assert.match(appSource, /打开管理员审计台/);
  assert.match(appSource, /复制本地复现命令/);
  assert.match(appSource, /导出当前分析摘要/);
  assert.match(appSource, /URL\.createObjectURL\(blob\)/);
  assert.match(appSource, /页面不会上传、派单或写入飞书/);
  assert.doesNotMatch(appSource, /admin-(?:upload|publish|sync-live)/);
});

test("analysis overview is driven by each validated card, result, series and metric availability", () => {
  const riskAnalysis = authoritativeAnalysis(riskCard, riskResult);
  const baselineAnalysis = authoritativeAnalysis(baselineCard, baselineResult);
  const riskOverview = analysisOverview(riskCard, riskAnalysis, riskSeries);
  const baselineOverview = analysisOverview(baselineCard, baselineAnalysis, baselineSeries);

  assert.equal(riskOverview.object, `${riskCard.station_id} · ${riskCard.tool_id} · ${riskCard.fastening_point}`);
  assert.equal(riskOverview.question, "当前窗口是否相对基线出现规格内风险？");
  assert.match(riskOverview.scope, new RegExp(riskCard.analysis_provenance.analysis_stratum.program_id));
  assert.equal(
    riskOverview.window,
    `前 ${riskAnalysis.baselineCount} 条基线 vs 最近 ${riskAnalysis.recentCount} 条当前窗口`,
  );
  assert.ok(riskOverview.signalItems.some((item) => item.includes(`${riskAnalysis.meanShiftSigma.toFixed(2)}σ`)));
  assert.ok(riskOverview.signalItems.some((item) => item.includes(`已载入 ${riskSeries.length} 条`)));
  assert.ok(riskOverview.signalItems.some((item) => item.includes("工具电流：可用")));
  assert.equal(
    riskOverview.knowledgeItems.reduce((total, item) => total + Number(item.match(/：(\d+) 条$/)?.[1] || 0), 0),
    riskCard.evidence.length,
  );
  assert.match(riskOverview.output, new RegExp(String(riskAnalysis.score)));
  assert.match(baselineOverview.output, new RegExp(String(baselineAnalysis.score)));
  assert.notEqual(riskOverview.output, baselineOverview.output);
  assert.match(baselineOverview.progress.label, /保持稳定监控/);

  const unavailableCard = structuredClone(riskCard);
  unavailableCard.analysis_provenance.metric_availability.current_a.available = false;
  unavailableCard.analysis_provenance.metric_availability.current_a.reason = "源字段缺失";
  assert.ok(
    analysisOverview(unavailableCard, riskAnalysis, riskSeries).signalItems
      .some((item) => item === "工具电流：不可用（源字段缺失）"),
  );
});

test("administrator tools open in an independent modal drawer", () => {
  assert.match(appSource, /className = "admin-drawer"/);
  assert.match(appSource, /setAttribute\("role", "dialog"\)/);
  assert.match(appSource, /setAttribute\("aria-modal", "true"\)/);
  assert.match(appSource, /event\.key === "Escape"/);
  assert.match(appSource, /adminReturnFocus/);
  assert.doesNotMatch(appSource, /insertAdminNavLink/);
});
