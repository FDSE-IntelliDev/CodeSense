"""Checking the model's structural proposals against **this codebase**.

The division of labour has three stages rather than two options:

    the model proposes   whether the query means "A-related code calls
                         B-related code"
    statistics validate  whether that relation holds in this codebase
    statistics tune      which side to start from, how many hops, what cost

Both extremes were wrong: letting the model decide everything scored 21%
R@100, and keeping it away from structure scored 47% while never using the
graph at all. **Proposing is semantic; validating is statistical.**

Measured discrimination on netty (42221 symbols, 120k edges):

    real         pool-chunk 4.75x, handler-pipeline 2.20x,
                 zerocopy-filechannel 1.32x
    fabricated   zerocopy-JSON 0.00x, pool-WebSocket 0.00x, DNS-compression 0.03x
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from codesense.ql.compile.partition import OVERLAP_FLOOR
from codesense.ql.context import EvalContext

__all__ = ["LIFT_FLOOR", "Relation", "relation_lift", "validate_groups", "validate_relations"]

#: How many times the random expectation the crossing edges must reach.
#:
#: Measured, real relations bottom out at 1.32x and fabricated ones top out
#: at 0.03x, so the margin is wide. 1.2 is deliberately lenient: a graph
#: constraint weights rather than filters, so accepting a wrong one costs
#: little while missing a real one costs more.
LIFT_FLOOR = 1.2

#: How many source nodes to sample when counting crossings. Counting them all
#: is too expensive on a large project, and the test only needs a magnitude.
SAMPLE_CAP = 400


@dataclass(frozen=True, slots=True)
class Relation:
    """A relation that passed validation."""

    src: str
    dst: str
    lift: float
    detail: str = ""


def relation_lift(
    src_terms: Sequence[str], dst_terms: Sequence[str], ctx: EvalContext
) -> tuple[float, str]:
    """How many times denser the edges between two term groups are than chance.

    The baseline is ``|A|*|B|*2E/N^2``, treating the graph as a random one
    with the same edge count. It is a crude baseline, but ample for telling
    4.75x from 0.
    """
    total = ctx.symbols.count()
    if total < 2:
        return 0.0, "too few symbols"
    left = {p.symbol_id for term in src_terms for p in ctx.postings.lookup(term)}
    right = {p.symbol_id for term in dst_terms for p in ctx.postings.lookup(term)}
    if not left or not right:
        return 0.0, "one side matched nothing"

    sampled = sorted(left)[:SAMPLE_CAP]
    scale = len(left) / len(sampled)
    crossing = scale * sum(
        1 for node in sampled for edge in ctx.edges.out_edges(node) if edge.target_id in right
    )
    edges = sum(ctx.edges.degree(node) for node in sampled) * scale
    expected = len(left) * len(right) * edges / (total * total) if total else 0.0
    if expected <= 0:
        return 0.0, "the graph has no edges"
    lift = crossing / expected
    return lift, f"{crossing:.0f} crossings vs {expected:.0f} expected ({lift:.2f}x)"


def validate_relations(
    proposed: Sequence[tuple[str, str]],
    groups: dict[str, Sequence[str]],
    ctx: EvalContext,
    *,
    floor: float = LIFT_FLOOR,
) -> tuple[list[Relation], list[str]]:
    """Keep the relations that actually hold in this codebase.

    Returns (accepted, reasons for rejection). The reasons matter: a user
    needs to know why a proposed relation was not used.
    """
    kept: list[Relation] = []
    rejected: list[str] = []
    for src, dst in proposed:
        if src not in groups or dst not in groups or src == dst:
            rejected.append(f"{src}->{dst}: names a group that does not exist")
            continue
        lift, detail = relation_lift(groups[src], groups[dst], ctx)
        if lift >= floor:
            kept.append(Relation(src=src, dst=dst, lift=lift, detail=detail))
        else:
            rejected.append(
                f"{src}->{dst}: {detail}, below {floor}x -- the relation does not "
                "hold in this project"
            )
    return kept, rejected


def validate_groups(
    proposed: dict[str, Sequence[str]], ctx: EvalContext, *, floor: float = OVERLAP_FLOOR
) -> tuple[dict[str, list[str]], list[str]]:
    """Check the model's groups: do the terms really land on the same symbols?

    The model groups **semantically** -- zero-copy, user-space memory,
    performance -- but semantic proximity does not imply the same landing
    sites. Grouping terms that land differently and adding them with equal
    weight dilutes the answer, which is what took R@100 from 65% to 21%.

    Groups without enough cohesion are folded back: **one broad unit beats
    several narrow ones that dilute the answer.**
    """
    usable = {
        name: [t for t in terms if ctx.postings.term_info(t) is not None]
        for name, terms in proposed.items()
    }
    usable = {name: terms for name, terms in usable.items() if terms}
    if len(usable) < 2:
        return {name: list(terms) for name, terms in usable.items()}, []

    kept: dict[str, list[str]] = {}
    notes: list[str] = []
    loose: list[str] = []
    for name, terms in usable.items():
        cohesion = _cohesion(terms, ctx)
        if len(terms) >= 2 and cohesion >= floor:
            kept[name] = terms
        else:
            loose.extend(terms)
            notes.append(
                f"group {name!r} has cohesion {cohesion:.3f}, too low; folded back -- "
                "its terms do not land on the same symbols"
            )

    if not kept:
        return {"q": sorted({t for terms in usable.values() for t in terms})}, [
            "no group had enough cohesion; merged into one unit"
        ]
    if loose:
        biggest = max(kept, key=lambda name: len(kept[name]))
        kept[biggest] = sorted({*kept[biggest], *loose})
    return kept, notes


def _cohesion(terms: Sequence[str], ctx: EvalContext) -> float:
    """Mean pairwise Jaccard within a group."""
    postings = [{p.symbol_id for p in ctx.postings.lookup(term)} for term in terms]
    pairs = [
        (postings[i], postings[j])
        for i in range(len(postings))
        for j in range(i + 1, len(postings))
    ]
    if not pairs:
        return 0.0
    return sum(len(left & right) / max(len(left | right), 1) for left, right in pairs) / len(pairs)
