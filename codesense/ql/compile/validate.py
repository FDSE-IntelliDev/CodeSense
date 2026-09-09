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
from codesense.ql.compile.relation_endpoints import (
    is_legacy_relation,
    relation_endpoint_strata,
)
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
    edge: tuple[str, ...] = ("calls", "contains")


def relation_lift(
    src_terms: Sequence[str],
    dst_terms: Sequence[str],
    ctx: EvalContext,
    *,
    edge: Sequence[str] = ("calls", "contains"),
) -> tuple[float, str]:
    """How many times denser the edges between two term groups are than chance.

    The declaration-only baseline remains ``|A|*|B|*2E/N^2``. File-endpoint
    kinds use the same heuristic with separate source and destination
    populations. It is crude, but ample for telling 4.75x from 0.
    """
    declarations = ctx.population
    kinds = tuple(dict.fromkeys(edge)) or ("calls", "contains")
    legacy = is_legacy_relation(kinds)
    if legacy and declarations < 2:
        return 0.0, "too few symbols"
    src_declarations = {p.symbol_id for term in src_terms for p in ctx.postings.lookup(term)}
    dst_declarations = {p.symbol_id for term in dst_terms for p in ctx.postings.lookup(term)}
    if not src_declarations or not dst_declarations:
        return 0.0, "one side matched nothing"

    # Keep the historical declaration-only calculation exactly as it was.
    if legacy:
        left = src_declarations
        right = dst_declarations
        sampled = sorted(left)[:SAMPLE_CAP]
        scale = len(left) / len(sampled)
        crossing = scale * sum(
            1
            for node in sampled
            for relation in ctx.edges.out_edges(node, kinds=kinds)
            if relation.target_id in right
        )
        edges = sum(ctx.edges.degree(node, kinds=kinds) for node in sampled) * scale
        expected = len(left) * len(right) * edges / (declarations * declarations)
        if expected <= 0:
            return 0.0, "the graph has no edges"
        lift = crossing / expected
        return lift, f"{crossing:.0f} crossings vs {expected:.0f} expected ({lift:.2f}x)"

    # Typed/mixed tuples keep every edge kind and physical endpoint role in
    # a separate sampling stratum. This prevents declaration IDs from using
    # the entire cap before later file IDs are ever inspected.
    crossing = 0.0
    expected = 0.0
    usable_endpoint_pair = False
    usable_population = False
    for stratum in relation_endpoint_strata(src_declarations, dst_declarations, ctx, kinds):
        if not stratum.source_ids or not stratum.destination_ids:
            continue
        usable_endpoint_pair = True
        if stratum.source_population < 1 or stratum.destination_population < 1:
            continue
        usable_population = True
        sampled = sorted(stratum.source_ids)[:SAMPLE_CAP]
        scale = len(stratum.source_ids) / len(sampled)
        stratum_crossing = scale * sum(
            1
            for node in sampled
            for relation in ctx.edges.out_edges(node, kinds=(stratum.kind,))
            if relation.target_id in stratum.destination_ids
        )
        stratum_edges = (
            sum(ctx.edges.degree(node, kinds=(stratum.kind,)) for node in sampled) * scale
        )
        crossing += stratum_crossing
        expected += (
            len(stratum.source_ids)
            * len(stratum.destination_ids)
            * stratum_edges
            / (stratum.source_population * stratum.destination_population)
        )

    if not usable_endpoint_pair:
        return 0.0, "one side matched nothing"
    if not usable_population:
        return 0.0, "too few endpoint symbols"
    if expected <= 0:
        return 0.0, "the graph has no edges"
    lift = crossing / expected
    return lift, f"{crossing:.0f} crossings vs {expected:.0f} expected ({lift:.2f}x)"


def validate_relations(
    proposed: Sequence[tuple[str, str] | tuple[str, str, Sequence[str]]],
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
    for proposal in proposed:
        src, dst = proposal[:2]
        edge = tuple(proposal[2]) if len(proposal) >= 3 else ("calls", "contains")
        edge = edge or ("calls", "contains")
        if src not in groups or dst not in groups or src == dst:
            rejected.append(f"{src}->{dst}: names a group that does not exist")
            continue
        lift, detail = relation_lift(groups[src], groups[dst], ctx, edge=edge)
        if lift >= floor:
            kept.append(Relation(src=src, dst=dst, lift=lift, edge=edge, detail=detail))
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
