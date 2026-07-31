"""The `unit` operator: evaluating a query unit into a fragment.

Evaluating a unit yields a Frag with nodes but no edges, where each node's
evidence records which signal matched it, on what detail, and the combined
score. That lets units take part directly in fragment algebra (``io & disk``)
and graph operators (``hop(io, disk)``) with no conversion step.
"""

from __future__ import annotations

from collections import defaultdict

from codesense.ql.combine import combine
from codesense.ql.context import EvalContext
from codesense.ql.frag import Evidence, Frag, UnitHit
from codesense.ql.satisfiers.base import Satisfier
from codesense.ql.unit import QueryUnit

__all__ = ["eval_unit"]

#: How several term hits inside one satisfier accumulate.
#:
#: The design fixes how signals combine with each other
#: (`QueryUnit.combine`) but not what happens within a signal. ``noisy_or``
#: is chosen here: several term hits should reinforce each other, beating a
#: single hit, without accumulating without bound -- otherwise adding a few
#: more derived terms would inflate the score, and derived terms are the
#: least trustworthy kind. ``noisy_or`` is bounded by 1, which stops that.
_WITHIN_SATISFIER = "noisy_or"


def eval_unit(unit: QueryUnit, ctx: EvalContext) -> Frag:
    """Evaluate a query unit into a fragment carrying evidence."""
    per_satisfier: dict[int, list[float]] = defaultdict(list)
    per_symbol_hits: dict[int, list[UnitHit]] = defaultdict(list)

    for satisfier in unit.satisfiers:
        if not isinstance(satisfier, Satisfier):
            raise TypeError(
                f"unit {unit.name!r} has a satisfier of the wrong type: {type(satisfier).__name__}"
            )
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
    """Append the combined unit score as one ``combined`` piece of evidence.

    Nothing original is removed -- explainability comes from the whole causal
    chain, and the combined result is only added to it.
    """
    total = combine(unit.combine, parts)
    summary = UnitHit(
        unit=unit.name,
        signal=Evidence.COMBINED,
        detail=f"{unit.combine}({len(parts)} signals)",
        score=total,
    )
    return (*hits, summary)
