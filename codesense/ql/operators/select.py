"""Narrowing operators: filter by attribute, take the top K, filter by degree.

These are the three most common ways to narrow when writing QL by hand (gaps
4 and 5 in ``tests/integration/test_handwritten_queries.py``). Without them a
script falls back to list comprehensions, which cannot reach the evidence or
preserve fragment structure.
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
    """Narrow by element attributes.

    Conditions combine with **and**. Passing ``None`` disables a condition,
    which differs from passing an empty sequence -- that filters everything
    out, being an explicitly empty condition.

    ``where`` is the escape hatch for anything the named conditions cannot
    express, but prefer the named ones: a compiler can analyse them and the
    evidence can record them.
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
    """Take the top n by score.

    ``by`` selects which unit's score to rank on; without it, the sum across
    units is used. Ranking needs to read evidence, which is why this is an
    operator rather than a `sorted()` in the script -- scripts cannot reach
    the evidence structure.

    Ties break on ascending symbol id so results are reproducible.
    """
    if n < 0:
        raise ValueError(f"top() needs a non-negative n, got {n}")
    ordered = sorted(frag.nodes, key=lambda sid: (-score_of(frag, sid, by), sid))
    return frag.induced(ordered[:n])


def score_of(frag: Frag, symbol_id: int, by: str | None = None) -> float:
    """One node's score. With ``by=None``, the sum across all units."""
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
    """Narrow by degree in the graph.

    Degree is measured over the **whole graph**, not within the fragment --
    "this function is called from many places" is about its standing in the
    codebase, not among the current candidates.

    The parameters are ``min_in``/``max_in`` rather than the draft's
    ``in_``/``out``: the latter needed a trailing underscore to dodge a
    keyword and could not express a range.

    Typical uses: ``degree(frag, ctx, max_in=0)`` for entry points,
    ``degree(frag, ctx, min_in=20)`` for widely called utilities.
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
