"""不执行就估算算子的输出规模与代价。

这是编排能优化的**前提**。倒排索引里每个 term 都带 `df`，
所以一个词法单元会命中多少符号，查表就能估出来——不必真跑一遍。
没有这一步，「顺序由查询决定」就只能靠猜。

估计只求**量级对**：区分「几个」「几百个」「几万个」足以决定顺序，
把 527 估成 480 没有额外价值。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from codesense.ql.context import EvalContext
from codesense.ql.satisfiers.base import Satisfier
from codesense.ql.satisfiers.lexical import AnnotationSatisfier, LexicalSatisfier, ModifierSatisfier
from codesense.ql.unit import QueryUnit

__all__ = ["Estimate", "estimate_hop", "estimate_intent", "estimate_unit"]

#: 一次 LLM 判定相当于多少次查表。用来让代价可比——
#: `intent` 比任何查表算子贵几个数量级，排序时必须体现出来。
INTENT_COST_FACTOR = 5_000.0

#: 意图判定的通过率假设。没有先验时按一半算。
INTENT_PASS_RATE = 0.5

#: 估不出来时假定的平均度数。真实值从边表数，这个只在没有边时兜底。
FALLBACK_DEGREE = 4.0


@dataclass(frozen=True, slots=True)
class Estimate:
    """一步的预计输出规模与代价。

    ``rows`` 决定下一步有多少输入，``cost`` 决定这一步值不值得先做。
    两者都是量级估计，不是精确值。
    """

    rows: int
    cost: float
    detail: str = ""

    def __str__(self) -> str:
        note = f"（{self.detail}）" if self.detail else ""
        return f"~{self.rows} 个 / 代价 {self.cost:,.0f}{note}"


def estimate_unit(unit: QueryUnit, ctx: EvalContext) -> Estimate:
    """估一个查询单元会命中多少符号。

    多个词之间按**独立**假设求并集：``N · (1 − Π(1 − dfᵢ/N))``。
    独立假设当然不成立（`buffer` 和 `buf` 高度相关），但它给出的是上界，
    而排序只需要上界能区分量级。
    """
    total = max(ctx.symbols.count(), 1)
    miss = 1.0
    cost = 0.0
    terms = 0
    for satisfier in unit.satisfiers:
        for term in _terms_of(satisfier):
            for surface in _surfaces(term, ctx):
                info = ctx.postings.term_info(surface)
                if info is None:
                    continue
                terms += 1
                cost += len(ctx.postings.lookup(surface))
                miss *= 1.0 - min(info.df / total, 1.0)
    rows = round(total * (1.0 - miss))
    return Estimate(rows=rows, cost=cost, detail=f"{terms} 个词")


def estimate_hop(
    src_rows: int, ctx: EvalContext, *, hops: tuple[int, int], dst_rows: int | None = None
) -> Estimate:
    """估从 ``src_rows`` 个起点出发的图约束会留下多少符号。

    分两步：先按平均度数的幂次估**可达集**（这是代价），
    再按 dst 的密度折一次估**结果**——`hop` 只保留能到达 dst 的路径，
    不是把可达的都留下。少了后半步会系统性高估（实测 18689 vs 实际 2022）。
    """
    total = max(ctx.symbols.count(), 1)
    degree = _average_degree(ctx)
    reach = float(src_rows)
    touched = 0.0
    for _ in range(max(hops[1], 0)):
        reach *= degree
        touched += reach
        if touched >= total:
            break
    touched = min(float(total), touched)
    density = 1.0 if dst_rows is None else min(dst_rows / total, 1.0)
    return Estimate(
        rows=min(total, round(touched * density)),
        cost=touched,
        detail=f"平均度 {degree:.1f}",
    )


def estimate_intent(rows: int) -> Estimate:
    """估意图判定的代价。

    它**不减少多少行，却贵几个数量级**——所以排序时它总该排在最后。
    这个估计存在的意义就是让规划器自己得出这个结论，而不是写死一条规则。
    """
    return Estimate(
        rows=round(rows * INTENT_PASS_RATE),
        cost=rows * INTENT_COST_FACTOR,
        detail="LLM",
    )


def _average_degree(ctx: EvalContext) -> float:
    """采样估平均度数。全量数一遍在大项目上太贵，而估计只需要量级对。"""
    sampled = 0
    total = 0
    for symbol_id in range(1, min(ctx.symbols.count(), 200) + 1):
        if ctx.symbols.get(symbol_id) is None:
            continue
        sampled += 1
        total += ctx.edges.degree(symbol_id)
    return max(total / sampled, 1.0) if sampled else FALLBACK_DEGREE


def _terms_of(satisfier: object) -> Sequence[str]:
    if isinstance(satisfier, LexicalSatisfier):
        return [term.value for term in satisfier.terms]
    if isinstance(satisfier, AnnotationSatisfier):
        return [term.value for term in satisfier.units] + list(satisfier.names)
    if isinstance(satisfier, ModifierSatisfier):
        return list(satisfier.modifiers)
    if isinstance(satisfier, Satisfier):
        return []
    return []


def _surfaces(term: str, ctx: EvalContext) -> list[str]:
    return [term, *(expansion.target for expansion in ctx.expansion.expand(term))]
