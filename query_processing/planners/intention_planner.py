"""Compile IntentionCon items into an intention-only semantic plan."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from definition import BASE_MODEL
from query_processing.plan_models import (
    IntentionClusterStagePlan,
    IntentionEmbeddingStagePlan,
    IntentionExecutionPlan,
    IntentionJudgeStagePlan,
    IntentionPlan,
    IntentionQueryProfile,
    IntentionRequirement,
    IntentionRequirements,
    IntentSpec,
)
from query_processing.planners.base import (
    extend_unique,
    normalize_optional_string,
    normalize_property,
    normalize_string_list,
)


class IntentionPlanner:
    """Own intention-field parsing and build an executor-ready query profile."""

    def plan(
        self,
        conditions: Iterable[Dict[str, Any]],
        raw_query: Optional[str] = None,
    ) -> IntentionPlan:
        include: List[IntentionRequirement] = []
        exclude: List[IntentionRequirement] = []

        for index, condition in enumerate(conditions or []):
            if not isinstance(condition, dict):
                continue
            requirement = self._build_requirement(condition, index)
            property_name = normalize_property(condition.get("property"))
            (exclude if property_name == "exclude" else include).append(requirement)

        normalized_raw_query = normalize_optional_string(raw_query)
        include_terms = self._build_query_terms(include)
        exclude_terms = self._build_query_terms(exclude)
        semantic_parts = (
            ([normalized_raw_query] if normalized_raw_query else []) + include_terms
        )
        has_requirements = bool(include or exclude)

        # The nested policy dataclasses serialize the complete physical contract:
        # Cluster guards/rescue, ordered absolute decisions, directional conflicts,
        # guarded Q40/Q80 adaptation, and gray-only LLM limits.  Keeping these
        # values in intention_semql makes Executor behavior reproducible and lets
        # a future optimizer replace policies without changing execution code.
        return IntentionPlan(
            raw_query=normalized_raw_query,
            query_profile=IntentionQueryProfile(
                semantic_text=" ".join(semantic_parts),
                include_terms=include_terms,
                exclude_terms=exclude_terms,
            ),
            requirements=IntentionRequirements(include=include, exclude=exclude),
            execution_plan=IntentionExecutionPlan(
                cluster=IntentionClusterStagePlan(
                    enabled=bool(include and semantic_parts),
                ),
                embedding=IntentionEmbeddingStagePlan(
                    enabled=bool(include_terms or exclude_terms),
                ),
                llm_judge=IntentionJudgeStagePlan(
                    enabled=has_requirements,
                    model=BASE_MODEL,
                ),
            ),
        )

    @staticmethod
    def _build_requirement(
        condition: Dict[str, Any],
        index: int,
    ) -> IntentionRequirement:
        intent = condition.get("intent")
        if not isinstance(intent, dict):
            intent = {}

        return IntentionRequirement(
            clause_id=f"intention_{index}",
            intent=IntentSpec(
                action=normalize_optional_string(intent.get("action")),
                object=normalize_optional_string(intent.get("object")),
            ),
            intent_statement=normalize_optional_string(condition.get("intent_statement")),
            aspect=IntentionPlanner._normalize_lower_optional(condition.get("aspect")),
            non_functional_type=IntentionPlanner._normalize_lower_optional(
                condition.get("non_functional_type")
            ),
            keywords=normalize_string_list(condition.get("keywords")),
            description=normalize_optional_string(condition.get("description")),
        )

    @staticmethod
    def _build_query_terms(
        requirements: Iterable[IntentionRequirement],
    ) -> List[str]:
        terms: List[str] = []
        for requirement in requirements:
            for value in (requirement.intent.action, requirement.intent.object):
                if value:
                    extend_unique(terms, [value])
            extend_unique(terms, requirement.keywords)
        return terms

    @staticmethod
    def _normalize_lower_optional(value: Any) -> Optional[str]:
        normalized = normalize_optional_string(value)
        return normalized.lower() if normalized is not None else None
