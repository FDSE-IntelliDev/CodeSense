"""Top-level query planner that routes each SemCon family independently."""

from __future__ import annotations

from typing import Any, Dict, Optional

from codesense.query.plan_models import QueryPlanBundle
from codesense.query.planners.intention_planner import IntentionPlanner
from codesense.query.planners.relation_planner import RelationPlanner
from codesense.query.planners.surface_planner import SurfacePlanner


class QueryPlanner:
    """Create independent surface, relation, and intention execution plans."""

    def __init__(
        self,
        surface_planner: Optional[SurfacePlanner] = None,
        relation_planner: Optional[RelationPlanner] = None,
        intention_planner: Optional[IntentionPlanner] = None,
    ) -> None:
        self.surface_planner = surface_planner or SurfacePlanner()
        self.relation_planner = relation_planner or RelationPlanner()
        self.intention_planner = intention_planner or IntentionPlanner()

    def plan(
        self,
        semcon: Dict[str, Any],
        raw_query: Optional[str] = None,
    ) -> QueryPlanBundle:
        payload = semcon if isinstance(semcon, dict) else {}
        return QueryPlanBundle(
            surface=self.surface_planner.plan(payload.get("surface", []), raw_query),
            relation=self.relation_planner.plan(payload.get("relation", []), raw_query),
            intention=self.intention_planner.plan(payload.get("intention", []), raw_query),
        )


def plan_query_from_semcon(
    semcon: Dict[str, Any],
    raw_query: Optional[str] = None,
) -> QueryPlanBundle:
    return QueryPlanner().plan(semcon, raw_query=raw_query)
