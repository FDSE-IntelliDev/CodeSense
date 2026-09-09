"""Execution plans: ordered steps that can be run, printed and explained.

The plan is itself an artifact. Before running, you can see **why** the
planner ordered things this way, with each step's predicted size and cost
beside it; after running, you can see how far prediction and reality
diverged -- and a wild divergence means the estimator needs fixing.

Design: ``docs/design/06-script-and-execution.md``.
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from codesense.ql.compile.cost import Estimate, estimate_hop, estimate_intent, estimate_unit
from codesense.ql.compile.relation_endpoints import (
    is_legacy_relation,
    relation_destinations,
)
from codesense.ql.context import EvalContext
from codesense.ql.frag import Frag
from codesense.ql.operators import eval_unit, intent, project, reach, score_of, top
from codesense.ql.unit import QueryUnit

__all__ = [
    "Boost",
    "Cohere",
    "EvalUnit",
    "Intent",
    "Narrow",
    "Plan",
    "ProjectTarget",
    "State",
    "Step",
    "Trace",
]

#: Multiplicative boost for candidates in a constraint's neighbourhood. The
#: graph is evidence independent of lexical matching, so it boosts rather
#: than replaces -- it must not lift things with no lexical basis at all.
BOOST = 0.6


#: Boost for matching the preferred element kind. Weaker than the graph
#: boost: the kind is the model's guess, the graph is a fact in the index.
KIND_PREFERENCE = 0.3


def _top_with_boost(
    frag: Frag, limit: int, boosted: set[int], preferred: set[int] = frozenset()
) -> Frag:
    """Take the top n by lexical score times graph boost times kind preference."""
    if not boosted and not preferred:
        return top(frag, limit)
    ordered = sorted(
        frag.nodes,
        key=lambda sid: (
            -score_of(frag, sid)
            * (1 + BOOST * (sid in boosted))
            * (1 + KIND_PREFERENCE * (sid in preferred)),
            sid,
        ),
    )
    return frag.induced(ordered[:limit])


_log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Trace:
    """What actually happened in one step."""

    label: str
    estimated: int
    actual: int
    seconds: float
    skipped: str = ""

    @property
    def drift(self) -> float:
        """Ratio of actual to predicted, for checking the estimator."""
        return self.actual / max(self.estimated, 1)


@dataclass
class State:
    """The working set during execution.

    ``projected`` lets one `estimate` implementation serve both a real run
    and a **static dry run**: in a dry run there is no real Frag, only
    predicted row counts threaded through.
    """

    units: dict[str, Frag] = field(default_factory=dict)
    current: Frag = field(default_factory=Frag)
    trace: list[Trace] = field(default_factory=list)
    projected: int | None = None
    projected_units: dict[str, int] = field(default_factory=dict)

    #: Symbols boosted by a graph constraint. Affects ranking, not membership.
    boosted: set[int] = field(default_factory=set)

    @property
    def rows(self) -> int:
        return len(self.current) if self.projected is None else self.projected

    def unit_rows(self, name: str) -> int:
        if self.projected is None:
            return len(self.units.get(name, Frag()))
        return self.projected_units.get(name, 0)

    @property
    def stopped(self) -> bool:
        """The working set is empty; later steps are pointless."""
        return bool(self.units) and not self.current


class Step(ABC):
    """One step of a plan."""

    label: str

    @abstractmethod
    def estimate(self, ctx: EvalContext, state: State) -> Estimate:
        """Estimate this step's output and cost without running it."""

    @abstractmethod
    def apply(self, ctx: EvalContext, state: State) -> None:
        """Update the working set in place."""


@dataclass(slots=True)
class EvalUnit(Step):
    """Evaluate a unit and union it into the working set.

    ``seed`` marks the first one. Symbols matching several units score higher
    as evidence accumulates, but matching only one is not eliminating --
    **elimination is `Cohere`'s and `Narrow`'s job**.
    """

    unit: QueryUnit
    seed: bool = False
    label: str = ""

    def __post_init__(self) -> None:
        self.label = f"unit({self.unit.name}){'  <- seed' if self.seed else ''}"

    def estimate(self, ctx: EvalContext, state: State) -> Estimate:
        """This step outputs the **union**, not the unit alone."""
        guess = estimate_unit(self.unit, ctx)
        if self.seed:
            return guess
        # Union under independence: |A or B| = N*(1 - (1-|A|/N)(1-|B|/N))
        total = max(ctx.population, 1)
        merged = total * (1 - (1 - state.rows / total) * (1 - guess.rows / total))
        return Estimate(rows=round(merged), cost=guess.cost, detail=guess.detail)

    def apply(self, ctx: EvalContext, state: State) -> None:
        found = eval_unit(self.unit, ctx)
        state.units[self.unit.name] = found
        # **Union, not intersection.** Units land on different elements --
        # "elements containing both performance and disk keywords barely
        # exist" (design chapter 01). Intersection kills the answers: on
        # netty's zero-copy query every target sat in one unit and nothing
        # survived. Relations between units are expressed by graph
        # constraints, not by set operations.
        state.current = found if self.seed else (state.current | found)


@dataclass(slots=True)
class Filter(Step):
    """Narrow the working set using an already-evaluated unit."""

    unit_name: str
    label: str = ""

    def __post_init__(self) -> None:
        self.label = f"& {self.unit_name}"

    def estimate(self, ctx: EvalContext, state: State) -> Estimate:
        other = state.unit_rows(self.unit_name)
        return Estimate(rows=min(state.rows, other), cost=float(state.rows))

    def apply(self, ctx: EvalContext, state: State) -> None:
        state.current = state.current & state.units.get(self.unit_name, Frag())


@dataclass(slots=True)
class Boost(Step):
    """A graph constraint: **weights** structurally connected candidates
    rather than filtering the rest away.

    "Performance code calls disk code" states a relation between two units,
    not a filter on the candidate set. Using it as a hard filter kills every
    answer that matches only one side -- which is the common case.

    Pure ``calls``/``contains`` constraints are bidirectional and may start
    from the smaller side because reach cost scales with the seed count.
    Typed constraints retain their validated ``src -> dst`` endpoint roles.
    """

    src_name: str
    dst_name: str
    edge: tuple[str, ...] = ("calls", "contains")
    hops: tuple[int, int] = (1, 2)
    weight: float = 0.6
    label: str = ""

    def __post_init__(self) -> None:
        relation = "<->" if is_legacy_relation(self.edge) else "->"
        self.label = (
            f"boost({self.src_name} {relation} {self.dst_name}, {self.hops[0]}-{self.hops[1]} hops)"
        )

    def estimate(self, ctx: EvalContext, state: State) -> Estimate:
        touched = estimate_hop(
            state.unit_rows(self.src_name),
            ctx,
            hops=self.hops,
            dst_rows=state.unit_rows(self.dst_name),
        )
        # Weighting changes ranking, not the number of candidates
        return Estimate(rows=state.rows, cost=touched.cost, detail=touched.detail)

    def apply(self, ctx: EvalContext, state: State) -> None:
        src = state.units.get(self.src_name, Frag())
        if not src or not state.current:
            return
        dst = state.units.get(self.dst_name, Frag())
        state.boosted |= relation_destinations(
            src,
            dst,
            ctx,
            edge=self.edge,
            hops=self.hops,
        ) & set(state.current.nodes)


@dataclass(slots=True)
class Cohere(Step):
    """Structural coherence: weight up candidates near the strongest hits.

    Not the same thing as `Boost`, and the difference matters:

        Boost    a relation stated by the query ("A-related code calls B") --
                 proposed by the model, validated by statistics, and **most
                 queries do not state one at all**
        Cohere   candidates structurally near the strongest hits are more
                 likely relevant -- **true of every query**, needing no model

    Measured, this contributes low double digits of R@100, purely from facts
    already in the index.
    """

    seeds: int = 20
    edge: tuple[str, ...] = ("calls", "contains")
    hops: tuple[int, int] = (1, 2)
    label: str = ""

    def __post_init__(self) -> None:
        self.label = f"cohere(top {self.seeds} as seeds, {self.hops[0]}-{self.hops[1]} hops)"

    def estimate(self, ctx: EvalContext, state: State) -> Estimate:
        touched = estimate_hop(min(state.rows, self.seeds), ctx, hops=self.hops)
        return Estimate(rows=state.rows, cost=touched.cost, detail="reranks only")

    def apply(self, ctx: EvalContext, state: State) -> None:
        if not state.current:
            return
        ordered = sorted(state.current.nodes, key=lambda sid: (-score_of(state.current, sid), sid))
        seeds = state.current.induced(ordered[: self.seeds])
        near = reach(seeds, ctx, edge=list(self.edge), direction="any", hops=self.hops)
        state.boosted |= set(near.nodes) & set(state.current.nodes)


@dataclass(slots=True)
class Narrow(Step):
    """Narrow to what the most expensive step can afford.

    ``kind`` is a **preference, not a filter**. The model's kinds are
    unreliable: asked about validation constraints on entity fields, it
    omitted `field` entirely, and a hard filter would have deleted every
    answer. Same principle as graph constraints -- unreliable signals weight,
    only reliable ones filter.
    """

    kind: tuple[str, ...] | None = None
    limit: int | None = None
    by: str | None = None
    label: str = ""

    def __post_init__(self) -> None:
        parts = []
        if self.kind:
            parts.append(f"prefer {'/'.join(self.kind[:3])}{'...' if len(self.kind) > 3 else ''}")
        if self.limit:
            parts.append(f"top {self.limit}")
        self.label = "narrow(" + ", ".join(parts) + ")"

    def estimate(self, ctx: EvalContext, state: State) -> Estimate:
        rows = min(state.rows, self.limit) if self.limit else state.rows
        return Estimate(rows=rows, cost=float(state.rows))

    def apply(self, ctx: EvalContext, state: State) -> None:
        found = state.current
        preferred = (
            {sid for sid, e in found.nodes.items() if e.kind in self.kind} if self.kind else set()
        )
        if self.limit:
            found = _top_with_boost(found, self.limit, state.boosted, preferred)
        state.current = found


@dataclass(slots=True)
class ProjectTarget(Step):
    """Project candidates to the element kind promised by the result contract."""

    target: tuple[str, ...]
    label: str = ""

    def __post_init__(self) -> None:
        self.label = f"target({'/'.join(self.target)})"

    def estimate(self, ctx: EvalContext, state: State) -> Estimate:
        return Estimate(rows=state.rows, cost=float(state.rows), detail="one graph hop")

    def apply(self, ctx: EvalContext, state: State) -> None:
        state.current = project(
            state.current,
            ctx,
            edge="in_file",
            kind=self.target,
            include_self=True,
        )


@dataclass(slots=True)
class Intent(Step):
    """Semantic judging. **Always last**, and the planner caps its input with
    `Narrow` first."""

    concept: str
    threshold: float = 0.5
    max_items: int | None = 60
    label: str = ""

    def __post_init__(self) -> None:
        self.label = (
            f'intent("{self.concept[:28]}…")'
            if len(self.concept) > 28
            else f'intent("{self.concept}")'
        )

    def estimate(self, ctx: EvalContext, state: State) -> Estimate:
        return estimate_intent(state.rows)

    def apply(self, ctx: EvalContext, state: State) -> None:
        state.current = intent(
            state.current,
            self.concept,
            ctx,
            threshold=self.threshold,
            max_items=self.max_items,
        )


@dataclass(frozen=True, slots=True)
class Plan:
    """Ordered steps, plus why the planner ordered them that way."""

    steps: tuple[Step, ...]
    reasoning: tuple[str, ...] = ()

    def run(
        self,
        ctx: EvalContext,
        *,
        skip: tuple[type[Step], ...] = (),
        after_step: Callable[[Step], None] | None = None,
    ) -> State:
        """Execute. ``skip`` omits expensive steps on a dry run, usually
        `Intent`.

        Stops once the working set empties: later steps cannot change the
        result and might waste an LLM call.
        """
        state = State()
        for step in self.steps:
            if isinstance(step, skip):
                state.trace.append(
                    Trace(step.label, 0, len(state.current), 0.0, skipped="skipped (dry run)")
                )
                continue
            if state.stopped:
                state.trace.append(Trace(step.label, 0, 0, 0.0, skipped="working set empty"))
                continue
            predicted = step.estimate(ctx, state).rows
            started = time.perf_counter()
            step.apply(ctx, state)
            state.trace.append(
                Trace(step.label, predicted, len(state.current), time.perf_counter() - started)
            )
            if after_step is not None:
                after_step(step)
        return state

    def explain(self, ctx: EvalContext) -> str:
        """Print the plan and its estimates before running."""
        lines = ["execution plan:"]
        lines += [f"  · {why}" for why in self.reasoning]
        lines.append("")
        state = State(projected=0)
        for index, step in enumerate(self.steps, 1):
            guess = step.estimate(ctx, state)
            lines.append(f"  {index}. {step.label:<44}{guess}")
            # Thread predictions through so later steps estimate from the
            # predicted sizes rather than nothing
            if isinstance(step, EvalUnit) and not step.seed:
                # Record the unit's own size separately: choosing a graph
                # direction looks at that, not at the union
                state.projected_units[step.unit.name] = estimate_unit(step.unit, ctx).rows
            elif isinstance(step, EvalUnit):
                state.projected_units[step.unit.name] = guess.rows
            state.projected = guess.rows
        return "\n".join(lines)

    @staticmethod
    def report(trace: Sequence[Trace]) -> str:
        """Compare prediction against reality after a run. A wild divergence
        means the estimator needs fixing."""
        lines = [f"  {'step':<44}{'est':>8}{'actual':>8}{'time':>9}"]
        for item in trace:
            if item.skipped:
                lines.append(f"  {item.label:<44}{item.skipped:>16}")
                continue
            spent = f"{item.seconds * 1000:.0f}ms"
            lines.append(f"  {item.label:<44}{item.estimated:>8}{item.actual:>8}{spent:>9}")
        return "\n".join(lines)
