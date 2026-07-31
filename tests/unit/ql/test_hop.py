"""Unit tests for `hop` and `reach`.

The base terrain is a chain 1->2->3->4 plus a branch 1->5; cases needing a
more complex topology build their own.
"""

from __future__ import annotations

import logging

import pytest

from codesense.ql import Edge, Element, Evidence, Frag, UnitHit
from codesense.ql.context import EvalContext
from codesense.ql.operators import hop, reach
from codesense.ql.store import (
    InMemoryEdgeStore,
    InMemoryExpansionTable,
    InMemoryPostingIndex,
    InMemorySymbolStore,
)

CHAIN = (Edge(1, 2, "calls"), Edge(2, 3, "calls"), Edge(3, 4, "calls"), Edge(1, 5, "calls"))


def make_element(symbol_id: int) -> Element:
    return Element(
        symbol_id=symbol_id, name=f"s{symbol_id}", kind="method", file="A.java", span=(1, 2)
    )


def make_context(
    edges: tuple[Edge, ...] = CHAIN, *, symbol_ids: range | tuple[int, ...] = range(1, 8)
) -> EvalContext:
    return EvalContext(
        symbols=InMemorySymbolStore([make_element(i) for i in symbol_ids]),
        postings=InMemoryPostingIndex({}, total_symbols=100),
        expansion=InMemoryExpansionTable({}),
        edges=InMemoryEdgeStore(edges),
    )


def frag(*symbol_ids: int, evidence: dict[int, Evidence] | None = None) -> Frag:
    return Frag(
        nodes={i: make_element(i) for i in symbol_ids},
        evidence=evidence or {},
    )


class TestHopBasics:
    def test_finds_a_direct_call(self) -> None:
        result = hop(frag(1), frag(2), make_context())
        assert [p.nodes for p in result.witnesses] == [(1, 2)]

    def test_finds_an_indirect_call(self) -> None:
        result = hop(frag(1), frag(3), make_context())
        assert result.witnesses[0].nodes == (1, 2, 3)

    def test_returns_every_node_and_edge_on_the_path(self) -> None:
        result = hop(frag(1), frag(3), make_context())
        assert set(result.nodes) == {1, 2, 3}
        assert set(result.edges) == {(1, 2, "calls"), (2, 3, "calls")}

    def test_returns_an_empty_fragment_when_unreachable(self) -> None:
        assert not hop(frag(5), frag(4), make_context())

    def test_an_empty_src_or_dst_returns_empty_immediately(self) -> None:
        ctx = make_context()
        assert not hop(Frag(), frag(3), ctx)
        assert not hop(frag(1), Frag(), ctx)

    def test_a_paths_edge_count_equals_the_hop_count(self) -> None:
        result = hop(frag(1), frag(4), make_context())
        assert len(result.witnesses[0]) == 3


class TestHopRange:
    def test_hops_is_a_closed_interval_not_a_ceiling(self) -> None:
        """`hops=(2,2)` means exactly two hops -- "called indirectly but not
        directly" is a real intent."""
        ctx = make_context()
        assert hop(frag(1), frag(2), ctx, hops=(2, 2)).witnesses == ()
        assert hop(frag(1), frag(3), ctx, hops=(2, 2)).witnesses != ()

    def test_an_integer_means_exactly_that_many_hops(self) -> None:
        ctx = make_context()
        assert not hop(frag(1), frag(3), ctx, hops=1)
        assert hop(frag(1), frag(3), ctx, hops=2)

    def test_beyond_the_upper_bound_is_unreachable(self) -> None:
        assert not hop(frag(1), frag(4), make_context(), hops=(1, 2))

    def test_an_invalid_interval_raises(self) -> None:
        with pytest.raises(ValueError, match="non-negative closed interval"):
            hop(frag(1), frag(2), make_context(), hops=(3, 1))

    def test_a_negative_value_raises(self) -> None:
        with pytest.raises(ValueError, match="non-negative closed interval"):
            hop(frag(1), frag(2), make_context(), hops=-1)


class TestHopDirection:
    def test_backwards(self) -> None:
        result = hop(frag(3), frag(1), make_context(), direction="backward")
        assert result.witnesses[0].nodes == (3, 2, 1)

    def test_not_found_forwards_but_found_backwards(self) -> None:
        ctx = make_context()
        assert not hop(frag(3), frag(1), ctx)
        assert hop(frag(3), frag(1), ctx, direction="backward")

    def test_any_is_undirected(self) -> None:
        """ "Are these two related at all" does not care about direction."""
        ctx = make_context()
        assert hop(frag(5), frag(2), ctx, direction="any", hops=(1, 3))

    def test_an_invalid_direction_raises(self) -> None:
        with pytest.raises(ValueError, match="direction"):
            hop(frag(1), frag(2), make_context(), direction="sideways")


class TestHopConstraints:
    def test_avoid_excludes_an_intermediate_node(self) -> None:
        assert not hop(frag(1), frag(3), make_context(), avoid=frag(2))

    def test_avoid_excludes_a_start_node(self) -> None:
        assert not hop(frag(1), frag(3), make_context(), avoid=frag(1))

    def test_via_requires_the_path_to_pass_through(self) -> None:
        ctx = make_context()
        assert hop(frag(1), frag(4), ctx, via=frag(2))
        assert not hop(frag(1), frag(4), ctx, via=frag(5))

    def test_filters_by_edge_kind(self) -> None:
        ctx = make_context((Edge(1, 2, "calls"), Edge(2, 3, "contains")))
        assert not hop(frag(1), frag(3), ctx, edge="calls")
        assert hop(frag(1), frag(3), ctx, edge=["calls", "contains"])

    def test_filters_by_confidence_blocking_virtual_dispatch(self) -> None:
        ctx = make_context((Edge(1, 2, "calls", confidence=0.6),))
        assert hop(frag(1), frag(2), ctx, min_confidence=0.5)
        assert not hop(frag(1), frag(2), ctx, min_confidence=0.9)


class TestHopBrakes:
    def _wide(self) -> tuple[Edge, ...]:
        """1 reaches 999 through any of 20 intermediates: 20 paths."""
        return tuple(
            e for i in range(100, 120) for e in (Edge(1, i, "calls"), Edge(i, 999, "calls"))
        )

    def test_max_paths_truncates(self) -> None:
        ctx = make_context(self._wide(), symbol_ids=(1, 999, *range(100, 120)))
        assert len(hop(frag(1), frag(999), ctx, max_paths=5).witnesses) == 5

    def test_truncation_must_log_rather_than_stay_silent(self) -> None:
        """Silent truncation reads as "that is all there was"."""
        ctx = make_context(self._wide(), symbol_ids=(1, 999, *range(100, 120)))
        logger = logging.getLogger("codesense.ql.operators.hop")
        records: list[logging.LogRecord] = []
        handler = logging.Handler()
        handler.emit = records.append  # type: ignore[method-assign]
        logger.addHandler(handler)
        try:
            hop(frag(1), frag(999), ctx, max_paths=5)
        finally:
            logger.removeHandler(handler)
        assert any("max_paths" in r.getMessage() for r in records)

    def test_no_log_when_nothing_is_truncated(self) -> None:
        ctx = make_context(self._wide(), symbol_ids=(1, 999, *range(100, 120)))
        logger = logging.getLogger("codesense.ql.operators.hop")
        records: list[logging.LogRecord] = []
        handler = logging.Handler()
        handler.emit = records.append  # type: ignore[method-assign]
        logger.addHandler(handler)
        try:
            hop(frag(1), frag(999), ctx, max_paths=1000)
        finally:
            logger.removeHandler(handler)
        assert not records

    def test_a_hub_node_is_not_expanded_further(self) -> None:
        """Utility methods are called by everyone, and paths through them
        carry almost no information."""
        hub = tuple(Edge(i, 500, "calls") for i in range(1, 30)) + (Edge(500, 999, "calls"),)
        ctx = make_context(hub, symbol_ids=(*range(1, 30), 500, 999))
        assert not hop(frag(1), frag(999), ctx, max_degree=10)
        assert hop(frag(1), frag(999), ctx, max_degree=100)

    def test_the_hub_cap_does_not_affect_the_start_nodes(self) -> None:
        hub = tuple(Edge(1, i, "calls") for i in range(100, 130)) + (Edge(100, 999, "calls"),)
        ctx = make_context(hub, symbol_ids=(1, 999, *range(100, 130)))
        assert hop(frag(1), frag(999), ctx, max_degree=5)

    def test_a_path_does_not_repeat_a_node(self) -> None:
        ring = (Edge(1, 2, "calls"), Edge(2, 3, "calls"), Edge(3, 1, "calls"))
        ctx = make_context(ring, symbol_ids=(1, 2, 3))
        for path in hop(frag(1), frag(3), ctx, hops=(1, 6)).witnesses:
            assert len(set(path.nodes)) == len(path.nodes)


class TestHopEvidence:
    def test_evidence_from_both_ends_reaches_the_result(self) -> None:
        src = frag(1, evidence={1: Evidence((UnitHit("perf", "lexical", "async"),))})
        dst = frag(3, evidence={3: Evidence((UnitHit("disk", "lexical", "swap"),))})
        result = hop(src, dst, make_context())
        assert result.evidence_for(1).unit_hits[0].unit == "perf"
        assert result.evidence_for(3).unit_hits[0].unit == "disk"

    def test_an_intermediate_node_has_no_unit_evidence(self) -> None:
        """It is in the result because of structure, not because it matched
        any word."""
        src = frag(1, evidence={1: Evidence((UnitHit("perf", "lexical", "async"),))})
        result = hop(src, frag(3), make_context())
        assert result.evidence_for(2).unit_hits == ()

    def test_a_node_that_is_both_start_and_end_merges_both_sides(self) -> None:
        ctx = make_context((Edge(1, 2, "calls"), Edge(2, 1, "calls")), symbol_ids=(1, 2))
        both = frag(1, 2, evidence={1: Evidence((UnitHit("a", "lexical", "x"),))})
        other = frag(1, 2, evidence={1: Evidence((UnitHit("b", "lexical", "y"),))})
        units = {h.unit for h in hop(both, other, ctx).evidence_for(1).unit_hits}
        assert {"a", "b"} <= units

    def test_a_node_absent_from_the_symbol_store_stays_out(self) -> None:
        ctx = make_context(symbol_ids=(1, 2))
        assert not hop(frag(1), frag(3), ctx)


class TestReach:
    def test_what_is_reachable_from_here(self) -> None:
        assert set(reach(frag(1), make_context(), hops=(1, 2)).nodes) == {2, 3, 5}

    def test_the_start_node_is_excluded_by_default(self) -> None:
        assert 1 not in reach(frag(1), make_context()).nodes

    def test_a_lower_bound_of_0_includes_the_start_node(self) -> None:
        assert 1 in reach(frag(1), make_context(), hops=(0, 1)).nodes

    def test_returns_nodes_only_not_paths(self) -> None:
        assert reach(frag(1), make_context()).witnesses == ()

    def test_reachable_backwards(self) -> None:
        assert set(reach(frag(4), make_context(), direction="backward", hops=(1, 3)).nodes) == {
            1,
            2,
            3,
        }

    def test_an_invalid_direction_raises(self) -> None:
        with pytest.raises(ValueError, match="direction"):
            reach(frag(1), make_context(), direction="sideways")
