"""Build public round-two assets from synthetic representative cases only."""

from __future__ import annotations

import json
from pathlib import Path

from torque_guard.artifacts import canonical_json_bytes
from torque_guard.round2 import CaseInput, RelationEvidenceAgent


ROOT = Path(__file__).resolve().parents[1]


def main(root: Path = ROOT, *, source_root: Path | None = None) -> None:
    """Render the public report into ``root`` from a checked-in synthetic source.

    ``build_all`` renders into an isolated directory.  The source is intentionally
    read from the repository rather than copied from any user-supplied workbook.
    """

    root = Path(root)
    source_root = Path(source_root or root)
    source = source_root / "docs" / "data" / "round2_cases.json"
    payload = json.loads(source.read_text(encoding="utf-8"))
    if payload.get("data_boundary") != "schema_and_relationship_reference_only":
        raise ValueError("第二轮公开资产的数据边界声明不正确")
    agent = RelationEvidenceAgent()
    reports = [agent.assess(CaseInput.from_mapping(item)).to_dict() for item in payload["cases"]]
    output = {
        "schema_version": "2.0",
        "data_boundary": payload["data_boundary"],
        "notice": payload["notice"],
        "source_contract": payload["source_contract"],
        "reports": reports,
    }
    destination = root / "docs" / "data" / "round2_reports.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(canonical_json_bytes(output))
    print(f"built round-two public assets -> {destination}")


if __name__ == "__main__":
    main()
