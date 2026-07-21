"""Compile RelationCon items into a normalized relation-only execution plan."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from query_processing.plan_models import (
    CallAnchor,
    RelationClause,
    RelationFilters,
    RelationGraphConstraint,
    RelationPlan,
)
from query_processing.planners.base import (
    normalize_optional_string,
    normalize_property,
)


class RelationPlanner:
    """Own parsing of caller/callee, graph roles, and relation-only fields."""

    ROLE_ALIASES = {
        "entrypoint": "entry_point",
        "entry-point": "entry_point",
        "entry_point": "entry_point",
        "leaf": "leaf",
        "isolated": "isolate",
        "isolate": "isolate",
    }

    def plan(
        self,
        conditions: Iterable[Dict[str, Any]],
        raw_query: Optional[str] = None,
    ) -> RelationPlan:
        include: List[RelationClause] = []
        exclude: List[RelationClause] = []

        for index, condition in enumerate(conditions or []):
            if not isinstance(condition, dict):
                continue
            clause = self._build_clause(condition, index)
            property_name = normalize_property(condition.get("property"))
            (exclude if property_name == "exclude" else include).append(clause)

        return RelationPlan(
            raw_query=normalize_optional_string(raw_query),
            filters=RelationFilters(include=include, exclude=exclude),
        )

    def _build_clause(
        self,
        condition: Dict[str, Any],
        index: int,
    ) -> RelationClause:
        return RelationClause(
            clause_id=f"relation_{index}",
            file_path=normalize_optional_string(condition.get("file_path")),
            graph_constraint=self._normalize_graph_constraint(
                condition.get("graph_constraint")
            ),
            caller=self.parse_call_anchor(condition.get("caller")),
            callee=self.parse_call_anchor(condition.get("callee")),
            code_ql=normalize_optional_string(condition.get("code_ql")),
            description=normalize_optional_string(condition.get("description")),
        )

    def _normalize_graph_constraint(
        self,
        value: Any,
    ) -> Optional[RelationGraphConstraint]:
        if not isinstance(value, dict):
            return None
        role = normalize_optional_string(value.get("role"))
        if role is None:
            return None
        normalized_role = self.ROLE_ALIASES.get(role.lower())
        if normalized_role is None:
            return None
        return RelationGraphConstraint(role=normalized_role)

    @staticmethod
    def parse_call_anchor(value: Any) -> Optional[CallAnchor]:
        raw = normalize_optional_string(value)
        if raw is None:
            return None

        parts = raw.split(":")
        if len(parts) == 2:
            file_name = normalize_optional_string(parts[0])
            symbol_name = normalize_optional_string(parts[1])
        elif len(parts) == 1:
            file_name = None
            symbol_name = raw
        else:
            return None

        if symbol_name is None:
            return None
        return CallAnchor(
            file_name=file_name,
            symbol_name=symbol_name,
        )
