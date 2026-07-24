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
    include_clause_operator: str = "intersect"
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
    include_terms: List[str] = field(default_factory=list)
    exclude_terms: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class IntentionResultLogic:
    include_operator: str = "all"
    exclude_operator: str = "any"


@dataclass(frozen=True)
class IntentionClusterStagePlan:
    """Physical policy for coarse cluster-level intention filtering."""

    # Planner 是否允许 Cluster 阶段运行；为 False 时候选直接进入下一阶段。
    enabled: bool = True
    # 只有候选数量 >= 8 才执行聚类；少于 8 个候选时跳过聚类并全部保留。
    min_candidate_count: int = 8
    # 层次聚类的余弦距离合并阈值；距离 <= 0.3 的样本可被合并到同一簇。
    distance_threshold: float = 0.3
    # 未触发保护条件时，按簇相关性从高到低保留前 60% 的 clusters。
    keep_top_cluster_ratio: float = 0.6
    # 只有 cluster 数量 >= 3 才允许丢弃低分簇；cluster 数量 <= 2 时全部保留。
    min_cluster_count_to_filter: int = 3
    # 最高与最低 cluster score 的差值必须 >= 0.05 才允许过滤，否则全部保留。
    min_score_spread: float = 0.05
    # 在全部 clusters 中，得分最高的约 10% 标记为 priority_1。
    priority_1_ratio: float = 0.1
    # 在全部 clusters 中，紧随 priority_1 的约 20% 标记为 priority_2；
    # 其余被保留的 clusters 标记为 priority_3。
    priority_2_ratio: float = 0.2
    # 低分 cluster 不在粗排阶段不可逆删除，而是作为 rescue candidates 继续进入
    # 候选级 Term Embedding；这样“所在簇弱、候选自身强”的元素仍可被救回。
    defer_low_cluster_discard_to_embedding: bool = True


@dataclass(frozen=True)
class IntentionConflictPolicy:
    """Planner-owned rules for routing contradictory signals to the gray zone."""

    # 只有 priority_1 / priority_2 这类强相关 cluster 才能挽救低 include 候选。
    cluster_support_tiers: List[str] = field(
        default_factory=lambda: ["priority_1", "priority_2"]
    )
    # include score < 0.35 才可能形成“Cluster 强支持、Embedding 弱”的冲突。
    cluster_support_include_ceiling: float = 0.35
    # 归一化 Cluster score 至少高出 include score 0.25 才视为有效支持冲突。
    cluster_support_min_gap: float = 0.25
    # include score >= 0.45 且 exclude 落入灰区时，形成正负意图极性冲突。
    polarity_include_floor: float = 0.45


@dataclass(frozen=True)
class IntentionAdaptiveDistributionPlan:
    """Query-local percentile policy for candidates unresolved by hard rules."""

    # Planner 是否允许在绝对规则之后使用查询内动态分布。
    enabled: bool = True
    # 原始候选至少达到 10 个，百分位切分才具有基本统计意义。
    min_candidate_count: int = 10
    # 绝对规则处理后至少剩余 8 个未决候选，才执行动态切分。
    min_unresolved_candidate_count: int = 8
    # 使用 Q10 和 Q90 计算稳健分布跨度，降低极端离群值的影响。
    robust_spread_lower_quantile: float = 0.1
    robust_spread_upper_quantile: float = 0.9
    # Q90(S)-Q10(S) 必须 >= 0.10；分数过于集中时，没有充分依据把它们区分为强、中、弱三档，全部进入灰区。
    min_robust_score_spread: float = 0.1
    # Q40 将相对后 40% 与中间区域分开。
    lower_quantile: float = 0.4
    # Q80 将相对前 20% 与中间区域分开。
    upper_quantile: float = 0.8
    # 距离分位边界 0.01 内视为近似同分，不执行激进的直接保留或丢弃。
    tie_tolerance: float = 0.01
    # 相对前 20% 仍需 include >= 0.45，才允许动态直接保留。
    adaptive_accept_include_floor: float = 0.45
    # 动态直接保留还要求 exclude < 0.25，避免潜在负向语义。
    adaptive_accept_exclude_ceiling: float = 0.25
    # 相对后 40% 只有 include < 0.35 且无冲突时才动态丢弃。
    adaptive_discard_include_ceiling: float = 0.35


@dataclass(frozen=True)
class IntentionEmbeddingStagePlan:
    """Physical policy for positive/negative term-embedding decisions."""

    # Planner 是否允许项目 Term Embedding 阶段运行。
    enabled: bool = True
    # include score < 0.20 且没有 Cluster 支持冲突时，属于绝对弱负向。
    include_hard_discard_threshold: float = 0.2
    # include score >= 0.55、exclude < 0.35 时，属于绝对明确正向。
    include_accept_threshold: float = 0.55
    # exclude score 从 0.35 开始进入灰区；区间为 [0.35, 0.6)。
    exclude_gray_threshold: float = 0.35
    # exclude score >= 0.6 表示明确命中任一负向意图，候选直接丢弃。
    exclude_discard_threshold: float = 0.6
    # Cluster score 在正向综合排序分数中的权重；仅存在 Cluster score 时参与计算。
    cluster_weight: float = 0.4
    # include embedding score 在正向综合排序分数中的权重。
    include_weight: float = 0.6
    # 最终排序分数中扣除 exclude_score * 0.4，负向匹配越强排名越低。
    exclude_penalty_weight: float = 0.4
    # 每个候选最多抽取 80 个术语参与匹配，限制单候选的两两评分开销。
    max_candidate_terms: int = 80
    # 无可用 embedding term 且 Judge 已启用时，将候选标为灰区并交给 LLM 判断。
    no_term_policy: str = "gray_for_llm"
    # 在 Embedding 阶段未丢弃的候选中，综合分最高的约 10% 标记为 priority_1。
    priority_1_ratio: float = 0.1
    # 在未丢弃候选中，紧随 priority_1 的约 20% 标记为 priority_2；其余为 priority_3。
    priority_2_ratio: float = 0.2
    # 决策阶段顺序是执行契约的一部分；Executor 不自行调整硬规则、动态分布和灰区顺序。
    decision_order: List[str] = field(
        default_factory=lambda: [
            "exclude_hard_veto",
            "absolute_positive",
            "absolute_weak_negative",
            "collect_unresolved",
            "distribution_guard",
            "adaptive_distribution",
        ]
    )
    conflict_policy: IntentionConflictPolicy = field(
        default_factory=IntentionConflictPolicy
    )
    adaptive_distribution: IntentionAdaptiveDistributionPlan = field(
        default_factory=IntentionAdaptiveDistributionPlan
    )


@dataclass(frozen=True)
class IntentionJudgeStagePlan:
    """Physical policy for judging only ambiguous embedding candidates."""

    # Planner 是否允许调用 LLM Judge；为 False 时按 disabled_gray_policy 处理灰区。
    enabled: bool = True
    # LLM 只接收 Embedding 阶段标记为 gray 的候选，不重复判断 confident candidates。
    candidate_source: str = "embedding_gray_zone"
    # Judge 使用的模型；空字符串表示由 Executor 回退到项目配置的 BASE_MODEL。
    model: str = ""
    # 每次 LLM API 请求最多发送 5 个灰区代码元素，限制单次 prompt 大小。
    batch_size: int = 5
    # 每个候选最多携带 3000 个代码字符，超出部分在构造 prompt 时截断。
    max_code_chars: int = 3000
    # LLM 调用失败或返回 uncertain 时保留该候选，避免外部服务异常造成错误过滤。
    failure_policy: str = "keep_uncertain"
    # Judge 被 Planner 禁用时仍保留灰区候选；可改为其他策略以选择丢弃。
    disabled_gray_policy: str = "keep"


@dataclass(frozen=True)
class IntentionExecutionPlan:
    cluster: IntentionClusterStagePlan = field(
        default_factory=IntentionClusterStagePlan
    )
    embedding: IntentionEmbeddingStagePlan = field(
        default_factory=IntentionEmbeddingStagePlan
    )
    llm_judge: IntentionJudgeStagePlan = field(
        default_factory=IntentionJudgeStagePlan
    )


@dataclass(frozen=True)
class IntentionPlan(SerializablePlan):
    version: str = PLAN_VERSION
    kind: str = "intention"
    raw_query: Optional[str] = None
    query_profile: IntentionQueryProfile = field(default_factory=IntentionQueryProfile)
    requirements: IntentionRequirements = field(default_factory=IntentionRequirements)
    result_logic: IntentionResultLogic = field(default_factory=IntentionResultLogic)
    execution_plan: IntentionExecutionPlan = field(
        default_factory=IntentionExecutionPlan
    )


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
