"""Execute planner-generated ``relation_semql.json`` plans.

RelationPlanner owns RelationCon parsing and emits normalized clauses. This
executor only performs the planned structural filters:

1. constraints inside one clause are intersected;
2. include clauses are intersected with each other and Surface candidates;
3. exclude clauses are unioned and subtracted from the include result.

Caller/callee and graph-role constraints reuse the implementation-aware
code-graph-first filtering kernels, with the existing LSP path only as a
database-unavailable fallback. ``code_ql`` remains in the plan as an explicit
future execution target; the online CodeQL query runner is not implemented yet.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from codesense.config import load_config
from codesense.filters import RELATION_FILTERS
from codesense.filters.relation_graph_store import RelationGraphStore


def _symbol_key(symbol: Dict[str, Any]) -> str:
    symbol_id = symbol.get("symbol_id")
    if symbol_id is not None:
        return str(symbol_id)
    return "|".join(
        [
            str(symbol.get("file", "")),
            str(symbol.get("name", "")),
            str(symbol.get("range", "")),
        ]
    )


# def _project_output_dir_from_candidate_path(candidate_path: str) -> Path:
#     parent = Path(candidate_path).resolve().parent
#     return parent.parent if parent.name.startswith("query_") else parent


def _normalize_path(value: Any) -> str:
    return str(value or "").strip().replace("\\", "/").strip("./")


_SOURCE_FILE_EXTENSIONS = {
    "c",
    "cc",
    "cpp",
    "go",
    "h",
    "hpp",
    "java",
    "js",
    "kt",
    "py",
    "rs",
    "ts",
    "tsx",
}


def _classify_file_path_constraint(
    constraint: str,
    candidate_files: Iterable[Any],
) -> str:
    """Classify a RelationCon path as a source file or directory/package."""
    expected = _normalize_path(constraint)
    suffix = expected.rsplit("/", 1)[-1]
    if "." in suffix and suffix.rsplit(".", 1)[-1].lower() in _SOURCE_FILE_EXTENSIONS:
        return "file"

    for candidate_file in candidate_files:
        candidate = _normalize_path(candidate_file)
        if candidate == expected or candidate.endswith(f"/{expected}"):
            return "file"
    return "directory"


def _matches_file_path(
    candidate_file: Any,
    constraint: str,
    constraint_kind: str,
) -> bool:
    candidate = _normalize_path(candidate_file)
    expected = _normalize_path(constraint)
    if not candidate or not expected:
        return False

    variants = {expected}
    if constraint_kind == "directory" and "/" not in expected and "." in expected:
        variants.add(expected.replace(".", "/"))

    if constraint_kind == "file":
        return any(
            candidate == variant or candidate.endswith(f"/{variant}")
            for variant in variants
        )

    padded_candidate = f"/{candidate}/"
    return any(
        candidate == variant
        or candidate.startswith(f"{variant}/")
        or f"/{variant}/" in padded_candidate
        for variant in variants
    )


@dataclass
class _ExecutionSet:
    symbols: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def from_symbols(cls, symbols: Iterable[Dict[str, Any]]) -> "_ExecutionSet":
        result: Dict[str, Dict[str, Any]] = {}
        for symbol in symbols:
            if not isinstance(symbol, dict):
                continue
            result.setdefault(_symbol_key(symbol), symbol)
        return cls(symbols=result)


class RelationExecutor:
    """Interpret a normalized RelationPlan over Surface candidate symbols."""

    def __init__(
        self,
        candidate_path: str,
        layer: Optional[int] = 1,
        worker_count: int = 4,
    ) -> None:
        self.candidate_path = candidate_path
        self.layer = layer
        self.worker_count = worker_count
        codegraph_path = (
            str(load_config().project_output_dir)
            + "/codegraph.sqlite"
        )
        self._graph_store = RelationGraphStore.open_if_ready(str(codegraph_path))
        self._warnings: List[str] = []
        self._clause_reports: Dict[str, Dict[str, Any]] = {}
        self.execution_report: Dict[str, Any] = {}

    def close(self) -> None:
        if self._graph_store is not None:
            self._graph_store.close()
            self._graph_store = None

    def execute(
        self,
        relation_plan: Dict[str, Any],
        candidates: Sequence[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        if not isinstance(relation_plan, dict) or relation_plan.get("kind") != "relation":
            raise ValueError("RelationExecutor requires a kind='relation' plan")

        self._warnings = []
        self._clause_reports = {}
        candidate_set = _ExecutionSet.from_symbols(candidates)
        filters = relation_plan.get("filters")
        filters = filters if isinstance(filters, dict) else {}
        result_logic = relation_plan.get("result_logic")
        result_logic = result_logic if isinstance(result_logic, dict) else {}
        self._validate_result_logic(result_logic)

        include_clauses = self._clauses(filters.get("include"))
        exclude_clauses = self._clauses(filters.get("exclude"))

        if include_clauses:
            include_result = self._intersect([
                self._execute_clause(clause, candidate_set, "include")
                for clause in include_clauses
            ])
            current = self._intersect((candidate_set, include_result))
        else:
            current = candidate_set

        if exclude_clauses:
            exclude_result = self._union(
                self._execute_clause(clause, candidate_set, "exclude")
                for clause in exclude_clauses
            )
            current = self._subtract(current, exclude_result)

        results = list(current.symbols.values())
        self.execution_report = {
            "plan_version": relation_plan.get("version"),
            "raw_query": relation_plan.get("raw_query"),
            "candidate_count": len(candidate_set.symbols),
            "result_count": len(results),
            "warnings": list(self._warnings),
            "clauses": self._clause_reports,
        }
        return results

    @staticmethod
    def _clauses(value: Any) -> List[Dict[str, Any]]:
        return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []

    @staticmethod
    def _validate_result_logic(logic: Dict[str, Any]) -> None:
        expected = {
            "clause_operator": "intersect",
            "include_clause_operator": "intersect",
            "include_operator": "intersect_candidates",
            "exclude_clause_operator": "union",
            "exclude_operator": "subtract",
        }
        for field_name, expected_value in expected.items():
            actual = str(logic.get(field_name) or expected_value)
            if actual != expected_value:
                raise ValueError(
                    f"Unsupported RelationPlan {field_name}: {actual!r}"
                )

    def _execute_clause(
        self,
        clause: Dict[str, Any],
        candidates: _ExecutionSet,
        property_name: str,
    ) -> _ExecutionSet:
        clause_id = str(clause.get("clause_id") or "").strip()
        working = candidates
        executed_constraint_count = 0
        constraint_counts: Dict[str, int] = {}
        file_path_kind: Optional[str] = None

        file_path = str(clause.get("file_path") or "").strip()
        if file_path:
            file_path_kind = _classify_file_path_constraint(
                file_path,
                (symbol.get("file") for symbol in working.symbols.values()),
            )
            filtered = [
                symbol
                for symbol in working.symbols.values()
                if _matches_file_path(
                    symbol.get("file"),
                    file_path,
                    file_path_kind,
                )
            ]
            working = self._intersect(
                (working, _ExecutionSet.from_symbols(filtered))
            )
            executed_constraint_count += 1
            constraint_counts["file_path"] = len(working.symbols)

        # 三类关系约束原本是三段几乎一样的代码。它们都实现 RelationFilter，
        # 所以这里只声明「用哪个实现、给什么参数」，按注册名取实现——
        # 本文件不认识任何一个具体过滤器类。加一种新关系约束不用改这里。
        preserve = property_name == "include"
        call_kwargs: Dict[str, Any] = {
            "graph_store": self._graph_store,
            "layer": self.layer,
            "worker_count": self.worker_count,
            "preserve_on_empty_fallback": preserve,
        }
        # 顺序有意义：每一步都在上一步收窄后的集合上继续过滤。
        specs: List[Tuple[str, Dict[str, Any]]] = []

        graph_constraint = clause.get("graph_constraint")
        graph_constraint = (
            graph_constraint if isinstance(graph_constraint, dict) else {}
        )
        role = str(graph_constraint.get("role") or "").strip().lower()
        if role:
            if self._graph_store is None:
                self._warn(
                    f"{clause_id}: code graph unavailable; graph role was not applied"
                )
            else:
                specs.append(
                    (
                        "graph_role",
                        {
                            "roles": [role],
                            "graph_store": self._graph_store,
                            "preserve_non_applicable": preserve,
                        },
                    )
                )

        caller_entry = self._call_entry(clause.get("caller"))
        if caller_entry is not None:
            specs.append(("caller", {"entries": [caller_entry], **call_kwargs}))

        callee_entry = self._call_entry(clause.get("callee"))
        if callee_entry is not None:
            specs.append(("callee", {"entries": [callee_entry], **call_kwargs}))

        for filter_name, filter_kwargs in specs:
            filtered = RELATION_FILTERS.create(filter_name, **filter_kwargs).apply(
                list(working.symbols.values())
            )
            working = self._intersect(
                (working, _ExecutionSet.from_symbols(filtered))
            )
            executed_constraint_count += 1
            constraint_counts[filter_name] = len(working.symbols)

        if str(clause.get("code_ql") or "").strip():
            # TODO: execute arbitrary planner-provided CodeQL against a reusable
            # project CodeQL database and map selected locations to symbol ids.
            self._warn(
                f"{clause_id}: code_ql is planned but online execution is not implemented"
            )

        if executed_constraint_count:
            clause_result = working
        elif property_name == "include":
            clause_result = candidates
        else:
            # An exclude clause with no executable constraint must not erase
            # the complete candidate set (for example, code_ql-only today).
            clause_result = _ExecutionSet()
        self._clause_reports[clause_id] = {
            "property": property_name,
            "constraint_operator": "intersect",
            "constraint_counts": constraint_counts,
            "file_path_kind": file_path_kind,
            "result_count": len(clause_result.symbols),
            "has_unexecuted_code_ql": bool(
                str(clause.get("code_ql") or "").strip()
            ),
        }
        return clause_result

    @staticmethod
    def _call_entry(
        value: Any,
    ) -> Optional[Tuple[Optional[str], str]]:
        if not isinstance(value, dict):
            return None
        symbol_name = str(value.get("symbol_name") or "").strip()
        if not symbol_name:
            return None
        file_name = str(value.get("file_name") or "").strip() or None
        return file_name, symbol_name

    @staticmethod
    def _union(result_sets: Iterable[_ExecutionSet]) -> _ExecutionSet:
        result = _ExecutionSet()
        for result_set in result_sets:
            for symbol_id, symbol in result_set.symbols.items():
                result.symbols.setdefault(symbol_id, symbol)
        return result

    @staticmethod
    def _intersect(result_sets: Sequence[_ExecutionSet]) -> _ExecutionSet:
        if not result_sets:
            return _ExecutionSet()
        common_ids: Set[str] = set(result_sets[0].symbols)
        for result_set in result_sets[1:]:
            common_ids.intersection_update(result_set.symbols)
        return _ExecutionSet(
            symbols={
                symbol_id: symbol
                for symbol_id, symbol in result_sets[0].symbols.items()
                if symbol_id in common_ids
            }
        )

    @staticmethod
    def _subtract(
        include_result: _ExecutionSet,
        exclude_result: _ExecutionSet,
    ) -> _ExecutionSet:
        excluded_ids = set(exclude_result.symbols)
        return _ExecutionSet(
            symbols={
                symbol_id: symbol
                for symbol_id, symbol in include_result.symbols.items()
                if symbol_id not in excluded_ids
            }
        )

    def _warn(self, message: str) -> None:
        if message not in self._warnings:
            self._warnings.append(message)
