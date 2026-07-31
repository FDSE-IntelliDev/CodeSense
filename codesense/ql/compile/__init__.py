"""编译：自然语言 → 查询规格 → 执行计划。

    QuerySpec   中间表示，可手写、可从 LLM 的 JSON 构造
    plan()      **按预估选择性排序**——编译器里唯一做优化的地方
    Plan        有序步骤，可跑、可打印、可对比预估与实际

自然语言 → 规格 那一步要 LLM，所以在 `codesense.llm` 里，
不在这（QL 层按契约只用标准库）。
"""

from codesense.ql.compile.cost import Estimate, estimate_hop, estimate_intent, estimate_unit
from codesense.ql.compile.plan import (
    Boost,
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

__all__ = [
    "Boost",
    "Estimate",
    "EvalUnit",
    "GraphConstraint",
    "Intent",
    "Narrow",
    "Plan",
    "QuerySpec",
    "State",
    "Step",
    "Trace",
    "estimate_hop",
    "estimate_intent",
    "estimate_unit",
    "plan",
]
