import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const [admin, index, video] = await Promise.all([
  readFile(new URL("../docs/admin.html", import.meta.url), "utf8"),
  readFile(new URL("../docs/index.html", import.meta.url), "utf8"),
  readFile(new URL("../docs/video/index.html", import.meta.url), "utf8"),
]);

test("admin returns to the second-round demo, never the legacy index", () => {
  assert.match(admin, /href="\.\/round2\.html"[^>]*>打开第二轮公开演示/);
  assert.match(admin, /href="\.\/video\/index\.html"[^>]*>观看演示视频/);
  assert.doesNotMatch(admin, /href="\.\/index\.html"/);
});

test("legacy root entry redirects to the second-round demo", () => {
  assert.match(index, /http-equiv="refresh" content="0; url=\.\/round2\.html"/);
  assert.match(index, /window\.location\.replace\("\.\/round2\.html/);
});

test("video page has a playable source, metadata preload and Chinese captions", () => {
  assert.match(video, /<video[^>]+controls[^>]+preload="metadata"[^>]+playsinline/);
  assert.match(video, /poster="\.\/poster\.jpg"/);
  assert.match(video, /<source src="\.\/quality-guardian-v2\.mp4" type="video\/mp4"/);
  assert.match(video, /<track kind="subtitles" srclang="zh" label="中文字幕" src="\.\/quality-guardian-v2\.srt"/);
});
