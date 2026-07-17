from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class KnowledgeBundle:
    control_plan: dict[str, str]
    pfmea: dict[str, str]
    historical_cases: list[dict[str, Any]]
    subgraph: dict[str, Any]


class KnowledgeBase:
    """Small, inspectable graph-retrieval layer for the competition MVP."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.control_plans = self._read_csv(self.root / "control_plan_demo.csv")
        self.pfmea_rows = self._read_csv(self.root / "pfmea_demo.csv")
        self.historical_cases = self._read_json(self.root / "historical_cases.json")
        self.ontology = self._read_json(self.root / "ontology.json")

    @staticmethod
    def _read_csv(path: Path) -> list[dict[str, str]]:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))

    @staticmethod
    def _read_json(path: Path) -> Any:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def retrieve(self, fastening_point: str) -> KnowledgeBundle:
        control = next(row for row in self.control_plans if row["fastening_point"] == fastening_point)
        pfmea = next(row for row in self.pfmea_rows if row["fastening_point"] == fastening_point)
        cause_ids = {item.strip() for item in pfmea["cause_ids"].split(";") if item.strip()}
        cases = [case for case in self.historical_cases if cause_ids.intersection(case["cause_ids"])]

        node_ids = {fastening_point, control["tool_id"], pfmea["failure_mode_id"], *cause_ids}
        edges = [
            edge
            for edge in self.ontology["edges"]
            if edge["source"] in node_ids or edge["target"] in node_ids
        ]
        connected = node_ids.union({edge["source"] for edge in edges}, {edge["target"] for edge in edges})
        nodes = [node for node in self.ontology["nodes"] if node["id"] in connected]
        return KnowledgeBundle(control, pfmea, cases, {"nodes": nodes, "edges": edges})
