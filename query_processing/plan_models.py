"""Typed query-plan models shared by the domain-specific planners.

The models intentionally contain only normalized, executor-facing data. Raw
SemCon parsing belongs to the corresponding planner and should not leak into
executors or filters.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


PLAN_VERSION = "1.0"


class SerializablePlan:
    """Mixin for JSON-serializable plan dataclasses."""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SurfaceTerm:
    value: str
    source: str


@dataclass(frozen=True)
class SurfaceTermExpression:
    operator: str = "or"
    terms: List[SurfaceTerm] = field(default_factory=list)


@dataclass(frozen=True)
class SurfaceKeywordGroup:
    group_id: str
    property: str
    term_expression: SurfaceTermExpression
    reason: Optional[str] = None


@dataclass(frozen=True)
class SurfaceGroupLogic:
    groups: List[str]
    graph_scope: str
    hop_count: int
    reason: Optional[str] = None


@dataclass(frozen=True)
class SurfaceGroupExpression:
    operator: str
    groups: List[str]
    rules: List[SurfaceGroupLogic] = field(default_factory=list)


@dataclass(frozen=True)
class SurfaceMatchSpec:
    kind: str = "unknown"
    code_element_types: List[str] = field(default_factory=list)
    code_text: Optional[str] = None


@dataclass(frozen=True)
class SurfaceRetrievalClause:
    clause_id: str
    property: str
    keyword_groups: List[SurfaceKeywordGroup] = field(default_factory=list)
    group_expression: Optional[SurfaceGroupExpression] = None


@dataclass(frozen=True)
class SurfaceConditionResultExpression:
    operator: str
    include_clause_id: Optional[str]
    exclude_clause_id: Optional[str]


@dataclass(frozen=True)
class SurfaceConditionPlan:
    condition_id: str
    match: SurfaceMatchSpec
    include_clause: Optional[SurfaceRetrievalClause]
    exclude_clause: Optional[SurfaceRetrievalClause]
    result_expression: SurfaceConditionResultExpression


@dataclass(frozen=True)
class SurfaceConditionMergeGroup:
    operator: str
    condition_ids: List[str]
    match_kind: str
    code_element_types: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class SurfaceConditionExpression:
    operator: str = "union"
    groups: List[SurfaceConditionMergeGroup] = field(default_factory=list)
    exclude_only_condition_ids: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class SurfacePlan(SerializablePlan):
    version: str = PLAN_VERSION
    kind: str = "surface"
    raw_query: Optional[str] = None
    conditions: List[SurfaceConditionPlan] = field(default_factory=list)
    condition_expression: SurfaceConditionExpression = field(
        default_factory=SurfaceConditionExpression
    )


@dataclass(frozen=True)
class CallAnchor:
    file_name: Optional[str]
    symbol_name: str
    hop_count: Optional[int]


@dataclass(frozen=True)
class RelationGraphConstraint:
    role: Optional[str] = None


@dataclass(frozen=True)
class RelationClause:
    clause_id: str
    file_path: Optional[str]
    graph_constraint: Optional[RelationGraphConstraint]
    caller: Optional[CallAnchor]
    callee: Optional[CallAnchor]
    code_ql: Optional[str]
    description: Optional[str]


@dataclass(frozen=True)
class RelationFilters:
    include: List[RelationClause] = field(default_factory=list)
    exclude: List[RelationClause] = field(default_factory=list)


@dataclass(frozen=True)
class RelationResultLogic:
    clause_operator: str = "intersect"
    include_clause_operator: str = "union"
    include_operator: str = "intersect_candidates"
    exclude_clause_operator: str = "union"
    exclude_operator: str = "subtract"


@dataclass(frozen=True)
class RelationPlan(SerializablePlan):
    version: str = PLAN_VERSION
    kind: str = "relation"
    raw_query: Optional[str] = None
    filters: RelationFilters = field(default_factory=RelationFilters)
    result_logic: RelationResultLogic = field(default_factory=RelationResultLogic)


@dataclass(frozen=True)
class IntentSpec:
    action: Optional[str]
    object: Optional[str]


@dataclass(frozen=True)
class IntentionRequirement:
    clause_id: str
    intent: IntentSpec
    intent_statement: Optional[str]
    aspect: Optional[str]
    non_functional_type: Optional[str]
    keywords: List[str]
    description: Optional[str]


@dataclass(frozen=True)
class IntentionRequirements:
    include: List[IntentionRequirement] = field(default_factory=list)
    exclude: List[IntentionRequirement] = field(default_factory=list)


@dataclass(frozen=True)
class IntentionQueryProfile:
    semantic_text: str = ""
    terms: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class IntentionResultLogic:
    include_operator: str = "all"
    exclude_operator: str = "any"


@dataclass(frozen=True)
class IntentionPlan(SerializablePlan):
    version: str = PLAN_VERSION
    kind: str = "intention"
    raw_query: Optional[str] = None
    query_profile: IntentionQueryProfile = field(default_factory=IntentionQueryProfile)
    requirements: IntentionRequirements = field(default_factory=IntentionRequirements)
    result_logic: IntentionResultLogic = field(default_factory=IntentionResultLogic)


@dataclass(frozen=True)
class QueryPlanBundle:
    """In-memory result of planning one natural-language query."""

    surface: SurfacePlan
    relation: RelationPlan
    intention: IntentionPlan

    def manifest(
        self,
        *,
        raw_query: Optional[str],
        surface_path: str = "surface_semql.json",
        relation_path: str = "relation_semql.json",
        intention_path: str = "intention_semql.json",
    ) -> Dict[str, Any]:
        return {
            "version": PLAN_VERSION,
            "raw_query": raw_query,
            "plans": {
                "surface": surface_path,
                "relation": relation_path,
                "intention": intention_path,
            },
        }
