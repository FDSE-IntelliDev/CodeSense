"""Tests for exact, evidence-preserving one-hop projection."""

from __future__ import annotations

import pytest

from codesense.ql import Edge, Element, Evidence, Frag, UnitHit
from codesense.ql.context import EvalContext
from codesense.ql.operators import project, score_of
from codesense.ql.store import (
    InMemoryEdgeStore,
    InMemoryExpansionTable,
    InMemoryPostingIndex,
    InMemorySymbolStore,
)


def scored(unit: str, score: float) -> Evidence:
    return Evidence((UnitHit(unit, Evidence.COMBINED, "match", score=score),))


def make_element(symbol_id: int, name: str, kind: str, file: str) -> Element:
    return Element(symbol_id=symbol_id, name=name, kind=kind, file=file, span=(1, 2))


@pytest.fixture
def ctx() -> EvalContext:
    elements = (
        make_element(1, "create", "method", "Controller.java"),
        make_element(2, "update", "method", "Controller.java"),
        make_element(3, "other", "method", "Other.java"),
        make_element(10, "Controller.java", "file", "Controller.java"),
        make_element(11, "Other.java", "file", "Other.java"),
        make_element(20, "PageRequest", "class", "PageRequest.java"),
    )
    edges = (
        Edge(1, 10, "in_file", site=(10, 11), provenance="parser", confidence=1.0),
        Edge(2, 10, "in_file", site=(20, 21), provenance="parser", confidence=0.9),
        Edge(3, 11, "in_file", site=(30, 31), provenance="parser", confidence=0.4),
        Edge(1, 20, "references", provenance="resolver", confidence=0.8),
        Edge(2, 20, "calls", provenance="resolver", confidence=0.8),
    )
    return EvalContext(
        symbols=InMemorySymbolStore(elements),
        postings=InMemoryPostingIndex({}, total_symbols=len(elements)),
        expansion=InMemoryExpansionTable({}),
        edges=InMemoryEdgeStore(edges),
    )


def test_projects_methods_to_their_file_and_keeps_scores(ctx: EvalContext) -> None:
    source = Frag(nodes=ctx.symbols.get_many((1,)), evidence={1: scored("query", 0.8)})

    result = project(source, ctx, edge="in_file", kind="file")

    assert [element.name for element in result] == ["Controller.java"]
    assert score_of(result, 10) == pytest.approx(0.8)
    assert any(hit.signal == "graph" for hit in result.evidence_for(10).unit_hits)


def test_same_unit_uses_strongest_contained_match_not_file_size(ctx: EvalContext) -> None:
    source = Frag(
        nodes=ctx.symbols.get_many((1, 2)),
        evidence={1: scored("query", 0.8), 2: scored("query", 0.4)},
    )

    result = project(source, ctx, edge="in_file", kind="file")

    assert score_of(result, 10) == pytest.approx(0.8)


def test_backward_projection_filters_by_kind_and_confidence(ctx: EvalContext) -> None:
    page_request = Frag(nodes=ctx.symbols.get_many((20,)))

    result = project(
        page_request,
        ctx,
        edge="references",
        direction="backward",
        kind="method",
        min_confidence=0.8,
    )

    assert set(result.nodes) == {1}


def test_projection_accepts_several_edge_kinds(ctx: EvalContext) -> None:
    page_request = Frag(nodes=ctx.symbols.get_many((20,)))

    result = project(page_request, ctx, edge=["references", "calls"], direction="backward")

    assert set(result.nodes) == {1, 2}


def test_include_self_is_opt_in_and_respects_kind(ctx: EvalContext) -> None:
    file_frag = Frag(nodes=ctx.symbols.get_many((10,)))

    assert 10 not in project(file_frag, ctx, kind="file").nodes
    assert 10 in project(file_frag, ctx, kind="file", include_self=True).nodes


def test_empty_input_projects_to_empty_fragment(ctx: EvalContext) -> None:
    assert not project(Frag(), ctx)


def test_invalid_direction_is_rejected(ctx: EvalContext) -> None:
    with pytest.raises(ValueError, match="direction must be 'forward' or 'backward'"):
        project(Frag(), ctx, direction="sideways")
