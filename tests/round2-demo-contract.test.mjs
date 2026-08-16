import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const html = await readFile(new URL("../docs/round2.html", import.meta.url), "utf8");
const script = await readFile(new URL("../docs/round2.js", import.meta.url), "utf8");

test("round-two public demo exposes callable case actions", () => {
  for (const id of ["case-select", "run-case", "copy-dossier", "retry-data", "model-toggle", "task-preview"]) {
    assert.match(html, new RegExp(`id=\\"${id}\\"`));
  }
  for (const method of ["selectCase", "generateTaskPreview", "getModelPayload", "getDossierText", "reload"]) {
    assert.match(script, new RegExp(`\\b${method}\\b`));
  }
  assert.match(script, /schema_and_relationship_reference_only/);
  assert.match(script, /公开演示没有发起飞书写入/);
});

test("public video and subtitle assets are linked from the page", () => {
  assert.match(html, /video\/quality-guardian-v2\.mp4/);
});
