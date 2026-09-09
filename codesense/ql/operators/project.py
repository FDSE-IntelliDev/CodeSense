"""Exact one-hop graph projection with an append-only evidence trail."""

from __future__ import annotations

from collections.abc import Sequence

from codesense.ql.context import EvalContext
from codesense.ql.frag import Element, Evidence, Frag, UnitHit

__all__ = ["project"]


def project(
    src: Frag,
    ctx: EvalContext,
    *,
    edge: str | Sequence[str] = "in_file",
    direction: str = "forward",
    kind: str | Sequence[str] | None = None,
    include_self: bool = False,
    min_confidence: float = 0.0,
) -> Frag:
    """Project source nodes across exactly one indexed graph edge.

    The returned fragment is node-only. Source evidence is carried to each
    projected node, while a zero-score graph hit records the edge witness.
    """
    if direction not in {"forward", "backward"}:
        raise ValueError("direction must be 'forward' or 'backward'")
    kinds = (edge,) if isinstance(edge, str) else tuple(edge)
    wanted = None if kind is None else frozenset((kind,) if isinstance(kind, str) else kind)
    nodes: dict[int, Element] = {}
    evidence: dict[int, Evidence] = {}

    for source_id in sorted(src.nodes):
        if include_self and (wanted is None or src.nodes[source_id].kind in wanted):
            nodes[source_id] = src.nodes[source_id]
            evidence[source_id] = evidence.get(source_id, Evidence()).merge(
                src.evidence_for(source_id)
            )
        selected = (
            ctx.edges.out_edges(source_id, kinds=kinds, min_confidence=min_confidence)
            if direction == "forward"
            else ctx.edges.in_edges(source_id, kinds=kinds, min_confidence=min_confidence)
        )
        for relation in selected:
            target_id = relation.target_id if direction == "forward" else relation.source_id
            target = ctx.symbols.get(target_id)
            if target is None or (wanted is not None and target.kind not in wanted):
                continue
            graph_hit = UnitHit(
                unit="projection",
                signal="graph",
                detail=f"{direction} {relation.kind} via {relation.provenance}",
                field=relation.kind,
                score=0.0,
                span=relation.site,
            )
            carried = src.evidence_for(source_id).merge(Evidence(unit_hits=(graph_hit,)))
            nodes[target_id] = target
            evidence[target_id] = evidence.get(target_id, Evidence()).merge(carried)
    return Frag(nodes=nodes, evidence=evidence)
