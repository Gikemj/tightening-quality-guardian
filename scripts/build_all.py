from __future__ import annotations

import tempfile
from pathlib import Path

from torque_guard.artifacts import commit_staged_files
from build_demo_assets import main as build_demo_assets
from evaluate import main as evaluate_rolling_windows
from evaluate_scenarios import main as evaluate_independent_scenarios
from generate_demo_data import main as generate_demo_data
from build_round2_assets import main as build_round2_assets


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_PUBLIC_ARTIFACTS = {
    Path("data/manifest.json"),
    Path("data/tightening_events_demo.csv"),
    Path("outputs/feishu_records_preview.json"),
    Path("outputs/metrics.json"),
    Path("outputs/risk_card.json"),
    Path("outputs/scenario_metrics.json"),
    Path("docs/data/baseline_card.json"),
    Path("docs/data/baseline_result.json"),
    Path("docs/data/baseline_series.json"),
    Path("docs/data/demo_series.json"),
    Path("docs/data/feishu_records_preview.json"),
    Path("docs/data/metrics.json"),
    Path("docs/data/risk_card.json"),
    Path("docs/data/risk_result.json"),
    Path("docs/data/scenario_metrics.json"),
    Path("docs/data/subgraph.json"),
    Path("docs/data/round2_reports.json"),
}


def _public_files(root: Path) -> set[Path]:
    files: set[Path] = set()
    for directory in (root / "data", root / "outputs", root / "docs" / "data"):
        if directory.exists():
            files.update(path.relative_to(root) for path in directory.rglob("*") if path.is_file())
    return files


def main() -> None:
    """Build in isolation, validate completeness, then commit as one recoverable batch."""

    with tempfile.TemporaryDirectory(prefix=".public-build-", dir=ROOT) as temporary:
        staged_root = Path(temporary)
        generate_demo_data(staged_root)
        build_demo_assets(staged_root)
        evaluate_rolling_windows(staged_root)
        evaluate_independent_scenarios(staged_root)
        build_round2_assets(staged_root, source_root=ROOT)

        staged_files = _public_files(staged_root)
        if staged_files != EXPECTED_PUBLIC_ARTIFACTS:
            missing = sorted(str(path) for path in EXPECTED_PUBLIC_ARTIFACTS - staged_files)
            unexpected = sorted(str(path) for path in staged_files - EXPECTED_PUBLIC_ARTIFACTS)
            raise RuntimeError(
                f"public artifact set is incomplete; missing={missing}, unexpected={unexpected}"
            )
        commit_staged_files(staged_root, ROOT, EXPECTED_PUBLIC_ARTIFACTS)
    print("all demo and evaluation assets rebuilt")


if __name__ == "__main__":
    main()
