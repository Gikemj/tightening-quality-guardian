from __future__ import annotations

import ast
import csv
import json
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

from torque_guard.artifacts import canonical_json_bytes


ROOT = Path(__file__).resolve().parents[1]


class PageAudit(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: set[str] = set()
        self.duplicate_ids: set[str] = set()
        self.local_anchors: list[str] = []
        self.local_resources: list[str] = []
        self.h1_count = 0
        self.lang = ""
        self.has_viewport = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "html":
            self.lang = values.get("lang") or ""
        if values.get("id"):
            element_id = str(values["id"])
            if element_id in self.ids:
                self.duplicate_ids.add(element_id)
            self.ids.add(element_id)
        if tag == "a" and str(values.get("href", "")).startswith("#"):
            self.local_anchors.append(unquote(urlsplit(str(values["href"])).fragment))
        for attribute in ("href", "src"):
            reference = str(values.get(attribute, "")).strip()
            if reference and not reference.startswith("#"):
                try:
                    parsed = urlsplit(reference)
                except ValueError:
                    self.local_resources.append(reference)
                else:
                    if not parsed.scheme and not parsed.netloc:
                        self.local_resources.append(reference)
        if tag == "h1":
            self.h1_count += 1
        if tag == "meta" and values.get("name") == "viewport":
            self.has_viewport = True


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


MARKDOWN_LINK = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
JAVASCRIPT_RESOURCE = re.compile(
    r'''(?:\bfrom\s+|\bimport\s*(?:\(\s*)?|\bfetch\s*\(\s*)["']([^"']+)["']'''
)
CSS_RESOURCE = re.compile(
    r'''url\(\s*(?:"([^"]+)"|'([^']+)'|([^)'"\s]+))\s*\)''',
    re.IGNORECASE,
)


def _decode_url_path(value: str) -> str:
    decoded = value
    for _ in range(5):
        previous = decoded
        decoded = unquote(decoded)
        if decoded == previous:
            return decoded
    if unquote(decoded) != decoded:
        raise ValueError("URL path has excessive nested encoding")
    return decoded


def resolve_local_reference(document: Path, reference: str, allowed_root: Path) -> Path | None:
    """Resolve a local URL safely inside its publication boundary."""

    try:
        parsed = urlsplit(reference)
    except ValueError as error:
        raise ValueError("invalid URL") from error
    if parsed.scheme or parsed.netloc:
        return None

    decoded_path = _decode_url_path(parsed.path)
    if "\x00" in decoded_path:
        raise ValueError("URL path contains a null byte")
    decoded_path = decoded_path.replace("\\", "/")
    if decoded_path.startswith("//"):
        raise ValueError("local URL escapes via a scheme-relative path")
    allowed_root = allowed_root.resolve()
    if not decoded_path:
        candidate = document
    elif decoded_path.startswith("/"):
        candidate = allowed_root / decoded_path.lstrip("/")
    else:
        candidate = document.parent / decoded_path
    resolved = candidate.resolve()
    try:
        resolved.relative_to(allowed_root)
    except ValueError as error:
        raise ValueError("local URL escapes its publication root") from error
    return resolved


def require_deploy_resources(document: Path, references: list[str], deploy_root: Path) -> None:
    broken: list[str] = []
    pending = [(document, reference) for reference in references]
    scanned: set[Path] = set()
    while pending:
        source, reference = pending.pop()
        try:
            target = resolve_local_reference(source, reference, deploy_root)
        except ValueError as error:
            broken.append(f"{source.name} -> {reference} ({error})")
            continue
        if target is not None and not target.is_file():
            broken.append(f"{source.name} -> {reference}")
            continue
        if target is None or target in scanned:
            continue
        scanned.add(target)
        if target.suffix.lower() == ".js":
            text = target.read_text(encoding="utf-8")
            pending.extend((target, match.group(1)) for match in JAVASCRIPT_RESOURCE.finditer(text))
        elif target.suffix.lower() == ".css":
            text = target.read_text(encoding="utf-8")
            pending.extend(
                (target, next(value for value in match.groups() if value is not None))
                for match in CSS_RESOURCE.finditer(text)
            )
    require(not broken, "broken or unsafe deployed local resources: " + "; ".join(broken[:10]))


def require_local_links(markdown_files: list[Path]) -> None:
    broken: list[str] = []
    for document in markdown_files:
        text = document.read_text(encoding="utf-8")
        for match in MARKDOWN_LINK.finditer(text):
            raw_target = match.group(1).strip()
            if raw_target.startswith("<") and ">" in raw_target:
                raw_target = raw_target[1 : raw_target.index(">")]
            else:
                raw_target = raw_target.split(maxsplit=1)[0]
            if not raw_target or raw_target.startswith("#"):
                continue
            try:
                target = resolve_local_reference(document, raw_target, ROOT)
            except ValueError as error:
                broken.append(f"{document.relative_to(ROOT)} -> {raw_target} ({error})")
                continue
            if target is not None and not target.exists():
                broken.append(f"{document.relative_to(ROOT)} -> {raw_target}")
    require(not broken, "broken Markdown local links: " + "; ".join(broken[:10]))


def declared_string_constant(path: Path, name: str) -> str:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for statement in tree.body:
        if isinstance(statement, ast.Assign):
            names = [target.id for target in statement.targets if isinstance(target, ast.Name)]
            if name in names and isinstance(statement.value, ast.Constant):
                value = statement.value.value
                if isinstance(value, str):
                    return value
        if (
            isinstance(statement, ast.AnnAssign)
            and isinstance(statement.target, ast.Name)
            and statement.target.id == name
            and isinstance(statement.value, ast.Constant)
            and isinstance(statement.value.value, str)
        ):
            return statement.value.value
    raise AssertionError(f"{name} is not a literal string in {path.relative_to(ROOT)}")


def require_canonical_generated_json() -> None:
    invalid: list[str] = []
    json_files = sorted(
        [*(ROOT / "data").glob("*.json"), *(ROOT / "outputs").glob("*.json"), *(ROOT / "docs" / "data").glob("*.json")]
    )
    for path in json_files:
        raw = path.read_bytes()
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            invalid.append(f"{path.relative_to(ROOT)} (invalid UTF-8 JSON)")
            continue
        try:
            canonical = canonical_json_bytes(payload)
        except (TypeError, ValueError):
            invalid.append(f"{path.relative_to(ROOT)} (non-canonical JSON value)")
            continue
        if raw != canonical:
            invalid.append(str(path.relative_to(ROOT)))
    require(
        not invalid,
        "generated JSON must use canonical UTF-8/LF formatting: " + "; ".join(invalid[:10]),
    )


PUBLIC_TEXT_SUFFIXES = {
    ".css",
    ".csv",
    ".env",
    ".example",
    ".html",
    ".js",
    ".json",
    ".md",
    ".mjs",
    ".py",
    ".svg",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}


def public_text_files() -> list[Path]:
    files: set[Path] = set()
    publication_roots = [
        ROOT / "assets",
        ROOT / "data",
        ROOT / "knowledge",
        ROOT / "outputs",
        ROOT / "docs",
        ROOT / ".github" / "workflows",
    ]
    for directory in publication_roots:
        files.update(
            path
            for path in directory.rglob("*")
            if path.is_file() and "__pycache__" not in path.parts
        )

    source_roots = [
        ROOT / "src",
        ROOT / "scripts",
    ]
    for directory in source_roots:
        files.update(
            path
            for path in directory.rglob("*")
            if path.is_file()
            and "__pycache__" not in path.parts
            and path.suffix.lower() in PUBLIC_TEXT_SUFFIXES
        )
    for path in [
        ROOT / "README.md",
        ROOT / "CONTRIBUTING.md",
        ROOT / "PRODUCT.md",
        ROOT / "DESIGN.md",
        ROOT / "Makefile",
        ROOT / "package.json",
        ROOT / "pyproject.toml",
        ROOT / ".env.example",
        ROOT / ".gitignore",
        ROOT / ".gitattributes",
    ]:
        if path.is_file():
            files.add(path)
    return sorted(files)


SECRET_PATTERNS = {
    "GitHub token": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    "model/API key": re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{16,}\b"),
    "Slack token": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{16,}\b"),
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "Feishu token": re.compile(r"\b(?:lark_oapi_|t-[A-Za-z0-9_-]{20,})[A-Za-z0-9_-]+\b"),
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "local user path": re.compile(
        r"(?:[A-Za-z]:\\Users\\[^\\\s]+|/" + r"home/[^/\s]+)"
    ),
}


def require_no_sensitive_public_text() -> None:
    findings: list[str] = []
    for path in public_text_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        for label, pattern in SECRET_PATTERNS.items():
            if pattern.search(text):
                findings.append(f"{path.relative_to(ROOT)} ({label})")
        if path.suffix.lower() == ".json":
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                continue
            pending: list[object] = [payload]
            while pending:
                item = pending.pop()
                if isinstance(item, dict):
                    for key, value in item.items():
                        if (
                            re.fullmatch(
                                r"(?i)(?:app_?secret|api_?key|access_?token|tenant_access_token|password|authorization)",
                                str(key),
                            )
                            and value not in (None, "", [], {})
                        ):
                            findings.append(
                                f"{path.relative_to(ROOT)} (populated sensitive field: {key})"
                            )
                        pending.append(value)
                elif isinstance(item, list):
                    pending.extend(item)
    require(
        not findings,
        "credential-like or machine-local text found in public assets: "
        + "; ".join(findings[:10]),
    )


def require_public_trace(card: dict, scope: str) -> None:
    trace = card.get("agent_trace", [])
    require(len(trace) >= 4, f"{scope} public audit trail is incomplete")
    expected_ids = [
        f"CALL-PUBLIC-{scope.upper()}-{index:03d}"
        for index in range(1, len(trace) + 1)
    ]
    require(
        [item.get("call_id") for item in trace] == expected_ids,
        f"{scope} public call IDs are not deterministic",
    )
    require(
        all(item.get("trace_mode") == "deterministic_public_build" for item in trace),
        f"{scope} trace is not marked as a deterministic public build",
    )
    require(
        all(
            item.get("started_at") == "2026-07-20T00:00:00Z"
            and item.get("completed_at") == "2026-07-20T00:00:00Z"
            and item.get("duration_ms") == 0
            for item in trace
        ),
        f"{scope} public trace contains runtime timing values",
    )
    serialized = json.dumps(trace, ensure_ascii=False)
    require(
        re.search(r"[A-Za-z]:\\\\", serialized) is None,
        f"{scope} public trace contains a Windows absolute path",
    )


def main() -> None:
    required = [
        ROOT / "README.md",
        ROOT / "assets" / "dashboard-preview.png",
        ROOT / "data" / "tightening_events_demo.csv",
        ROOT / "knowledge" / "ontology.json",
        ROOT / "outputs" / "risk_card.json",
        ROOT / "outputs" / "metrics.json",
        ROOT / "outputs" / "scenario_metrics.json",
        ROOT / "docs" / "index.html",
        ROOT / "docs" / "40-strong-submission.md",
        ROOT / "docs" / "business-value.md",
        ROOT / "docs" / "pilot-plan.md",
        ROOT / "docs" / "round2.html",
        ROOT / "docs" / "round2.css",
        ROOT / "docs" / "round2.js",
        ROOT / "docs" / "data-boundary-round2.html",
        ROOT / "docs" / "data" / "round2_cases.json",
        ROOT / "docs" / "data" / "round2_reports.json",
        ROOT / ".env.example",
        ROOT / ".gitattributes",
        ROOT / ".github" / "workflows" / "ci.yml",
        ROOT / ".github" / "workflows" / "pages.yml",
    ]
    require(all(path.exists() for path in required), "required repository artifact is missing")
    require_canonical_generated_json()

    round2_source = json.loads(
        (ROOT / "docs" / "data" / "round2_cases.json").read_text(encoding="utf-8")
    )
    round2_reports = json.loads(
        (ROOT / "docs" / "data" / "round2_reports.json").read_text(encoding="utf-8")
    )
    require(
        round2_source.get("data_boundary") == "schema_and_relationship_reference_only"
        and round2_reports.get("data_boundary") == "schema_and_relationship_reference_only",
        "round-two public assets must declare the relationship-reference data boundary",
    )
    require(
        all(str(item.get("case_id", "")).startswith("CASE-DEMO-") for item in round2_source.get("cases", [])),
        "round-two public cases must be explicitly synthetic CASE-DEMO records",
    )
    require(
        len(round2_reports.get("reports", [])) == len(round2_source.get("cases", [])) >= 3,
        "round-two report coverage is incomplete",
    )
    for report in round2_reports["reports"]:
        facts = report.get("facts", [])
        gaps = report.get("gaps", [])
        task_ids = {task.get("task_id") for task in report.get("tasks", [])}
        evidence_ids = {item.get("evidence_id") for item in [*facts, *gaps]}
        require(
            all(task.get("approval_required") is True for task in report.get("tasks", []))
            and all(set(task.get("evidence_ids", [])) <= evidence_ids for task in report.get("tasks", [])),
            "round-two tasks must retain an approval gate and concrete evidence links",
        )
        if report.get("disposition") == "complete_case_before_reasoning":
            require(
                "根因" not in json.dumps(
                    {"facts": facts, "tasks": report.get("tasks", [])}, ensure_ascii=False
                )
                and bool(task_ids),
                "incomplete relationship cases must request completion, not infer a root cause",
            )

    gitignore_entries = {
        line.strip()
        for line in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    require(
        ".local/" in gitignore_entries,
        "private live-integration artifact directory must be git-ignored",
    )
    for constant_name in ("PRIVATE_LIVE_RISK_OUTPUT", "PRIVATE_LIVE_FEISHU_OUTPUT"):
        private_output = Path(
            declared_string_constant(
                ROOT / "src" / "torque_guard" / "cli.py", constant_name
            )
        )
        require(
            not private_output.is_absolute()
            and private_output.parts[:2] == (".local", "live"),
            f"{constant_name} must default inside the git-ignored .local/live directory",
        )

    with (ROOT / "data" / "tightening_events_demo.csv").open(
        "r", encoding="utf-8-sig", newline=""
    ) as handle:
        rows = list(csv.DictReader(handle))
    manifest = json.loads((ROOT / "data" / "manifest.json").read_text(encoding="utf-8"))
    require(len(rows) == manifest["records"] == 820, "dataset row count does not match manifest")
    require(not manifest["seres_internal_data"], "demo must not claim SERES internal data")

    card = json.loads((ROOT / "outputs" / "risk_card.json").read_text(encoding="utf-8"))
    require(card.get("schema_version") == "1.0", "risk-card schema version is missing")
    provenance = card.get("analysis_provenance", {})
    declared_policy_version = declared_string_constant(
        ROOT / "src" / "torque_guard" / "risk.py", "RISK_POLICY_VERSION"
    )
    require(
        provenance.get("generated_by") == "torque_guard.risk.RiskAnalyzer"
        and provenance.get("risk_policy_version") == declared_policy_version
        and re.fullmatch(r"risk-policy-[1-9][0-9]*\.[0-9]+", declared_policy_version)
        and str(provenance.get("knowledge_revision", "")).startswith("sha256:")
        and str(provenance.get("input_window_revision", "")).startswith("sha256:"),
        "risk-card policy, knowledge, or input revision is missing",
    )
    require(card["risk_level"] == "high", "primary demo must end in a high-risk review card")
    require(all(item["source"] and item["locator"] for item in card["evidence"]), "evidence is not traceable")
    measurement_sources = {
        item["source"]
        for item in card["evidence"]
        if item.get("category") in {"spc", "equipment"}
    }
    require(
        measurement_sources == {"data/tightening_events_demo.csv"},
        "measurement evidence does not identify the actual public input source",
    )
    require(all(item["approval_required"] for item in card["recommended_actions"]), "unsafe automatic action found")
    evidence_ids = {item["evidence_id"] for item in card["evidence"]}
    candidate_names = {item["cause"] for item in card["candidate_causes"]}
    require(
        all(
            item.get("why")
            and item.get("evidence_ids")
            and set(item["evidence_ids"]) <= evidence_ids
            and item.get("candidate_causes")
            and set(item["candidate_causes"]) <= candidate_names
            for item in card["recommended_actions"]
        ),
        "task explanation, evidence, or candidate-cause linkage is incomplete",
    )
    reasoning = card.get("reasoning", {})
    require(reasoning.get("decision") in {"supported", "refused"}, "controlled reasoning decision is missing")
    if reasoning.get("decision") == "supported":
        cited = {
            evidence_id
            for hypothesis in reasoning.get("hypotheses", [])
            for evidence_id in hypothesis.get("evidence_ids", [])
        }
        require(bool(cited) and cited <= evidence_ids, "reasoning contains missing or unknown citations")
    trace = card.get("agent_trace", [])
    require(len(trace) >= 4, "real tool audit trail is incomplete")
    require(all(item.get("status") == "succeeded" for item in trace), "primary demo contains a failed tool call")
    require_public_trace(card, "risk")
    workflow = card.get("workflow", {})
    require(workflow.get("human_approval_required") is True, "workflow human approval gate is missing")
    require(workflow.get("automatic_stop_line_allowed") is False, "workflow permits unsafe automatic action")

    scenario_metrics = json.loads(
        (ROOT / "outputs" / "scenario_metrics.json").read_text(encoding="utf-8")
    )
    require(scenario_metrics.get("cases") == 120, "independent scenario suite must contain 120 cases")
    require(
        set(scenario_metrics.get("scenarios", {}))
        == {"normal", "hidden_torque_drift", "sensor_zero_drift", "repeated_alarm"},
        "independent scenario coverage is incomplete",
    )
    require(
        "not factory evidence" in scenario_metrics.get("evaluation_scope", ""),
        "synthetic scenario metrics must not look like factory evidence",
    )

    risk_result = json.loads((ROOT / "docs" / "data" / "risk_result.json").read_text(encoding="utf-8"))
    baseline_card = json.loads((ROOT / "docs" / "data" / "baseline_card.json").read_text(encoding="utf-8"))
    baseline_result = json.loads((ROOT / "docs" / "data" / "baseline_result.json").read_text(encoding="utf-8"))
    require(
        risk_result["risk_score"] == card["risk_score"],
        "web risk score differs from Python risk card",
    )
    require(
        baseline_result["risk_score"] == baseline_card["risk_score"],
        "web baseline score differs from Python baseline card",
    )
    comparison_keys = {
        "metric_id",
        "label",
        "baseline",
        "current",
        "delta",
        "unit",
        "status",
        "rule_ids",
        "evidence_ids",
    }
    expected_comparisons = {
        "torque_mean_nm",
        "angle_dispersion_ratio",
        "retry_mean",
        "in_spec_rate",
    }
    for result, result_card in (
        (risk_result, card),
        (baseline_result, baseline_card),
    ):
        comparisons = result.get("comparisons", [])
        result_evidence = {item["evidence_id"] for item in result_card["evidence"]}
        require(
            {item.get("metric_id") for item in comparisons} == expected_comparisons,
            "authoritative result comparison coverage is incomplete",
        )
        require(
            all(
                set(item) == comparison_keys
                and item["status"] in {"normal", "triggered"}
                and item["evidence_ids"]
                and set(item["evidence_ids"]) <= result_evidence
                and all(
                    isinstance(item[key], (int, float))
                    and not isinstance(item[key], bool)
                    for key in ("baseline", "current", "delta")
                )
                for item in comparisons
            ),
            "authoritative result comparison contract is invalid",
        )
    require_public_trace(baseline_card, "baseline")
    require(
        trace[0].get("input_summary", {}).get("file") == "data/tightening_events_demo.csv",
        "public risk trace source must be repository-relative",
    )

    page_text = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
    page = PageAudit()
    page.feed(page_text)
    require(page.lang == "zh-CN", "page language must be zh-CN")
    require(page.has_viewport, "viewport meta is missing")
    require(page.h1_count == 1, "page must contain exactly one h1")
    require(not page.duplicate_ids, f"duplicate HTML ids found: {sorted(page.duplicate_ids)}")
    require(all(anchor in page.ids for anchor in page.local_anchors if anchor), "broken in-page anchor")
    require_deploy_resources(ROOT / "docs" / "index.html", page.local_resources, ROOT / "docs")
    require("relationship-chain" in page.ids, "knowledge relationship chain is missing")
    require("质控前哨" in page_text, "public project name must match the submission")

    subgraph = json.loads((ROOT / "docs" / "data" / "subgraph.json").read_text(encoding="utf-8"))
    relations = {edge["relation"] for edge in subgraph["edges"]}
    require(
        {"has_equipment", "executes", "controls", "may_cause", "affects", "verifies"} <= relations,
        "subgraph is missing a required reasoning relation",
    )

    markdown_files = [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]
    require_local_links(markdown_files)
    public_text = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in [*markdown_files, ROOT / "docs" / "index.html"]
    )
    require("TODO" not in public_text and "TBD" not in public_text, "unfinished placeholder found")
    require_no_sensitive_public_text()

    ci_text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    for required_gate in [
        'python-version: ["3.10", "3.12"]',
        "python scripts/build_all.py",
        "python -m unittest discover -s tests -v",
        "node --test tests/web-engine.test.mjs",
        "node --check docs/risk-engine.js",
        "node --check docs/app.js",
        "python scripts/audit_repository.py",
        "git diff --exit-code -- data docs/data outputs",
    ]:
        require(required_gate in ci_text, f"CI gate is missing: {required_gate}")

    pages_text = (ROOT / ".github" / "workflows" / "pages.yml").read_text(encoding="utf-8")
    require("workflow_run:" in pages_text, "Pages deployment must depend on the Verify workflow")
    require(
        "github.event.workflow_run.conclusion == 'success'" in pages_text,
        "Pages deployment can run after a failed Verify workflow",
    )
    require(
        "python scripts/build_all.py" in pages_text,
        "Pages must rebuild the complete public artifact set",
    )

    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
    populated_example_keys = [
        line.split("=", 1)[0]
        for line in env_example.splitlines()
        if line.strip() and not line.lstrip().startswith("#") and "=" in line
        and line.split("=", 1)[1].strip()
    ]
    require(
        not populated_example_keys,
        f".env.example must not contain credential values: {populated_example_keys}",
    )

    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    require(
        package.get("scripts", {}).get("build") == "python scripts/build_all.py",
        "npm build must use the complete isolated build_all entrypoint",
    )
    require(
        package.get("engines", {}).get("node") == ">=20",
        "package.json must declare the verified Node.js >=20 baseline",
    )
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    require(
        "PYTHONPATH=src $(PYTHON) scripts/build_all.py" in makefile
        and "PYTHONPATH=src $(PYTHON) -m unittest" in makefile,
        "Makefile must work from a fresh clone without relying on editable install",
    )

    print("repository audit passed: data, evidence, safety, page structure, links, and secret scan")


if __name__ == "__main__":
    main()
