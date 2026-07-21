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
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

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
    # symbol_id -> condition_id -> pair-rule evidence
    pair_coverage: Dict[str, Dict[str, List[Dict[str, Any]]]] = field(
        default_factory=dict
    )


@dataclass(frozen=True)
class _PairRule:
    rule_id: str
    left_group_id: str
    right_group_id: str
    hop_count: int
    graph_scope: str


@dataclass(frozen=True)
class _PairMatch:
    left_symbol_id: str
    right_symbol_id: str
    distance: int


@dataclass(frozen=True)
class _PairResult:
    rule: _PairRule
    matches: Tuple[_PairMatch, ...]


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
        self.filtered_result_path = str(self.output_dir / "filtered_by_type.json")
        self.evidence_result_path = str(self.output_dir / "surface_evidence.json")
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
                    "pair_coverage": final_set.pair_coverage.get(symbol_id, {}),
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

        pair_rules = self._compile_pair_rules(group_ids, expression)
        if not pair_rules:
            return self._intersect([group_results[group_id] for group_id in group_ids])

        requires_call_graph = any(
            rule.hop_count > 0 and rule.graph_scope == "call"
            for rule in pair_rules
        )
        graph_store = self._get_graph_store() if requires_call_graph else None
        if requires_call_graph and graph_store is None:
            self._warn(
                f"{condition_id}: call graph unavailable; AND(n) degraded to AND(0)"
            )

        unsupported = [
            rule
            for rule in pair_rules
            if rule.hop_count > 0 and rule.graph_scope != "call"
        ]
        if unsupported:
            self._warn(
                f"{condition_id}: non-call AND(n) pair rules are not supported; "
                "the affected pairs use strict intersection"
            )

        paired_group_ids = set()
        execution_sets: List[_ExecutionSet] = []
        for rule in pair_rules:
            paired_group_ids.add(rule.left_group_id)
            paired_group_ids.add(rule.right_group_id)
            pair_result = self._execute_pair_rule(
                rule,
                group_results[rule.left_group_id],
                group_results[rule.right_group_id],
                graph_store,
            )
            if not pair_result.matches:
                return _ExecutionSet()
            execution_sets.append(
                self._pair_result_to_execution_set(
                    pair_result,
                    group_results,
                    condition_id,
                )
            )

        execution_sets.extend(
            group_results[group_id]
            for group_id in group_ids
            if group_id not in paired_group_ids
        )
        return self._intersect(execution_sets)

    @staticmethod
    def _compile_pair_rules(
        group_ids: List[str],
        expression: Dict[str, Any],
    ) -> List[_PairRule]:
        """Read planner-normalized atomic pair rules."""
        group_order = {group_id: index for index, group_id in enumerate(group_ids)}
        valid_groups = set(group_ids)
        compiled: Dict[Tuple[frozenset, str], _PairRule] = {}

        raw_rules = expression.get("rules")
        rules = raw_rules if isinstance(raw_rules, list) else []
        for raw_rule in rules:
            if not isinstance(raw_rule, dict):
                continue

            raw_groups = raw_rule.get("groups")
            if not isinstance(raw_groups, list) or len(raw_groups) != 2:
                continue
            left_group, right_group = (
                str(raw_groups[0]),
                str(raw_groups[1]),
            )
            if (
                left_group == right_group
                or left_group not in valid_groups
                or right_group not in valid_groups
            ):
                continue
            if group_order[left_group] > group_order[right_group]:
                left_group, right_group = right_group, left_group

            graph_scope = str(raw_rule.get("graph_scope") or "").strip().lower()
            if graph_scope not in {"call", "import"}:
                continue
            hop_count = _non_negative_int(
                raw_rule.get("hop_count"),
                -1,
            )
            if hop_count < 0:
                continue

            rule_key = (frozenset((left_group, right_group)), graph_scope)
            candidate = _PairRule(
                rule_id=f"{left_group}__{right_group}__{graph_scope}",
                left_group_id=left_group,
                right_group_id=right_group,
                hop_count=hop_count,
                graph_scope=graph_scope,
            )
            existing = compiled.get(rule_key)
            if existing is None or candidate.hop_count < existing.hop_count:
                compiled[rule_key] = candidate

        return sorted(
            compiled.values(),
            key=lambda rule: (
                group_order[rule.left_group_id],
                group_order[rule.right_group_id],
                rule.graph_scope,
            ),
        )

    @staticmethod
    def _execute_pair_rule(
        rule: _PairRule,
        left_result: _ExecutionSet,
        right_result: _ExecutionSet,
        graph_store: Optional[RelationGraphStore],
    ) -> _PairResult:
        """Execute one pair by expanding only the smaller direct-result side."""
        if (
            rule.hop_count <= 0
            or rule.graph_scope != "call"
            or graph_store is None
        ):
            common_ids = set(left_result.symbols) & set(right_result.symbols)
            return _PairResult(
                rule=rule,
                matches=tuple(
                    _PairMatch(symbol_id, symbol_id, 0)
                    for symbol_id in sorted(common_ids)
                ),
            )

        expand_left = len(left_result.symbols) <= len(right_result.symbols)
        starts = left_result.symbols if expand_left else right_result.symbols
        targets = right_result.symbols if expand_left else left_result.symbols
        try:
            numeric_starts = [int(symbol_id) for symbol_id in starts]
            numeric_targets = [int(symbol_id) for symbol_id in targets]
        except (TypeError, ValueError):
            return _PairResult(rule=rule, matches=())

        matched_pairs = graph_store.undirected_call_pairs(
            numeric_starts,
            numeric_targets,
            rule.hop_count,
        )
        matches = []
        for start_id, target_id, distance in matched_pairs:
            if expand_left:
                left_symbol_id = _symbol_key(start_id)
                right_symbol_id = _symbol_key(target_id)
            else:
                left_symbol_id = _symbol_key(target_id)
                right_symbol_id = _symbol_key(start_id)
            matches.append(
                _PairMatch(
                    left_symbol_id=left_symbol_id,
                    right_symbol_id=right_symbol_id,
                    distance=distance,
                )
            )
        return _PairResult(
            rule=rule,
            matches=tuple(
                sorted(
                    matches,
                    key=lambda match: (
                        match.left_symbol_id,
                        match.right_symbol_id,
                        match.distance,
                    ),
                )
            ),
        )

    @classmethod
    def _pair_result_to_execution_set(
        cls,
        pair_result: _PairResult,
        group_results: Dict[str, _ExecutionSet],
        condition_id: str,
    ) -> _ExecutionSet:
        """Project supported pair endpoints into one intersectable result set."""
        result = _ExecutionSet()
        rule = pair_result.rule
        seen_by_symbol: Dict[
            str,
            Set[Tuple[str, str, str, int]],
        ] = {}

        for match in pair_result.matches:
            endpoints = (
                (rule.left_group_id, match.left_symbol_id),
                (rule.right_group_id, match.right_symbol_id),
            )
            for group_id, symbol_id in endpoints:
                symbol = group_results[group_id].symbols.get(symbol_id)
                if symbol is None:
                    continue
                result.symbols.setdefault(symbol_id, symbol)
                cls._merge_symbol_coverage(
                    result.coverage,
                    symbol_id,
                    group_results[group_id].coverage.get(symbol_id, {}),
                )

            evidence = {
                "rule_id": rule.rule_id,
                "groups": [
                    rule.left_group_id,
                    rule.right_group_id,
                ],
                "symbol_ids": [
                    int(match.left_symbol_id),
                    int(match.right_symbol_id),
                ],
                "graph_scope": rule.graph_scope,
                "distance": match.distance,
                "hop_limit": rule.hop_count,
            }
            evidence_key: Tuple[str, str, str, int] = (
                rule.rule_id,
                match.left_symbol_id,
                match.right_symbol_id,
                match.distance,
            )
            for symbol_id in {match.left_symbol_id, match.right_symbol_id}:
                if symbol_id not in result.symbols:
                    continue
                seen = seen_by_symbol.setdefault(symbol_id, set())
                if evidence_key in seen:
                    continue
                seen.add(evidence_key)
                result.pair_coverage.setdefault(symbol_id, {}).setdefault(
                    condition_id,
                    [],
                ).append(deepcopy(evidence))

            cls._merge_incident_graph_evidence(
                result,
                group_results,
                condition_id,
                rule,
                match,
            )
        return result

    @classmethod
    def _merge_incident_graph_evidence(
        cls,
        result: _ExecutionSet,
        group_results: Dict[str, _ExecutionSet],
        condition_id: str,
        rule: _PairRule,
        match: _PairMatch,
    ) -> None:
        if match.left_symbol_id == match.right_symbol_id:
            return
        endpoints = (
            (
                match.left_symbol_id,
                rule.right_group_id,
                match.right_symbol_id,
            ),
            (
                match.right_symbol_id,
                rule.left_group_id,
                match.left_symbol_id,
            ),
        )
        for result_symbol_id, neighbor_group_id, neighbor_symbol_id in endpoints:
            if result_symbol_id not in result.symbols:
                continue
            direct_evidence = (
                group_results[neighbor_group_id]
                .coverage.get(neighbor_symbol_id, {})
                .get(condition_id, {})
                .get(neighbor_group_id)
            )
            if direct_evidence is None:
                continue
            graph_evidence = deepcopy(direct_evidence)
            graph_evidence.update(
                {
                    "match_type": "graph_neighbor",
                    "distance": match.distance,
                    "neighbor_symbol_id": int(neighbor_symbol_id),
                    "pair_groups": [
                        rule.left_group_id,
                        rule.right_group_id,
                    ],
                    "hop_limit": rule.hop_count,
                }
            )
            result.coverage.setdefault(result_symbol_id, {}).setdefault(
                condition_id,
                {},
            ).setdefault(neighbor_group_id, graph_evidence)

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
                cls._merge_symbol_pair_coverage(
                    result.pair_coverage,
                    symbol_id,
                    result_set.pair_coverage.get(symbol_id, {}),
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
                cls._merge_symbol_pair_coverage(
                    result.pair_coverage,
                    symbol_id,
                    result_set.pair_coverage.get(symbol_id, {}),
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
            pair_coverage={
                symbol_id: deepcopy(coverage)
                for symbol_id, coverage in include_result.pair_coverage.items()
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

    @staticmethod
    def _merge_symbol_pair_coverage(
        target: Dict[str, Dict[str, List[Dict[str, Any]]]],
        symbol_id: str,
        source: Dict[str, List[Dict[str, Any]]],
    ) -> None:
        symbol_coverage = target.setdefault(symbol_id, {})
        for condition_id, pair_evidence in source.items():
            condition_coverage = symbol_coverage.setdefault(condition_id, [])
            seen = {
                (
                    evidence.get("rule_id"),
                    tuple(evidence.get("symbol_ids", [])),
                    evidence.get("distance"),
                )
                for evidence in condition_coverage
            }
            for evidence in pair_evidence:
                evidence_key = (
                    evidence.get("rule_id"),
                    tuple(evidence.get("symbol_ids", [])),
                    evidence.get("distance"),
                )
                if evidence_key in seen:
                    continue
                seen.add(evidence_key)
                condition_coverage.append(deepcopy(evidence))

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
    output_path = Path(QUERY_OUTPUT_DIR) / "filtered_by_type.json"
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
