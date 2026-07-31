"""编译：自然语言 → 查询规格 → 执行计划。

    QuerySpec   中间表示，可手写、可从 LLM 的 JSON 构造
    plan()      **按预估选择性排序**——编译器里唯一做优化的地方
    Plan        有序步骤，可跑、可打印、可对比预估与实际

自然语言 → 规格 那一步要 LLM，所以在 `codesense.llm` 里，
不在这（QL 层按契约只用标准库）。
"""

from codesense.ql.compile.build import ScoredTerm, build_spec, infer_fields, infer_kinds
from codesense.ql.compile.cost import Estimate, estimate_hop, estimate_intent, estimate_unit
from codesense.ql.compile.emit import to_script
from codesense.ql.compile.partition import Cluster, partition
from codesense.ql.compile.plan import (
    Boost,
    Cohere,
    EvalUnit,
    Intent,
    Narrow,
    Plan,
    State,
    Step,
    Trace,
)
from codesense.ql.compile.planner import plan
from codesense.ql.compile.spec import GraphConstraint, QuerySpec
from codesense.ql.compile.validate import (
    Relation,
    relation_lift,
    validate_groups,
    validate_relations,
)

__all__ = [
    "Boost",
    "Cluster",
    "Cohere",
    "Estimate",
    "EvalUnit",
    "GraphConstraint",
    "Intent",
    "Narrow",
    "Plan",
    "QuerySpec",
    "Relation",
    "ScoredTerm",
    "State",
    "Step",
    "Trace",
    "build_spec",
    "estimate_hop",
    "estimate_intent",
    "estimate_unit",
    "infer_fields",
    "infer_kinds",
    "partition",
    "plan",
    "to_script",
    "relation_lift",
    "validate_groups",
    "validate_relations",
]
