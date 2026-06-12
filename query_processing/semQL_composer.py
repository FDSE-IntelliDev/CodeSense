"""
Compose extracted SemCon conditions into a SemQL structure.

This module does not decide concrete AND / OR / NOT execution logic. It only
organizes atomic SemCon conditions by condition type and property so later
search, filter, and reranking stages can decide how to execute them.
"""

from typing import Any, Dict, List, Optional


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