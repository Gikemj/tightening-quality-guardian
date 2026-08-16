from __future__ import annotations

import csv
import hashlib
import io
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


SOURCE_FILES = (
    "control_plan_demo.csv",
    "pfmea_demo.csv",
    "historical_cases.json",
    "ontology.json",
    "alarm_dictionary_demo.csv",
)

CONTROL_COLUMNS = {
    "control_plan_id",
    "fastening_point",
    "tool_id",
    "quality_characteristic",
    "torque_target_nm",
    "torque_lsl_nm",
    "torque_usl_nm",
    "angle_target_deg",
    "angle_lsl_deg",
    "angle_usl_deg",
    "classification",
    "sample_strategy",
    "reaction_plan",
}
PFMEA_COLUMNS = {
    "pfmea_id",
    "fastening_point",
    "failure_mode_id",
    "failure_mode",
    "effect",
    "severity",
    "cause_ids",
    "current_controls",
}
ALARM_COLUMNS = {
    "alarm_code",
    "source",
    "meaning",
    "quality_link",
    "recommended_check",
}


@dataclass(frozen=True)
class KnowledgeBundle:
    control_plan: dict[str, Any]
    pfmea: dict[str, Any]
    historical_cases: list[dict[str, Any]]
    subgraph: dict[str, Any]
    alarm_dictionary: dict[str, dict[str, Any]]


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _required_text(row: dict[str, Any], key: str, *, source: str, row_number: int) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{source} 第 {row_number} 行字段 {key!r} 不能为空")
    return value.strip()


def _finite_float(value: Any, *, source: str, row_number: int, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{source} 第 {row_number} 行字段 {field!r} 不是有效数字")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{source} 第 {row_number} 行字段 {field!r} 不是有效数字：{value!r}"
        ) from exc
    if not math.isfinite(result):
        raise ValueError(f"{source} 第 {row_number} 行字段 {field!r} 必须是有限数字")
    return 0.0 if result == 0 else result


def _strict_integer(value: Any, *, source: str, row_number: int, field: str) -> int:
    result = _finite_float(value, source=source, row_number=row_number, field=field)
    if not result.is_integer():
        raise ValueError(f"{source} 第 {row_number} 行字段 {field!r} 必须是整数")
    return int(result)


class KnowledgeBase:
    """Validated, local-only graph retrieval with semantic revisions.

    Each file is read exactly once.  The same in-memory parsed values are used
    both by retrieval and by the revision digest, avoiding a read/hash race.
    The digest is based on canonical data rather than transport bytes, so a BOM
    or LF/CRLF conversion does not create a false knowledge revision.
    """

    def __init__(self, root: str | Path):
        self.root = Path(root)
        raw_sources = self._read_sources_once()

        control_rows = self._parse_csv(
            "control_plan_demo.csv", raw_sources["control_plan_demo.csv"], CONTROL_COLUMNS
        )
        pfmea_rows = self._parse_csv(
            "pfmea_demo.csv", raw_sources["pfmea_demo.csv"], PFMEA_COLUMNS
        )
        alarm_rows = self._parse_csv(
            "alarm_dictionary_demo.csv",
            raw_sources["alarm_dictionary_demo.csv"],
            ALARM_COLUMNS,
        )
        historical = self._parse_json(
            "historical_cases.json", raw_sources["historical_cases.json"]
        )
        ontology = self._parse_json("ontology.json", raw_sources["ontology.json"])

        self.control_plans = self._normalize_control_plans(control_rows)
        self.pfmea_rows = self._normalize_pfmea(pfmea_rows)
        self.historical_cases = self._normalize_history(historical)
        self.ontology = self._normalize_ontology(ontology)
        normalized_alarms = self._normalize_alarms(alarm_rows)
        self._validate_references(normalized_alarms)

        self._control_by_point = {
            row["fastening_point"]: row for row in self.control_plans
        }
        self._pfmea_by_point = {row["fastening_point"]: row for row in self.pfmea_rows}
        self.alarm_dictionary = {row["alarm_code"]: row for row in normalized_alarms}
        self._graph_points = {
            node["id"]
            for node in self.ontology["nodes"]
            if node["type"] == "FasteningPoint"
        }

        semantic_sources = {
            "alarm_dictionary_demo.csv": normalized_alarms,
            "control_plan_demo.csv": self.control_plans,
            "historical_cases.json": self.historical_cases,
            "ontology.json": self.ontology,
            "pfmea_demo.csv": self.pfmea_rows,
        }
        digest = hashlib.sha256(_canonical_json_bytes(semantic_sources)).hexdigest()
        self.revision = f"sha256:{digest}"

    def _read_sources_once(self) -> dict[str, bytes]:
        sources: dict[str, bytes] = {}
        for name in SOURCE_FILES:
            path = self.root / name
            try:
                sources[name] = path.read_bytes()
            except FileNotFoundError as exc:
                raise ValueError(f"知识库缺少必需文件：{path}") from exc
            except OSError as exc:
                raise ValueError(f"无法读取知识文件 {path}：{exc}") from exc
        return sources

    @staticmethod
    def _decode(name: str, content: bytes) -> str:
        try:
            return content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError(f"知识文件 {name} 必须使用 UTF-8 编码") from exc

    @classmethod
    def _parse_csv(
        cls,
        name: str,
        content: bytes,
        required_columns: set[str],
    ) -> list[dict[str, str]]:
        reader = csv.DictReader(io.StringIO(cls._decode(name, content), newline=""))
        headers = reader.fieldnames
        if not headers:
            raise ValueError(f"知识文件 {name} 缺少表头")
        if len(headers) != len(set(headers)):
            duplicates = sorted(key for key in set(headers) if headers.count(key) > 1)
            raise ValueError(f"知识文件 {name} 存在重复列：{', '.join(duplicates)}")
        missing = required_columns - set(headers)
        if missing:
            raise ValueError(f"知识文件 {name} 缺少列：{', '.join(sorted(missing))}")
        rows: list[dict[str, str]] = []
        for row_number, raw in enumerate(reader, start=2):
            if None in raw:
                raise ValueError(f"知识文件 {name} 第 {row_number} 行列数多于表头")
            row = {
                str(key).strip(): ("" if value is None else value.strip())
                for key, value in raw.items()
            }
            if not any(row.values()):
                continue
            rows.append(row)
        if not rows:
            raise ValueError(f"知识文件 {name} 没有数据行")
        return rows

    @classmethod
    def _parse_json(cls, name: str, content: bytes) -> Any:
        try:
            return json.loads(cls._decode(name, content))
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"知识文件 {name} JSON 非法：第 {exc.lineno} 行第 {exc.colno} 列"
            ) from exc

    @staticmethod
    def _assert_unique(
        rows: Iterable[dict[str, Any]],
        key: str,
        *,
        source: str,
    ) -> None:
        values = [row[key] for row in rows]
        duplicates = sorted(value for value in set(values) if values.count(value) > 1)
        if duplicates:
            raise ValueError(f"{source} 字段 {key!r} 必须唯一，重复值：{', '.join(duplicates)}")

    @classmethod
    def _normalize_control_plans(
        cls, rows: list[dict[str, str]]
    ) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        numeric_fields = (
            "torque_target_nm",
            "torque_lsl_nm",
            "torque_usl_nm",
            "angle_target_deg",
            "angle_lsl_deg",
            "angle_usl_deg",
        )
        for row_number, source_row in enumerate(rows, start=2):
            row: dict[str, Any] = dict(source_row)
            for field in CONTROL_COLUMNS - set(numeric_fields):
                row[field] = _required_text(
                    source_row, field, source="control_plan_demo.csv", row_number=row_number
                )
            for field in numeric_fields:
                row[field] = _finite_float(
                    source_row.get(field),
                    source="control_plan_demo.csv",
                    row_number=row_number,
                    field=field,
                )
            if not (
                0 <= row["torque_lsl_nm"]
                < row["torque_target_nm"]
                < row["torque_usl_nm"]
                <= 10000
            ):
                raise ValueError(
                    f"control_plan_demo.csv 第 {row_number} 行扭矩上下限/目标值不合理"
                )
            if not (
                0 <= row["angle_lsl_deg"]
                < row["angle_target_deg"]
                < row["angle_usl_deg"]
                <= 36000
            ):
                raise ValueError(
                    f"control_plan_demo.csv 第 {row_number} 行角度上下限/目标值不合理"
                )
            normalized.append(row)
        cls._assert_unique(normalized, "control_plan_id", source="control_plan_demo.csv")
        cls._assert_unique(normalized, "fastening_point", source="control_plan_demo.csv")
        return sorted(normalized, key=lambda item: item["control_plan_id"])

    @classmethod
    def _normalize_pfmea(cls, rows: list[dict[str, str]]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for row_number, source_row in enumerate(rows, start=2):
            row: dict[str, Any] = dict(source_row)
            for field in PFMEA_COLUMNS - {"severity", "cause_ids"}:
                row[field] = _required_text(
                    source_row, field, source="pfmea_demo.csv", row_number=row_number
                )
            severity = _strict_integer(
                source_row.get("severity"),
                source="pfmea_demo.csv",
                row_number=row_number,
                field="severity",
            )
            if not 1 <= severity <= 10:
                raise ValueError(f"pfmea_demo.csv 第 {row_number} 行 severity 必须位于 1..10")
            causes = sorted(
                {
                    item.strip()
                    for item in _required_text(
                        source_row,
                        "cause_ids",
                        source="pfmea_demo.csv",
                        row_number=row_number,
                    ).split(";")
                    if item.strip()
                }
            )
            if not causes:
                raise ValueError(f"pfmea_demo.csv 第 {row_number} 行 cause_ids 不能为空")
            row["severity"] = severity
            row["cause_ids"] = ";".join(causes)
            normalized.append(row)
        cls._assert_unique(normalized, "pfmea_id", source="pfmea_demo.csv")
        cls._assert_unique(normalized, "fastening_point", source="pfmea_demo.csv")
        return sorted(normalized, key=lambda item: item["pfmea_id"])

    @classmethod
    def _normalize_alarms(cls, rows: list[dict[str, str]]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for row_number, source_row in enumerate(rows, start=2):
            row: dict[str, Any] = dict(source_row)
            for field in ALARM_COLUMNS:
                row[field] = _required_text(
                    source_row,
                    field,
                    source="alarm_dictionary_demo.csv",
                    row_number=row_number,
                )
            normalized.append(row)
        cls._assert_unique(
            normalized, "alarm_code", source="alarm_dictionary_demo.csv"
        )
        return sorted(normalized, key=lambda item: item["alarm_code"])

    @classmethod
    def _normalize_history(cls, payload: Any) -> list[dict[str, Any]]:
        if not isinstance(payload, list) or not payload:
            raise ValueError("historical_cases.json 顶层必须是非空数组")
        normalized: list[dict[str, Any]] = []
        required = {"case_id", "title", "cause_ids", "summary", "verification", "source_note"}
        for index, source_case in enumerate(payload, start=1):
            if not isinstance(source_case, dict):
                raise ValueError(f"historical_cases.json 第 {index} 项必须是对象")
            missing = required - set(source_case)
            if missing:
                raise ValueError(
                    f"historical_cases.json 第 {index} 项缺少字段：{', '.join(sorted(missing))}"
                )
            case = dict(source_case)
            for field in required - {"cause_ids"}:
                value = case.get(field)
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(
                        f"historical_cases.json 第 {index} 项字段 {field!r} 不能为空"
                    )
                case[field] = value.strip()
            causes = case.get("cause_ids")
            if not isinstance(causes, list) or not causes:
                raise ValueError(
                    f"historical_cases.json 第 {index} 项 cause_ids 必须是非空数组"
                )
            normalized_causes = sorted(
                {item.strip() for item in causes if isinstance(item, str) and item.strip()}
            )
            if len(normalized_causes) != len(causes):
                raise ValueError(
                    f"historical_cases.json 第 {index} 项 cause_ids 含空值、重复值或非字符串"
                )
            case["cause_ids"] = normalized_causes
            normalized.append(case)
        cls._assert_unique(normalized, "case_id", source="historical_cases.json")
        return sorted(normalized, key=lambda item: item["case_id"])

    @classmethod
    def _normalize_ontology(cls, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("ontology.json 顶层必须是对象")
        schema = payload.get("schema")
        if not isinstance(schema, str) or not schema.strip():
            raise ValueError("ontology.json 缺少非空 schema")
        raw_nodes = payload.get("nodes")
        raw_edges = payload.get("edges")
        if not isinstance(raw_nodes, list) or not isinstance(raw_edges, list):
            raise ValueError("ontology.json 的 nodes 与 edges 必须是数组")

        nodes: list[dict[str, Any]] = []
        for index, source_node in enumerate(raw_nodes, start=1):
            if not isinstance(source_node, dict):
                raise ValueError(f"ontology.json nodes[{index}] 必须是对象")
            node = dict(source_node)
            for field in ("id", "type", "name"):
                value = node.get(field)
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(f"ontology.json nodes[{index}].{field} 不能为空")
                node[field] = value.strip()
            nodes.append(node)
        cls._assert_unique(nodes, "id", source="ontology.json nodes")

        edges: list[dict[str, Any]] = []
        edge_keys: list[str] = []
        for index, source_edge in enumerate(raw_edges, start=1):
            if not isinstance(source_edge, dict):
                raise ValueError(f"ontology.json edges[{index}] 必须是对象")
            edge = dict(source_edge)
            for field in ("source", "relation", "target"):
                value = edge.get(field)
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(f"ontology.json edges[{index}].{field} 不能为空")
                edge[field] = value.strip()
            key = "\0".join((edge["source"], edge["relation"], edge["target"]))
            if key in edge_keys:
                raise ValueError(f"ontology.json 存在重复关系：{edge}")
            edge_keys.append(key)
            edges.append(edge)
        result = dict(payload)
        result["schema"] = schema.strip()
        result["nodes"] = sorted(nodes, key=lambda item: item["id"])
        result["edges"] = sorted(
            edges, key=lambda item: (item["source"], item["relation"], item["target"])
        )
        return result

    def _validate_references(self, alarms: list[dict[str, Any]]) -> None:
        del alarms  # Structure and uniqueness were validated before this call.
        control_points = {row["fastening_point"] for row in self.control_plans}
        pfmea_points = {row["fastening_point"] for row in self.pfmea_rows}
        if control_points != pfmea_points:
            missing_control = sorted(pfmea_points - control_points)
            missing_pfmea = sorted(control_points - pfmea_points)
            details: list[str] = []
            if missing_control:
                details.append("缺控制计划=" + ",".join(missing_control))
            if missing_pfmea:
                details.append("缺 PFMEA=" + ",".join(missing_pfmea))
            raise ValueError("控制计划与 PFMEA 紧固点引用不完整：" + "；".join(details))

        declared_causes = {
            cause
            for row in self.pfmea_rows
            for cause in str(row["cause_ids"]).split(";")
        }
        for case in self.historical_cases:
            unknown = set(case["cause_ids"]) - declared_causes
            if unknown:
                raise ValueError(
                    f"历史案例 {case['case_id']} 引用了 PFMEA 中不存在的 cause_id："
                    + ", ".join(sorted(unknown))
                )

        node_by_id = {node["id"]: node for node in self.ontology["nodes"]}
        for edge in self.ontology["edges"]:
            unknown = {edge["source"], edge["target"]} - set(node_by_id)
            if unknown:
                raise ValueError(
                    "ontology.json 关系引用了不存在的 node id：" + ", ".join(sorted(unknown))
                )

        # The demo ontology deliberately covers only selected points.  For each
        # point it does claim to cover, all table references must be present.
        graph_points = {
            node_id
            for node_id, node in node_by_id.items()
            if node["type"] == "FasteningPoint"
        }
        controls = {row["fastening_point"]: row for row in self.control_plans}
        pfmeas = {row["fastening_point"]: row for row in self.pfmea_rows}
        for point in graph_points:
            if point not in controls or point not in pfmeas:
                raise ValueError(f"ontology.json 紧固点 {point!r} 缺少控制计划或 PFMEA")
            expected = {
                controls[point]["tool_id"]: "Equipment",
                pfmeas[point]["failure_mode_id"]: "FailureMode",
                **{
                    cause: "Cause"
                    for cause in str(pfmeas[point]["cause_ids"]).split(";")
                },
            }
            for node_id, node_type in expected.items():
                node = node_by_id.get(node_id)
                if node is None:
                    raise ValueError(
                        f"ontology.json 紧固点 {point!r} 缺少引用节点 {node_id!r}"
                    )
                if node["type"] != node_type:
                    raise ValueError(
                        f"ontology.json 节点 {node_id!r} 类型应为 {node_type}，实际为 {node['type']}"
                    )

    def retrieve(self, fastening_point: str) -> KnowledgeBundle:
        if not isinstance(fastening_point, str) or not fastening_point.strip():
            raise ValueError("fastening_point 必须是非空字符串")
        point = fastening_point.strip()
        control = self._control_by_point.get(point)
        pfmea = self._pfmea_by_point.get(point)
        if control is None or pfmea is None:
            raise ValueError(f"知识库中不存在紧固点 {point!r} 的控制计划或 PFMEA")
        cause_ids = {item for item in str(pfmea["cause_ids"]).split(";") if item}
        cases = [
            case for case in self.historical_cases if cause_ids.intersection(case["cause_ids"])
        ]

        if point not in self._graph_points:
            subgraph = {
                "coverage": "not_available_for_point",
                "nodes": [],
                "edges": [],
            }
        else:
            node_ids = {point, control["tool_id"], pfmea["failure_mode_id"], *cause_ids}
            edges = [
                edge
                for edge in self.ontology["edges"]
                if edge["source"] in node_ids or edge["target"] in node_ids
            ]
            connected = node_ids.union(
                {edge["source"] for edge in edges},
                {edge["target"] for edge in edges},
            )
            nodes = [node for node in self.ontology["nodes"] if node["id"] in connected]
            subgraph = {"coverage": "complete_for_point", "nodes": nodes, "edges": edges}
        return KnowledgeBundle(
            control,
            pfmea,
            cases,
            subgraph,
            self.alarm_dictionary,
        )
