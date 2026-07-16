"""Compile SurfaceCon items into a group-aware surface execution plan."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple

from query_processing.plan_models import (
    SurfaceConditionExpression,
    SurfaceConditionMergeGroup,
    SurfaceConditionPlan,
    SurfaceConditionResultExpression,
    SurfaceGroupExpression,
    SurfaceGroupLogic,
    SurfaceKeywordGroup,
    SurfaceMatchSpec,
    SurfacePairwiseHopCount,
    SurfacePlan,
    SurfaceRetrievalClause,
    SurfaceTerm,
    SurfaceTermExpression,
)
from query_processing.planners.base import (
    normalize_optional_string,
    normalize_property,
    normalize_string_list,
)


class SurfacePlanner:
    """Normalize SurfaceCon without collapsing term, group, or condition logic."""

    SUPPORTED_MATCH_KINDS = {"code_element", "code_snippet", "code_line", "unknown"}
    DEFAULT_HOP_COUNT = 0

    def plan(
        self,
        conditions: Iterable[Dict[str, Any]],
        raw_query: Optional[str] = None,
    ) -> SurfacePlan:
        condition_plans: List[SurfaceConditionPlan] = []
        for index, condition in enumerate(conditions or []):
            if not isinstance(condition, dict):
                continue
            condition_plans.append(self._build_condition_plan(condition, index))

        return SurfacePlan(
            raw_query=normalize_optional_string(raw_query),
            conditions=condition_plans,
            condition_expression=self._build_condition_expression(condition_plans),
        )

    def _build_condition_plan(
        self,
        condition: Dict[str, Any],
        index: int,
    ) -> SurfaceConditionPlan:
        condition_id = f"surface_{index}"
        keyword_groups = self._normalize_keyword_groups(condition.get("keyword_groups"))
        if not keyword_groups:
            keyword_groups = [self._build_legacy_keyword_group(condition, index)]

        include_groups = [
            group for group in keyword_groups if group.property == "include"
        ]
        exclude_groups = [
            group for group in keyword_groups if group.property == "exclude"
        ]

        include_clause = self._build_include_clause(
            condition_id,
            include_groups,
            condition.get("group_logic"),
        )
        exclude_clause = self._build_exclude_clause(condition_id, exclude_groups)

        return SurfaceConditionPlan(
            condition_id=condition_id,
            match=self._build_match_spec(condition),
            include_clause=include_clause,
            exclude_clause=exclude_clause,
            result_expression=self._build_result_expression(
                include_clause,
                exclude_clause,
            ),
        )

    def _build_include_clause(
        self,
        condition_id: str,
        groups: List[SurfaceKeywordGroup],
        raw_group_logic: Any,
    ) -> Optional[SurfaceRetrievalClause]:
        if not groups:
            return None

        group_ids = [group.group_id for group in groups]
        operator = "identity" if len(group_ids) == 1 else "and_hop"
        return SurfaceRetrievalClause(
            clause_id=f"{condition_id}_include",
            property="include",
            keyword_groups=groups,
            group_expression=SurfaceGroupExpression(
                operator=operator,
                groups=group_ids,
                default_hop_count=self.DEFAULT_HOP_COUNT,
                rules=self._normalize_group_logic(raw_group_logic, groups),
            ),
        )

    @staticmethod
    def _build_exclude_clause(
        condition_id: str,
        groups: List[SurfaceKeywordGroup],
    ) -> Optional[SurfaceRetrievalClause]:
        if not groups:
            return None

        group_ids = [group.group_id for group in groups]
        return SurfaceRetrievalClause(
            clause_id=f"{condition_id}_exclude",
            property="exclude",
            keyword_groups=groups,
            group_expression=SurfaceGroupExpression(
                operator="identity" if len(group_ids) == 1 else "or",
                groups=group_ids,
            ),
        )

    @staticmethod
    def _build_result_expression(
        include_clause: Optional[SurfaceRetrievalClause],
        exclude_clause: Optional[SurfaceRetrievalClause],
    ) -> SurfaceConditionResultExpression:
        if include_clause and exclude_clause:
            operator = "subtract"
        elif include_clause:
            operator = "identity"
        elif exclude_clause:
            operator = "exclude_only"
        else:
            operator = "empty"

        return SurfaceConditionResultExpression(
            operator=operator,
            include_clause_id=include_clause.clause_id if include_clause else None,
            exclude_clause_id=exclude_clause.clause_id if exclude_clause else None,
        )

    def _build_match_spec(self, condition: Dict[str, Any]) -> SurfaceMatchSpec:
        match_kind = str(condition.get("match_kind") or "unknown").strip().lower()
        if match_kind not in self.SUPPORTED_MATCH_KINDS:
            match_kind = "unknown"

        return SurfaceMatchSpec(
            kind=match_kind,
            code_element_types=self._normalize_code_element_types(
                condition.get("code_element_type")
            ),
            code_text=normalize_optional_string(condition.get("code_text")),
        )

    def _build_legacy_keyword_group(
        self,
        condition: Dict[str, Any],
        index: int,
    ) -> SurfaceKeywordGroup:
        """Adapt the old condition-level property/terms into one keyword group."""
        return SurfaceKeywordGroup(
            group_id=f"legacy_{index}",
            property=normalize_property(condition.get("property")),
            term_expression=SurfaceTermExpression(
                terms=self._normalize_terms(condition)
            ),
            reason=None,
        )

    @staticmethod
    def _normalize_terms(container: Dict[str, Any]) -> List[SurfaceTerm]:
        terms: List[SurfaceTerm] = []
        seen = set()
        for source, field_name in (("keyword", "keywords"), ("synonym", "synonyms")):
            for value in normalize_string_list(container.get(field_name)):
                key = value.casefold()
                if key in seen:
                    continue
                seen.add(key)
                terms.append(SurfaceTerm(value=value, source=source))
        return terms

    def _normalize_keyword_groups(self, value: Any) -> List[SurfaceKeywordGroup]:
        if not isinstance(value, list):
            return []

        groups: List[SurfaceKeywordGroup] = []
        used_ids = set()
        for index, raw_group in enumerate(value):
            if not isinstance(raw_group, dict):
                continue

            group_id = normalize_optional_string(raw_group.get("group_id")) or f"k{index + 1}"
            if group_id in used_ids:
                continue
            used_ids.add(group_id)
            groups.append(
                SurfaceKeywordGroup(
                    group_id=group_id,
                    property=normalize_property(raw_group.get("property")),
                    term_expression=SurfaceTermExpression(
                        terms=self._normalize_terms(raw_group)
                    ),
                    reason=normalize_optional_string(raw_group.get("reason")),
                )
            )
        return groups

    def _normalize_group_logic(
        self,
        value: Any,
        keyword_groups: List[SurfaceKeywordGroup],
    ) -> List[SurfaceGroupLogic]:
        if not isinstance(value, list):
            return []

        include_group_ids = {group.group_id for group in keyword_groups}
        result: List[SurfaceGroupLogic] = []
        for raw_logic in value:
            if not isinstance(raw_logic, dict):
                continue

            groups = [
                group_id
                for group_id in normalize_string_list(raw_logic.get("groups"))
                if group_id in include_group_ids
            ]
            if len(groups) < 2:
                continue

            graph_scope = [
                scope.lower()
                for scope in normalize_string_list(raw_logic.get("graph_scope"))
                if scope.lower() in {"call", "import"}
            ]
            result.append(
                SurfaceGroupLogic(
                    groups=groups,
                    graph_scope=graph_scope,
                    default_hop_count=self._normalize_hop_count(
                        raw_logic.get("default_hop_count"),
                        default=self.DEFAULT_HOP_COUNT,
                    ),
                    pairwise_hop_counts=self._normalize_pairwise_hop_counts(
                        raw_logic.get("pairwise_hop_counts"),
                        set(groups),
                    ),
                    reason=normalize_optional_string(raw_logic.get("reason")),
                )
            )
        return result

    @staticmethod
    def _normalize_pairwise_hop_counts(
        value: Any,
        allowed_group_ids: set,
    ) -> List[SurfacePairwiseHopCount]:
        if not isinstance(value, list):
            return []

        result: List[SurfacePairwiseHopCount] = []
        seen_pairs = set()
        for raw_pair in value:
            if not isinstance(raw_pair, dict):
                continue
            groups = normalize_string_list(raw_pair.get("groups"))
            if len(groups) != 2 or any(group not in allowed_group_ids for group in groups):
                continue

            hop_count = SurfacePlanner._normalize_hop_count(
                raw_pair.get("hop_count"),
                default=None,
            )
            if hop_count is None:
                continue

            pair_key = frozenset(groups)
            if len(pair_key) != 2 or pair_key in seen_pairs:
                continue
            seen_pairs.add(pair_key)
            result.append(
                SurfacePairwiseHopCount(
                    groups=groups,
                    hop_count=hop_count,
                    reason=normalize_optional_string(raw_pair.get("reason")),
                )
            )
        return result

    @staticmethod
    def _normalize_hop_count(value: Any, default: Optional[int]) -> Optional[int]:
        try:
            hop_count = int(value)
        except (TypeError, ValueError):
            return default
        return hop_count if hop_count >= 0 else default

    @staticmethod
    def _build_condition_expression(
        conditions: List[SurfaceConditionPlan],
    ) -> SurfaceConditionExpression:
        grouped: Dict[Tuple[str, Tuple[str, ...]], List[str]] = {}
        exclude_only_condition_ids: List[str] = []

        for condition in conditions:
            if condition.include_clause is None:
                if condition.exclude_clause is not None:
                    exclude_only_condition_ids.append(condition.condition_id)
                continue

            match = condition.match
            type_key = (
                tuple(sorted(match.code_element_types))
                if match.kind == "code_element"
                else ()
            )
            grouped.setdefault((match.kind, type_key), []).append(condition.condition_id)

        merge_groups = [
            SurfaceConditionMergeGroup(
                operator="identity" if len(condition_ids) == 1 else "intersect",
                condition_ids=condition_ids,
                match_kind=match_kind,
                code_element_types=list(type_key),
            )
            for (match_kind, type_key), condition_ids in grouped.items()
        ]
        if not merge_groups:
            operator = "empty"
        elif len(merge_groups) == 1:
            operator = "identity"
        else:
            operator = "union"

        return SurfaceConditionExpression(
            operator=operator,
            groups=merge_groups,
            exclude_only_condition_ids=exclude_only_condition_ids,
        )

    @staticmethod
    def _normalize_code_element_types(value: Any) -> List[str]:
        types = [item.lower() for item in normalize_string_list(value)]
        seen = set(types)
        if "function" in seen and "method" not in seen:
            types.append("method")
        if "method" in seen and "function" not in seen:
            types.append("function")
        return types
