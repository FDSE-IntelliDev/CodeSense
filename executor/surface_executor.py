"""Execute the four-layer planner-generated SurfacePlan.

Execution layers:
1. term: keywords and synonyms inside one group are OR-ed;
2. group: include groups use identity or graph-aware AND(n);
3. condition: exclude groups are OR-ed and subtracted from the include result;
4. cross-condition: compatible conditions intersect and incompatible groups union.

The executor consumes only ``surface_semql.json``. SurfaceCon/SemQL schema
parsing belongs to SurfacePlanner and is intentionally not duplicated here.
"""

from __future__ import annotations

import json
import sys
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from definition import PROJECT_OUTPUT_DIR, QUERY_OUTPUT_DIR
from filters.relation_graph_store import RelationGraphStore
from filters.type_filter import filter_symbols_by_type
from search.full_term_matcher import FullTermMatcher
from search.invert_index_search import search_symbols_by_terms
from utils.file_utils import load_res, save_res


def _symbol_key(value: Any) -> str:
    return str(value)


def _non_negative_int(value: Any, default: int = 0) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if number >= 0 else default


@dataclass
class _ExecutionSet:
    symbols: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    # symbol_id -> condition_id -> group_id -> evidence
    coverage: Dict[str, Dict[str, Dict[str, Dict[str, Any]]]] = field(
        default_factory=dict
    )


class SurfaceExecutor:
    """Interpret SurfacePlan while reusing the existing term-search kernel."""

    SUPPORTED_MATCH_KINDS = {"code_element", "unknown"}

    def __init__(
        self,
        output_dir: str = QUERY_OUTPUT_DIR,
        project_output_dir: str = PROJECT_OUTPUT_DIR,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.project_output_dir = Path(project_output_dir)
        self.invert_index_path = str(self.project_output_dir / "invert_index.json")
        self.ngramed_symbol_path = str(
            self.project_output_dir / "ngramed_symbol.json"
        )
        self.codegraph_path = str(self.project_output_dir / "codegraph.sqlite")
        self.filtered_result_path = str(self.output_dir / "filtered_by_type_hop_0.json")
        self.evidence_result_path = str(self.output_dir / "surface_evidence_hop_0.json")
        self.group_search_result_path = str(
            self.output_dir / "surface_group_search_results.json"
        )

        self.matcher = FullTermMatcher(
            invert_index_path=self.invert_index_path,
            ngramed_symbol_path=self.ngramed_symbol_path,
        )
        ngramed_symbols = load_res(self.ngramed_symbol_path)
        self.ngramed_symbols = (
            ngramed_symbols if isinstance(ngramed_symbols, dict) else {}
        )
        self._graph_store: Optional[RelationGraphStore] = None
        self._warnings: List[str] = []
        self._condition_summaries: Dict[str, Dict[str, Any]] = {}
        self._group_search_results: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self.execution_report: Dict[str, Any] = {}
        self.group_search_report: Dict[str, Any] = {}

    def close(self) -> None:
        if self._graph_store is not None:
            self._graph_store.close()
            self._graph_store = None

    def execute(self, surface_plan: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Execute a serialized SurfacePlan and return compatible symbol records."""
        self._warnings = []
        self._condition_summaries = {}
        self._group_search_results = {}
        self.execution_report = {}
        self.group_search_report = {}

        if not isinstance(surface_plan, dict) or surface_plan.get("kind") != "surface":
            raise ValueError("SurfaceExecutor requires a kind='surface' plan")

        conditions = surface_plan.get("conditions")
        if not isinstance(conditions, list):
            conditions = []

        condition_results: Dict[str, _ExecutionSet] = {}
        for condition in conditions:
            if not isinstance(condition, dict):
                continue
            condition_id = str(condition.get("condition_id") or "").strip()
            if not condition_id:
                continue
            condition_results[condition_id] = self._execute_condition(condition)

        final_set = self._execute_condition_expression(
            surface_plan.get("condition_expression"),
            condition_results,
        )
        final_results = list(final_set.symbols.values())
        self.execution_report = {
            "plan_version": surface_plan.get("version"),
            "raw_query": surface_plan.get("raw_query"),
            "result_count": len(final_results),
            "warnings": list(self._warnings),
            "conditions": self._condition_summaries,
            "evidence_by_symbol_id": {
                symbol_id: {
                    "condition_ids": list(final_set.coverage.get(symbol_id, {})),
                    "coverage": final_set.coverage.get(symbol_id, {}),
                }
                for symbol_id in final_set.symbols
            },
        }
        self.group_search_report = {
            "plan_version": surface_plan.get("version"),
            "raw_query": surface_plan.get("raw_query"),
            "stage": "before_group_expression",
            "description": (
                "Per-group direct term-OR results after condition type filtering "
                "and before identity/or/and_hop execution."
            ),
            "conditions": self._group_search_results,
        }
        return final_results

    def _execute_condition(self, condition: Dict[str, Any]) -> _ExecutionSet:
        condition_id = str(condition["condition_id"])
        match = condition.get("match") if isinstance(condition.get("match"), dict) else {}

        if not self._supports_match(match, condition_id):
            self._condition_summaries[condition_id] = {
                "include_count": 0,
                "exclude_count": 0,
                "result_count": 0,
                "result_operator": condition.get("result_expression", {}).get(
                    "operator", "empty"
                ),
            }
            return _ExecutionSet()

        include_result = self._execute_clause(
            condition.get("include_clause"), match, condition_id
        )
        exclude_result = self._execute_clause(
            condition.get("exclude_clause"), match, condition_id
        )
        result_expression = (
            condition.get("result_expression")
            if isinstance(condition.get("result_expression"), dict)
            else {}
        )
        operator = str(result_expression.get("operator") or "empty")

        if operator == "identity":
            result = include_result
        elif operator == "subtract":
            result = self._subtract(include_result, exclude_result)
        elif operator == "exclude_only":
            result = exclude_result
        else:
            result = _ExecutionSet()

        self._condition_summaries[condition_id] = {
            "include_count": len(include_result.symbols),
            "exclude_count": len(exclude_result.symbols),
            "result_count": len(result.symbols),
            "result_operator": operator,
        }
        return result

    def _supports_match(self, match: Dict[str, Any], condition_id: str) -> bool:
        kind = str(match.get("kind") or "unknown").strip().lower()
        code_text = str(match.get("code_text") or "").strip()
        if code_text:
            # TODO: route non-empty code_text to a dedicated exact/snippet executor.
            self._warn(
                f"{condition_id}: non-empty code_text is not supported by SurfaceExecutor yet"
            )
            return False
        if kind not in self.SUPPORTED_MATCH_KINDS:
            # TODO: add dedicated code_snippet and code_line search executors.
            self._warn(
                f"{condition_id}: match kind '{kind}' is not supported by SurfaceExecutor yet"
            )
            return False
        return True

    def _execute_clause(
        self,
        clause: Any,
        match: Dict[str, Any],
        condition_id: str,
    ) -> _ExecutionSet:
        if not isinstance(clause, dict):
            return _ExecutionSet()

        groups = clause.get("keyword_groups")
        if not isinstance(groups, list):
            return _ExecutionSet()

        expression = (
            clause.get("group_expression")
            if isinstance(clause.get("group_expression"), dict)
            else {}
        )
        clause_id = str(clause.get("clause_id") or f"{condition_id}_clause")
        clause_record = self._group_search_results.setdefault(
            condition_id, {}
        ).setdefault(
            clause_id,
            {
                "property": str(clause.get("property") or "include"),
                "match": deepcopy(match),
                "group_expression": deepcopy(expression),
                "groups": {},
            },
        )

        group_results: Dict[str, _ExecutionSet] = {}
        for group in groups:
            if not isinstance(group, dict):
                continue
            group_id = str(group.get("group_id") or "").strip()
            if not group_id:
                continue
            group_results[group_id] = self._execute_keyword_group(
                group,
                match,
                condition_id,
                clause_id,
            )
        clause_record["direct_result_count_by_group"] = {
            group_id: len(result.symbols)
            for group_id, result in group_results.items()
        }
        group_ids = [
            str(group_id)
            for group_id in expression.get("groups", [])
            if str(group_id) in group_results
        ]
        if not group_ids:
            group_ids = list(group_results)

        operator = str(expression.get("operator") or "identity")
        ordered_results = [group_results[group_id] for group_id in group_ids]
        if operator == "or":
            return self._union(ordered_results)
        if operator == "and_hop":
            return self._and_hop(
                group_ids,
                group_results,
                expression,
                condition_id,
            )
        return ordered_results[0] if ordered_results else _ExecutionSet()

    def _execute_keyword_group(
        self,
        group: Dict[str, Any],
        match: Dict[str, Any],
        condition_id: str,
        clause_id: str,
    ) -> _ExecutionSet:
        group_id = str(group["group_id"])
        term_expression = (
            group.get("term_expression")
            if isinstance(group.get("term_expression"), dict)
            else {}
        )
        raw_terms = term_expression.get("terms")
        if not isinstance(raw_terms, list):
            raw_terms = []

        terms: List[str] = []
        source_by_term: Dict[str, str] = {}
        for raw_term in raw_terms:
            if not isinstance(raw_term, dict):
                continue
            value = str(raw_term.get("value") or "").strip()
            if not value:
                continue
            terms.append(value)
            source_by_term.setdefault(
                value.casefold(), str(raw_term.get("source") or "keyword")
            )

        if not terms:
            result = _ExecutionSet()
            self._record_group_search_result(
                condition_id=condition_id,
                clause_id=clause_id,
                group=group,
                terms=raw_terms,
                result=result,
            )
            return result

        search_result = search_symbols_by_terms(
            invert_index_path=self.invert_index_path,
            ngramed_symbol_path=self.ngramed_symbol_path,
            terms=terms,
            matcher=self.matcher,
            ngramed_symbols=self.ngramed_symbols,
        )
        symbols = search_result.get("symbols", [])
        if str(match.get("kind") or "unknown").lower() == "code_element":
            symbols = filter_symbols_by_type(
                symbols,
                match.get("code_element_types", []),
            )

        details = search_result.get("detail", [])
        matches_by_id = search_result.get("matched_subtokens_by_symbol_id", {})
        result = _ExecutionSet()
        for symbol in symbols:
            if not isinstance(symbol, dict) or symbol.get("symbol_id") is None:
                continue
            symbol_id = _symbol_key(symbol["symbol_id"])
            result.symbols[symbol_id] = symbol
            evidence = self._build_direct_evidence(
                terms=terms,
                source_by_term=source_by_term,
                matched_terms=matches_by_id.get(symbol_id, []),
                details=details,
            )
            result.coverage[symbol_id] = {
                condition_id: {
                    group_id: evidence,
                }
            }
        self._record_group_search_result(
            condition_id=condition_id,
            clause_id=clause_id,
            group=group,
            terms=raw_terms,
            result=result,
        )
        return result

    def _record_group_search_result(
        self,
        condition_id: str,
        clause_id: str,
        group: Dict[str, Any],
        terms: List[Dict[str, Any]],
        result: _ExecutionSet,
    ) -> None:
        """Record direct group hits before clause-level OR/AND_HOP execution."""
        group_id = str(group["group_id"])
        clause_record = self._group_search_results[condition_id][clause_id]
        clause_record["groups"][group_id] = {
            "property": str(group.get("property") or clause_record["property"]),
            "reason": group.get("reason"),
            "term_expression": {
                "operator": "or",
                "terms": deepcopy(terms),
            },
            "result_count": len(result.symbols),
            "results": list(result.symbols.values()),
            "evidence_by_symbol_id": {
                symbol_id: (
                    result.coverage.get(symbol_id, {})
                    .get(condition_id, {})
                    .get(group_id, {})
                )
                for symbol_id in result.symbols
            },
        }

    @staticmethod
    def _build_direct_evidence(
        terms: Sequence[str],
        source_by_term: Dict[str, str],
        matched_terms: Sequence[str],
        details: Any,
    ) -> Dict[str, Any]:
        detail_list = details if isinstance(details, list) else []
        chosen_term = terms[0] if terms else ""
        chosen_matched_term = matched_terms[0] if matched_terms else ""
        chosen_keyword = chosen_term

        for term in terms:
            matched = False
            for detail in detail_list:
                if not isinstance(detail, dict):
                    continue
                source_terms = detail.get("source_terms") or [detail.get("term")]
                if term not in source_terms:
                    continue
                detail_matches = detail.get("matched_subtokens") or []
                overlap = [item for item in matched_terms if item in detail_matches]
                if not overlap:
                    continue
                chosen_term = term
                chosen_matched_term = overlap[0]
                chosen_keyword = str(detail.get("keyword") or term)
                matched = True
                break
            if matched:
                break

        return {
            "term": chosen_term,
            "matched_term": chosen_matched_term,
            "matched_keyword": chosen_keyword,
            "term_source": source_by_term.get(chosen_term.casefold(), "keyword"),
            "match_type": "direct",
            "distance": 0,
        }

    def _and_hop(
        self,
        group_ids: List[str],
        group_results: Dict[str, _ExecutionSet],
        expression: Dict[str, Any],
        condition_id: str,
    ) -> _ExecutionSet:
        if len(group_ids) < 2:
            return group_results[group_ids[0]] if group_ids else _ExecutionSet()

        pair_rules: Dict[frozenset, Tuple[int, Tuple[str, ...]]] = {}
        for index, left_group in enumerate(group_ids):
            for right_group in group_ids[index + 1 :]:
                pair_rules[frozenset((left_group, right_group))] = self._pair_rule(
                    expression,
                    left_group,
                    right_group,
                )

        if all(limit == 0 for limit, _scopes in pair_rules.values()):
            return self._intersect([group_results[group_id] for group_id in group_ids])

        positive_call_rules = [
            pair
            for pair, (limit, scopes) in pair_rules.items()
            if limit > 0 and "call" in scopes
        ]
        # unsupported_rules = [
        #     pair
        #     for pair, (limit, scopes) in pair_rules.items()
        #     if limit > 0 and "call" not in scopes
        # ]
        # if unsupported_rules:
        #     self._warn(
        #         f"{condition_id}: non-call AND(n) scope is not supported; "
        #         "the affected pairs use strict intersection"
        #     )
        if not positive_call_rules:
            return self._intersect([group_results[group_id] for group_id in group_ids])

        graph_store = self._get_graph_store()
        if graph_store is None:
            self._warn(
                f"{condition_id}: call graph unavailable; AND(n) degraded to AND(0)"
            )
            return self._intersect([group_results[group_id] for group_id in group_ids])

        max_hop_by_group = {group_id: 0 for group_id in group_ids}
        for pair, (limit, scopes) in pair_rules.items():
            if limit <= 0 or "call" not in scopes:
                continue
            for group_id in pair:
                max_hop_by_group[group_id] = max(max_hop_by_group[group_id], limit)

        neighborhoods: Dict[str, Dict[int, Tuple[int, int]]] = {}
        for group_id in group_ids:
            start_ids = []
            for symbol_id in group_results[group_id].symbols:
                try:
                    start_ids.append(int(symbol_id))
                except (TypeError, ValueError):
                    continue
            neighborhoods[group_id] = graph_store.undirected_call_neighborhood(
                start_ids,
                max_hop_by_group[group_id],
            )

        candidates = self._union([group_results[group_id] for group_id in group_ids])
        result = _ExecutionSet()
        for symbol_id, symbol in candidates.symbols.items():
            try:
                numeric_symbol_id = int(symbol_id)
            except (TypeError, ValueError):
                continue

            direct_groups = {
                group_id
                for group_id in group_ids
                if symbol_id in group_results[group_id].symbols
            }
            covered_groups = set(direct_groups)
            selected_graph_evidence: Dict[str, Dict[str, Any]] = {}

            changed = True
            while changed and len(covered_groups) < len(group_ids):
                changed = False
                for target_group in group_ids:
                    if target_group in covered_groups:
                        continue
                    neighborhood_entry = neighborhoods[target_group].get(
                        numeric_symbol_id
                    )
                    if neighborhood_entry is None:
                        continue
                    distance, origin_symbol_id = neighborhood_entry

                    valid_anchors = []
                    for anchor_group in covered_groups:
                        limit, scopes = pair_rules.get(
                            frozenset((target_group, anchor_group)),
                            (0, ()),
                        )
                        if "call" in scopes and distance <= limit:
                            valid_anchors.append((limit, anchor_group))
                    if not valid_anchors:
                        continue

                    origin_id = _symbol_key(origin_symbol_id)
                    origin_evidence = (
                        group_results[target_group]
                        .coverage.get(origin_id, {})
                        .get(condition_id, {})
                        .get(target_group)
                    )
                    if origin_evidence is None:
                        continue
                    graph_evidence = deepcopy(origin_evidence)
                    graph_evidence.update(
                        {
                            "match_type": "graph_neighbor",
                            "distance": distance,
                            "neighbor_symbol_id": origin_symbol_id,
                        }
                    )
                    selected_graph_evidence[target_group] = graph_evidence
                    covered_groups.add(target_group)
                    changed = True

            if len(covered_groups) != len(group_ids):
                continue

            result.symbols[symbol_id] = symbol
            result.coverage[symbol_id] = deepcopy(
                candidates.coverage.get(symbol_id, {})
            )
            condition_coverage = result.coverage[symbol_id].setdefault(
                condition_id, {}
            )
            condition_coverage.update(selected_graph_evidence)
        return result

    @staticmethod
    def _pair_rule(
        expression: Dict[str, Any],
        left_group: str,
        right_group: str,
    ) -> Tuple[int, Tuple[str, ...]]:
        default_hop = _non_negative_int(expression.get("default_hop_count"), 0)
        selected: List[Tuple[int, Tuple[str, ...]]] = []
        rules = expression.get("rules")
        if not isinstance(rules, list):
            rules = []

        pair_key = frozenset((left_group, right_group))
        for rule in rules:
            if not isinstance(rule, dict):
                continue
            rule_groups = {str(group_id) for group_id in rule.get("groups", [])}
            if not pair_key.issubset(rule_groups):
                continue
            hop_count = _non_negative_int(
                rule.get("default_hop_count"),
                default_hop,
            )
            pairwise = rule.get("pairwise_hop_counts")
            if isinstance(pairwise, list):
                for pair in pairwise:
                    if not isinstance(pair, dict):
                        continue
                    if frozenset(str(item) for item in pair.get("groups", [])) == pair_key:
                        hop_count = _non_negative_int(pair.get("hop_count"), hop_count)
                        break
            scopes = tuple(
                str(scope).lower()
                for scope in rule.get("graph_scope", [])
                if str(scope).lower() in {"call", "import"}
            )
            selected.append((hop_count, scopes))

        if not selected:
            return default_hop, ("call",) if default_hop > 0 else ()
        # Multiple applicable constraints are combined conservatively.
        return min(selected, key=lambda item: item[0])

    def _get_graph_store(self) -> Optional[RelationGraphStore]:
        if self._graph_store is None:
            self._graph_store = RelationGraphStore.open_if_ready(self.codegraph_path)
        return self._graph_store

    def _execute_condition_expression(
        self,
        expression: Any,
        condition_results: Dict[str, _ExecutionSet],
    ) -> _ExecutionSet:
        if not isinstance(expression, dict):
            return _ExecutionSet()

        merge_results: List[_ExecutionSet] = []
        merge_groups = expression.get("groups")
        if not isinstance(merge_groups, list):
            merge_groups = []

        for merge_group in merge_groups:
            if not isinstance(merge_group, dict):
                continue
            result_sets = [
                condition_results[condition_id]
                for condition_id in merge_group.get("condition_ids", [])
                if condition_id in condition_results
            ]
            operator = str(merge_group.get("operator") or "identity")
            if operator == "intersect":
                merge_results.append(self._intersect(result_sets))
            elif result_sets:
                merge_results.append(result_sets[0])

        operator = str(expression.get("operator") or "empty")
        if operator == "union":
            final_result = self._union(merge_results)
        elif merge_results:
            final_result = merge_results[0]
        else:
            final_result = _ExecutionSet()

        exclude_only_results = [
            condition_results[condition_id]
            for condition_id in expression.get("exclude_only_condition_ids", [])
            if condition_id in condition_results
        ]
        if exclude_only_results:
            final_result = self._subtract(
                final_result,
                self._union(exclude_only_results),
            )
        return final_result

    @classmethod
    def _union(cls, result_sets: Iterable[_ExecutionSet]) -> _ExecutionSet:
        result = _ExecutionSet()
        for result_set in result_sets:
            for symbol_id, symbol in result_set.symbols.items():
                result.symbols.setdefault(symbol_id, symbol)
                cls._merge_symbol_coverage(
                    result.coverage,
                    symbol_id,
                    result_set.coverage.get(symbol_id, {}),
                )
        return result

    @classmethod
    def _intersect(cls, result_sets: Sequence[_ExecutionSet]) -> _ExecutionSet:
        if not result_sets:
            return _ExecutionSet()
        common_ids = set(result_sets[0].symbols)
        for result_set in result_sets[1:]:
            common_ids.intersection_update(result_set.symbols)

        result = _ExecutionSet()
        for symbol_id, symbol in result_sets[0].symbols.items():
            if symbol_id not in common_ids:
                continue
            result.symbols[symbol_id] = symbol
            for result_set in result_sets:
                cls._merge_symbol_coverage(
                    result.coverage,
                    symbol_id,
                    result_set.coverage.get(symbol_id, {}),
                )
        return result

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
            },
            coverage={
                symbol_id: deepcopy(coverage)
                for symbol_id, coverage in include_result.coverage.items()
                if symbol_id not in excluded_ids
            },
        )

    @staticmethod
    def _merge_symbol_coverage(
        target: Dict[str, Dict[str, Dict[str, Dict[str, Any]]]],
        symbol_id: str,
        source: Dict[str, Dict[str, Dict[str, Any]]],
    ) -> None:
        symbol_coverage = target.setdefault(symbol_id, {})
        for condition_id, groups in source.items():
            condition_coverage = symbol_coverage.setdefault(condition_id, {})
            for group_id, evidence in groups.items():
                condition_coverage.setdefault(group_id, deepcopy(evidence))

    def _warn(self, message: str) -> None:
        if message not in self._warnings:
            self._warnings.append(message)


def run_surface_search(
    output_dir: str = QUERY_OUTPUT_DIR,
    project_output_dir: str = PROJECT_OUTPUT_DIR,
    surface_plan_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Execute ``surface_semql.json`` and preserve the legacy symbol-list output."""
    plan_path = Path(surface_plan_path or Path(output_dir) / "surface_semql.json")
    surface_plan = load_res(str(plan_path))
    executor = SurfaceExecutor(
        output_dir=output_dir,
        project_output_dir=project_output_dir,
    )
    try:
        results = executor.execute(surface_plan)
        save_res(executor.filtered_result_path, results)
        save_res(executor.evidence_result_path, executor.execution_report)
        save_res(executor.group_search_result_path, executor.group_search_report)
        return results
    finally:
        executor.close()


def main() -> None:
    results = run_surface_search(output_dir=QUERY_OUTPUT_DIR)
    output_path = Path(QUERY_OUTPUT_DIR) / "filtered_by_type_hop_0.json"
    print(
        json.dumps(
            {
                "result_count": len(results),
                "output_path": str(output_path.resolve()),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
