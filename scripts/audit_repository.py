from __future__ import annotations

import csv
import json
import re
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PageAudit(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: set[str] = set()
        self.local_anchors: list[str] = []
        self.h1_count = 0
        self.lang = ""
        self.has_viewport = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "html":
            self.lang = values.get("lang") or ""
        if values.get("id"):
            self.ids.add(str(values["id"]))
        if tag == "a" and str(values.get("href", "")).startswith("#"):
            self.local_anchors.append(str(values["href"])[1:])
        if tag == "h1":
            self.h1_count += 1
        if tag == "meta" and values.get("name") == "viewport":
            self.has_viewport = True


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    required = [
        ROOT / "README.md",
        ROOT / "assets" / "dashboard-preview.png",
        ROOT / "data" / "tightening_events_demo.csv",
        ROOT / "knowledge" / "ontology.json",
        ROOT / "outputs" / "risk_card.json",
        ROOT / "outputs" / "metrics.json",
        ROOT / "docs" / "index.html",
    ]
    require(all(path.exists() for path in required), "required repository artifact is missing")

    with (ROOT / "data" / "tightening_events_demo.csv").open(
        "r", encoding="utf-8-sig", newline=""
    ) as handle:
        rows = list(csv.DictReader(handle))
    manifest = json.loads((ROOT / "data" / "manifest.json").read_text(encoding="utf-8"))
    require(len(rows) == manifest["records"] == 820, "dataset row count does not match manifest")
    require(not manifest["seres_internal_data"], "demo must not claim SERES internal data")

    card = json.loads((ROOT / "outputs" / "risk_card.json").read_text(encoding="utf-8"))
    require(card["risk_level"] == "high", "primary demo must end in a high-risk review card")
    require(all(item["source"] and item["locator"] for item in card["evidence"]), "evidence is not traceable")
    require(all(item["approval_required"] for item in card["recommended_actions"]), "unsafe automatic action found")

    page_text = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
    page = PageAudit()
    page.feed(page_text)
    require(page.lang == "zh-CN", "page language must be zh-CN")
    require(page.has_viewport, "viewport meta is missing")
    require(page.h1_count == 1, "page must contain exactly one h1")
    require(all(anchor in page.ids for anchor in page.local_anchors if anchor), "broken in-page anchor")
    require("relationship-chain" in page.ids, "knowledge relationship chain is missing")
    require("质控前哨" in page_text, "public project name must match the submission")

    subgraph = json.loads((ROOT / "docs" / "data" / "subgraph.json").read_text(encoding="utf-8"))
    relations = {edge["relation"] for edge in subgraph["edges"]}
    require(
        {"has_equipment", "executes", "controls", "may_cause", "affects", "verifies"} <= relations,
        "subgraph is missing a required reasoning relation",
    )

    public_text = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md")), ROOT / "docs" / "index.html"]
    )
    require("TODO" not in public_text and "TBD" not in public_text, "unfinished placeholder found")
    secret_patterns = [r"gho_[A-Za-z0-9]+", r"sk-[A-Za-z0-9]{16,}", r"lark_oapi_[A-Za-z0-9]+"]
    require(not any(re.search(pattern, public_text) for pattern in secret_patterns), "credential-like text found")

    print("repository audit passed: data, evidence, safety, page structure, links, and secret scan")


if __name__ == "__main__":
    main()
