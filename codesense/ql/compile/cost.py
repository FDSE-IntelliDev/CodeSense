"""Estimating an operator's output size and cost without running it.

This is what makes ordering optimisable at all. The inverted index carries
`df` for every term, so how many symbols a lexical unit will match is a
lookup rather than an execution. Without it, "order decided by the query"
would be guesswork.

Estimates only need the right **order of magnitude**: telling "a few" from
"a few hundred" from "tens of thousands" decides the ordering, and getting
527 rather than 480 adds nothing.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from codesense.ql.context import EvalContext
from codesense.ql.satisfiers.base import Satisfier
from codesense.ql.satisfiers.lexical import AnnotationSatisfier, LexicalSatisfier, ModifierSatisfier
from codesense.ql.unit import QueryUnit

__all__ = ["Estimate", "estimate_hop", "estimate_intent", "estimate_unit"]

#: How many lookups one LLM judgement is worth, so costs are comparable.
#: `intent` is orders of magnitude more expensive than any lookup operator,
#: and the ordering has to reflect that.
INTENT_COST_FACTOR = 5_000.0

#: Assumed pass rate for intent judging. Half, absent any prior.
INTENT_PASS_RATE = 0.5

#: Fallback average degree. The real value is counted from the edges; this
#: only applies when there are none.
FALLBACK_DEGREE = 4.0


@dataclass(frozen=True, slots=True)
class Estimate:
    """Predicted output size and cost of one step.

    ``rows`` determines how much input the next step gets; ``cost`` decides
    whether this step is worth doing first. Both are order-of-magnitude
    estimates, not exact figures.
    """

    rows: int
    cost: float
    detail: str = ""

    def __str__(self) -> str:
        note = f" ({self.detail})" if self.detail else ""
        return f"~{self.rows} rows / cost {self.cost:,.0f}{note}"


def estimate_unit(unit: QueryUnit, ctx: EvalContext) -> Estimate:
    """Estimate how many symbols a unit will match.

    Terms are unioned under an **independence** assumption:
    ``N * (1 - Prod(1 - df_i/N))``. Independence plainly does not hold --
    `buffer` and `buf` are highly correlated -- but it yields an upper bound,
    and ordering only needs that bound to separate magnitudes.
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
    return Estimate(rows=rows, cost=cost, detail=f"{terms} terms")


def estimate_hop(
    src_rows: int, ctx: EvalContext, *, hops: tuple[int, int], dst_rows: int | None = None
) -> Estimate:
    """Estimate what a graph constraint from ``src_rows`` starts will leave.

    Two stages: the **reachable set** by powers of the average degree, which
    is the cost; then discounted by dst's density for the **result**, since
    `hop` keeps only paths that reach dst rather than everything reachable.
    Omitting the second stage overestimates systematically -- 18689 predicted
    against 2022 actual, measured.
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
        detail=f"avg degree {degree:.1f}",
    )


def estimate_intent(rows: int) -> Estimate:
    """Estimate the cost of intent judging.

    It **removes few rows while costing orders of magnitude more**, so it
    always belongs last. This estimate exists so the planner reaches that
    conclusion itself rather than following a hard-coded rule.
    """
    return Estimate(
        rows=round(rows * INTENT_PASS_RATE),
        cost=rows * INTENT_COST_FACTOR,
        detail="LLM",
    )


def _average_degree(ctx: EvalContext) -> float:
    """Average degree by sampling. Counting everything is too expensive on a
    large project, and an estimate only needs the right magnitude."""
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
