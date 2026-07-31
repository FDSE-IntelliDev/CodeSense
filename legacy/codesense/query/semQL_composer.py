"""Legacy combined-SemQL compatibility layer.

New code should use :mod:`query_processing.planners` to create independent
surface, relation, and intention plans. The combined composer remains during
the migration because existing executors still consume ``semQL.json``.
"""

from typing import Any, Dict, List, Optional

from codesense.query.plan_models import QueryPlanBundle
from codesense.query.planners.query_planner import plan_query_from_semcon


class SemQLComposer:
    """Group surface / intention / relation SemCon by include / exclude property."""

    CONDITION_TYPES = ("surface", "intention", "relation")
    PROPERTIES = ("include", "exclude")

    def compose(self, semCon: Dict[str, Any], raw_query: Optional[str] = None) -> Dict[str, Any]:
        semQL = {
            "raw_query": raw_query,
            "conditions": {
                condition_type: {
                    "include": [],
                    "exclude": [],
                }
                for condition_type in self.CONDITION_TYPES
            },
        }

        if not isinstance(semCon, dict):
            return semQL

        for condition_type in self.CONDITION_TYPES:
            items = semCon.get(condition_type, [])
            if not isinstance(items, list):
                continue

            for item in items:
                if not isinstance(item, dict):
                    continue

                condition = dict(item)
                condition["type"] = condition_type
                property_name = self._normalize_property(condition.get("property"))
                condition["property"] = property_name
                semQL["conditions"][condition_type][property_name].append(condition)

        return semQL

    @staticmethod
    def _normalize_property(property_value: Any) -> str:
        property_name = str(property_value or "include").strip().lower()
        if property_name == "exclude":
            return "exclude"
        return "include"


def compose_semQL_from_semCon(semCon: Dict[str, Any], raw_query: Optional[str] = None) -> Dict[str, Any]:
    return SemQLComposer().compose(semCon, raw_query=raw_query)


def compose_query_plans_from_semCon(
    semCon: Dict[str, Any],
    raw_query: Optional[str] = None,
) -> QueryPlanBundle:
    """Compile SemCon into independent domain plans.

    This function is the planner-based replacement for the combined composer.
    It lives here temporarily to offer a discoverable migration path for older
    imports.
    """
    return plan_query_from_semcon(semCon, raw_query=raw_query)
