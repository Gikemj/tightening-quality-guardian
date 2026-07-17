import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { analyzeSeries, pathFor } from "../docs/risk-engine.js";

const series = JSON.parse(
  await readFile(new URL("../docs/data/demo_series.json", import.meta.url), "utf8"),
);

test("risk replay detects the synthetic hidden-risk window", () => {
  const result = analyzeSeries(series, 60, 24);
  assert.equal(result.level, "high");
  assert.ok(result.score >= 75);
  assert.equal(result.inSpecRate, 1);
});

test("baseline replay does not create a high-risk card", () => {
  const result = analyzeSeries(series.slice(0, 84), 60, 24);
  assert.equal(result.level, "low");
  assert.ok(result.score < 45);
});

test("chart path contains one coordinate per value", () => {
  const values = [43, 48, 53];
  const path = pathFor(values, 100, 100, 43, 53);
  assert.match(path, /^M/);
  assert.equal((path.match(/[ML]/g) || []).length, values.length);
});
