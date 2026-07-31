"""收窄类算子：按属性筛、按分数取前 K、按度数筛。

这三个都是手写 QL 时最常用的收窄方式（``tests/integration/
test_handwritten_queries.py`` 的缺口 4 和 5），没有它们脚本里就得写列表推导，
而列表推导拿不到证据、也没法保持片段结构。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from codesense.ql.context import EvalContext
from codesense.ql.frag import Element, Frag

__all__ = ["degree", "only", "score_of", "top"]


def only(
    frag: Frag,
    *,
    kind: str | Sequence[str] | None = None,
    file: str | Sequence[str] | None = None,
    language: str | None = None,
    where: Callable[[Element], bool] | None = None,
) -> Frag:
    """按元素属性收窄。

    多个条件之间是**与**关系。给 ``None`` 表示该条件不生效——
    这与「给空序列」不同，后者会筛掉所有元素（明确的空条件）。

    ``where`` 是逃生舱：属性筛不了的用它，但优先用具名条件，
    因为具名条件能被编译器分析、被证据记录。
    """
    kinds = _as_set(kind)
    files = _as_set(file)

    def keep(element: Element) -> bool:
        if kinds is not None and element.kind not in kinds:
            return False
        if files is not None and element.file not in files:
            return False
        if language is not None and element.language != language:
            return False
        return not (where is not None and not where(element))

    return frag.induced(sid for sid, element in frag.nodes.items() if keep(element))


def top(frag: Frag, n: int, *, by: str | None = None) -> Frag:
    """按分数取前 n 个。

    ``by`` 指定按哪个单元的分数排；不给则按所有单元分数之和。
    排序需要读证据，所以这是 QL 层的算子而不是脚本里的 `sorted()`——
    脚本拿不到证据结构。

    并列时按 symbol_id 升序，保证结果可复现。
    """
    if n < 0:
        raise ValueError(f"top 的 n 不能为负，收到 {n}")
    ordered = sorted(frag.nodes, key=lambda sid: (-score_of(frag, sid, by), sid))
    return frag.induced(ordered[:n])


def score_of(frag: Frag, symbol_id: int, by: str | None = None) -> float:
    """某个节点的分数。``by=None`` 时取所有单元之和。"""
    scores = frag.evidence_for(symbol_id).scores
    return scores.get(by, 0.0) if by is not None else sum(scores.values())


def degree(
    frag: Frag,
    ctx: EvalContext,
    *,
    edge: str | Sequence[str] | None = "calls",
    min_in: int | None = None,
    max_in: int | None = None,
    min_out: int | None = None,
    max_out: int | None = None,
) -> Frag:
    """按图上的度数收窄。

    度数是**全图**的度数，不是片段内的——「这个函数被很多地方调用」
    问的是它在整个代码库里的地位，不是它在当前候选集里的地位。

    参数取名 ``min_in`` / ``max_in`` 而不是设计初稿的 ``in_`` / ``out``：
    后者要靠下划线避开关键字，而且表达不了区间。

    典型用法：``degree(frag, ctx, max_in=0)`` 取入口点，
    ``degree(frag, ctx, min_in=20)`` 找被广泛调用的工具方法。
    """
    kinds = None if edge is None else tuple(_as_set(edge) or ())

    def keep(symbol_id: int) -> bool:
        if min_in is None and max_in is None and min_out is None and max_out is None:
            return True
        incoming = len(ctx.edges.in_edges(symbol_id, kinds=kinds))
        outgoing = len(ctx.edges.out_edges(symbol_id, kinds=kinds))
        return _within(incoming, min_in, max_in) and _within(outgoing, min_out, max_out)

    return frag.induced(sid for sid in frag.nodes if keep(sid))


def _within(value: int, low: int | None, high: int | None) -> bool:
    return (low is None or value >= low) and (high is None or value <= high)


def _as_set(value: str | Sequence[str] | None) -> frozenset[str] | None:
    if value is None:
        return None
    return frozenset([value] if isinstance(value, str) else value)
