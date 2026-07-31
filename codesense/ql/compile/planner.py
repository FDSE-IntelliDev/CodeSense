"""Ordering a query spec into an execution plan.

**The only place the compiler genuinely optimises anything.** The operator
set is fixed; what decides speed is the order -- and the order is optimisable
because the inverted index's `df` lets us estimate how many symbols each unit
will match before running it (the `cost` module).

This module is where the design's "order decided by the query, not hardcoded"
(``docs/design/01-motivation.md``) actually happens.
"""

from __future__ import annotations

from dataclasses import dataclass

from codesense.ql.compile.cost import estimate_unit
from codesense.ql.compile.plan import Boost, Cohere, EvalUnit, Intent, Narrow, Plan, Step
from codesense.ql.compile.spec import QuerySpec
from codesense.ql.context import EvalContext
from codesense.ql.satisfiers.lexical import AnnotationSatisfier, LexicalSatisfier, ModifierSatisfier
from codesense.ql.unit import QueryUnit

__all__ = ["USELESS_RATIO", "plan"]

#: Coverage above which a unit is discarded.
#:
#: Set high after **getting burned**: at 0.5, on petclinic's 142 symbols,
#: the planner discarded the only unit containing the answer -- a ten-term
#: union easily exceeds half -- leaving an irrelevant two-term unit. That
#: repeats the mistake the ICF floor made: **excluding what the caller
#: explicitly asked for instead of downweighting it**. Dropping a unit saves
#: one posting scan and can cost the answer.
#:
#: Now only units matching nearly everything are dropped; breadth otherwise
#: is handled by `_specificity` weighting.
USELESS_RATIO = 0.9

#: Ceiling on candidates handed to `intent`. It costs thousands of lookups,
#: so its input has to be capped first.
INTENT_INPUT_CAP = 60

#: Ratio at which a graph constraint is worth reversing to start from the
#: smaller side. When the sides are comparable the direction does not matter.
ASYMMETRY = 3

#: Floor on unit weight. Even a very broad unit carries some information and
#: should not go to zero.
MIN_UNIT_WEIGHT = 0.15


def _specificity(rows: int, total: int) -> float:
    """A unit's specificity: the more it covers, the less each hit is worth.

    ICF's logic applied at the **unit** level. Without it a unit covering 38%
    of the codebase adds with the same weight as one covering 1%, which on
    netty is exactly what pushed the answers out of the top 60: the real
    targets only matched the narrow unit and lost to noise that matched three
    terms in the broad one.
    """
    return max(MIN_UNIT_WEIGHT, 1.0 - rows / max(total, 1))


def _reweighted(unit: QueryUnit, factor: float) -> QueryUnit:
    """Scale every satisfier's weight in a unit by its specificity."""
    scaled: list[object] = []
    for satisfier in unit.satisfiers:
        if isinstance(satisfier, LexicalSatisfier):
            scaled.append(
                LexicalSatisfier(
                    terms=satisfier.terms,
                    weight=satisfier.weight * factor,
                    fields=satisfier.fields,
                )
            )
        elif isinstance(satisfier, AnnotationSatisfier):
            scaled.append(
                AnnotationSatisfier(
                    units=satisfier.units,
                    names=satisfier.names,
                    weight=satisfier.weight * factor,
                )
            )
        elif isinstance(satisfier, ModifierSatisfier):
            scaled.append(
                ModifierSatisfier(modifiers=satisfier.modifiers, weight=satisfier.weight * factor)
            )
        else:
            scaled.append(satisfier)
    return QueryUnit(
        name=unit.name, concept=unit.concept, satisfiers=tuple(scaled), combine=unit.combine
    )


@dataclass(frozen=True, slots=True)
class _Sized:
    unit: object
    rows: int
    cost: float


def plan(spec: QuerySpec, ctx: EvalContext) -> Plan:
    """Order a spec into a plan by estimated selectivity."""
    sized = sorted(
        (
            _Sized(unit=unit, rows=(guess := estimate_unit(unit, ctx)).rows, cost=guess.cost)
            for unit in spec.units
        ),
        key=lambda item: item.rows,
    )
    total = max(ctx.symbols.count(), 1)
    why: list[str] = []
    steps: list[Step] = []

    useful, dropped = _partition(sized, total)
    for item in dropped:
        why.append(
            f"dropping unit {item.unit.name!r}: an estimated {item.rows} matches "
            f"({100 * item.rows / total:.0f}%), effectively the whole table"
        )
    if not useful:
        # All being broad is no reason to do nothing: keep the narrowest so
        # there is at least a result
        useful, dropped = sized[:1], sized[1:]
        why.append("every unit is broad; keeping the narrowest to avoid an empty plan")

    for position, item in enumerate(useful):
        factor = _specificity(item.rows, total)
        steps.append(EvalUnit(_reweighted(item.unit, factor), seed=position == 0))
        why.append(
            f"{'start with' if position == 0 else 'union in'} {item.unit.name!r} "
            f"(est. {item.rows} rows, {100 * item.rows / total:.0f}%, weight x{factor:.2f})"
        )
    why.append(
        "units union rather than intersect -- they land on different elements (design chapter 01)"
    )

    steps, why = _add_graph(spec, {item.unit.name: item.rows for item in useful}, steps, why)

    steps.append(Cohere())
    why.append(
        "structural coherence: candidates near the strongest hits are weighted up. "
        "This needs no relation stated in the query -- what is near an answer is "
        "more likely an answer, on every query"
    )

    if spec.kinds or spec.concept:
        limit = INTENT_INPUT_CAP if spec.concept else spec.limit
        steps.append(Narrow(kind=spec.kinds or None, limit=limit, by=useful[0].unit.name))
        if spec.concept:
            why.append(
                f"cap at {INTENT_INPUT_CAP} before judging: intent costs thousands of lookups"
            )
    else:
        steps.append(Narrow(limit=spec.limit or 100, by=useful[0].unit.name))

    if spec.concept:
        steps.append(Intent(spec.concept, max_items=INTENT_INPUT_CAP))
        why.append(
            "intent goes last: it is the only operator calling an LLM, and every "
            "step before it saves money"
        )

    return Plan(steps=tuple(steps), reasoning=tuple(why))


def _partition(sized: list[_Sized], total: int) -> tuple[list[_Sized], list[_Sized]]:
    useful = [item for item in sized if item.rows <= total * USELESS_RATIO and item.rows > 0]
    dropped = [item for item in sized if item not in useful]
    return useful, dropped


def _add_graph(
    spec: QuerySpec, rows: dict[str, int], steps: list[Step], why: list[str]
) -> tuple[list[Step], list[str]]:
    """Insert graph constraints and decide which side to start from.

    A graph constraint **weights** rather than filters -- answers matching
    only one side must not be killed. The start is always the smaller side:
    `hop` costs scale linearly with the number of seeds.
    """
    for constraint in spec.graph:
        if constraint.src not in rows or constraint.dst not in rows:
            why.append(
                f"skipping constraint {constraint.src}->{constraint.dst}: one side's "
                "unit was not kept"
            )
            continue
        src, dst = constraint.src, constraint.dst
        if rows[dst] * ASYMMETRY < rows[src]:
            src, dst = dst, src
            why.append(
                f"constraint reversed: starting from {src!r} ({rows[src]} rows) rather "
                f"than {dst!r} ({rows[dst]} rows) -- hop cost scales with seed count"
            )
        steps.append(Boost(src, dst, edge=constraint.edge, hops=constraint.hops))
    return steps, why
