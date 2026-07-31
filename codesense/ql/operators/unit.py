"""`unit` 算子：把一个查询单元求值成片段。

单元求值后就是一个只有节点、没有边的 Frag，每个节点的证据里记着
它被哪个信号、以哪个 detail 命中，以及合成后的分数。这让单元可以直接
参与片段代数（``io & disk``）和图算子（``hop(io, disk)``），不需要额外转换。
"""

from __future__ import annotations

from collections import defaultdict

from codesense.ql.combine import combine
from codesense.ql.context import EvalContext
from codesense.ql.frag import Evidence, Frag, UnitHit
from codesense.ql.satisfiers.base import Satisfier
from codesense.ql.unit import QueryUnit

__all__ = ["eval_unit"]

#: 同一个 satisfier 内部多个词命中时的累加方式。
#:
#: 设计文档只规定了**信号之间**怎么合成（`QueryUnit.combine`），没规定
#: 信号内部。这里选 ``noisy_or``：多个词命中应当互相印证（比单个词强），
#: 但不该无限累加——否则往单元里多塞几个 derived 词就能把分数刷上去，
#: 而 derived 词恰恰是最不可信的一类。``noisy_or`` 有上界 1，正好挡住这个。
_WITHIN_SATISFIER = "noisy_or"


def eval_unit(unit: QueryUnit, ctx: EvalContext) -> Frag:
    """求值一个查询单元，产出带证据的片段。"""
    per_satisfier: dict[int, list[float]] = defaultdict(list)
    per_symbol_hits: dict[int, list[UnitHit]] = defaultdict(list)

    for satisfier in unit.satisfiers:
        if not isinstance(satisfier, Satisfier):
            raise TypeError(f"单元 {unit.name!r} 的 satisfier 类型不对: {type(satisfier).__name__}")
        for symbol_id, hits in satisfier.hits(unit.name, ctx).items():
            if not hits:
                continue
            per_symbol_hits[symbol_id].extend(hits)
            per_satisfier[symbol_id].append(combine(_WITHIN_SATISFIER, [h.score for h in hits]))

    elements = ctx.symbols.get_many(per_symbol_hits)
    return Frag(
        nodes=elements,
        evidence={
            symbol_id: Evidence(unit_hits=_scored(unit, hits, per_satisfier[symbol_id]))
            for symbol_id, hits in per_symbol_hits.items()
            if symbol_id in elements
        },
    )


def _scored(unit: QueryUnit, hits: list[UnitHit], parts: list[float]) -> tuple[UnitHit, ...]:
    """把合成后的单元总分作为一条 ``combined`` 证据附在原始证据之后。

    原始证据一条不删——可解释性来自完整的因果链，合成结果只是追加。
    """
    total = combine(unit.combine, parts)
    summary = UnitHit(
        unit=unit.name,
        signal=Evidence.COMBINED,
        detail=f"{unit.combine}({len(parts)} 个信号)",
        score=total,
    )
    return (*hits, summary)
