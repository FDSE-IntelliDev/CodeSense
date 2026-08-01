"""Unit tests for `only` / `top` / `degree`."""

from __future__ import annotations

import pytest

from codesense.ql import Edge, Element, Evidence, Frag, UnitHit
from codesense.ql.context import EvalContext
from codesense.ql.operators import degree, only, score_of, top
from codesense.ql.store import (
    InMemoryEdgeStore,
    InMemoryExpansionTable,
    InMemoryPostingIndex,
    InMemorySymbolStore,
)


def make_element(symbol_id: int, **overrides: object) -> Element:
    defaults: dict[str, object] = {
        "symbol_id": symbol_id,
        "name": f"s{symbol_id}",
        "kind": "method",
        "file": "A.java",
        "span": (1, 2),
        "language": "java",
    }
    return Element(**{**defaults, **overrides})  # type: ignore[arg-type]


def make_frag(*elements: Element, scores: dict[int, dict[str, float]] | None = None) -> Frag:
    return Frag(
        nodes={e.symbol_id: e for e in elements},
        evidence={
            sid: Evidence(
                tuple(
                    UnitHit(unit=unit, signal="lexical", detail="x", score=value)
                    for unit, value in units.items()
                )
            )
            for sid, units in (scores or {}).items()
        },
    )


def make_context(edges: tuple[Edge, ...] = ()) -> EvalContext:
    return EvalContext(
        symbols=InMemorySymbolStore([]),
        postings=InMemoryPostingIndex({}, total_symbols=100),
        expansion=InMemoryExpansionTable({}),
        edges=InMemoryEdgeStore(edges),
    )


class TestOnly:
    def test_filters_by_element_kind(self) -> None:
        frag = make_frag(make_element(1, kind="method"), make_element(2, kind="class"))
        assert set(only(frag, kind="method").nodes) == {1}

    def test_accepts_several_kinds(self) -> None:
        frag = make_frag(
            make_element(1, kind="method"),
            make_element(2, kind="class"),
            make_element(3, kind="variable"),
        )
        assert set(only(frag, kind=["method", "class"]).nodes) == {1, 2}

    def test_filters_by_file(self) -> None:
        frag = make_frag(make_element(1, file="A.java"), make_element(2, file="B.java"))
        assert set(only(frag, file="B.java").nodes) == {2}

    def test_filters_by_language(self) -> None:
        frag = make_frag(make_element(1, language="java"), make_element(2, language="python"))
        assert set(only(frag, language="python").nodes) == {2}

    def test_several_conditions_combine_with_and(self) -> None:
        frag = make_frag(
            make_element(1, kind="method", file="A.java"),
            make_element(2, kind="method", file="B.java"),
        )
        assert set(only(frag, kind="method", file="A.java").nodes) == {1}

    def test_None_disables_a_condition(self) -> None:
        frag = make_frag(make_element(1), make_element(2))
        assert len(only(frag, kind=None)) == 2

    def test_an_empty_sequence_filters_everything_unlike_None(self) -> None:
        frag = make_frag(make_element(1), make_element(2))
        assert not only(frag, kind=[])

    def test_the_where_escape_hatch(self) -> None:
        frag = make_frag(make_element(1, name="getUser"), make_element(2, name="setUser"))
        assert set(only(frag, where=lambda e: e.name.startswith("get")).nodes) == {1}

    def test_preserves_evidence(self) -> None:
        frag = make_frag(make_element(1), scores={1: {"io": 0.5}})
        assert only(frag, kind="method").evidence_for(1).scores["io"] == 0.5

    def test_drops_dangling_edges(self) -> None:
        frag = Frag(
            nodes={1: make_element(1), 2: make_element(2, kind="class")},
            edges={(1, 2, "calls"): Edge(1, 2, "calls")},
        )
        assert only(frag, kind="method").edges == {}


class TestTop:
    def _frag(self) -> Frag:
        return make_frag(
            make_element(1),
            make_element(2),
            make_element(3),
            scores={1: {"io": 0.9}, 2: {"io": 0.5}, 3: {"io": 0.7}},
        )

    def test_takes_the_top_n(self) -> None:
        assert set(top(self._frag(), 2, by="io").nodes) == {1, 3}

    def test_returns_everything_when_n_exceeds_the_total(self) -> None:
        assert len(top(self._frag(), 99, by="io")) == 3

    def test_n_of_0_returns_nothing(self) -> None:
        assert not top(self._frag(), 0, by="io")

    def test_a_negative_n_raises(self) -> None:
        with pytest.raises(ValueError, match="non-negative"):
            top(self._frag(), -1)

    def test_without_by_it_ranks_on_the_sum_across_units(self) -> None:
        frag = make_frag(
            make_element(1),
            make_element(2),
            scores={1: {"io": 0.4, "perf": 0.4}, 2: {"io": 0.6}},
        )
        assert set(top(frag, 1).nodes) == {1}

    def test_ranking_on_one_unit_differs_from_the_sum(self) -> None:
        frag = make_frag(
            make_element(1),
            make_element(2),
            scores={1: {"io": 0.4, "perf": 0.9}, 2: {"io": 0.6}},
        )
        assert set(top(frag, 1, by="io").nodes) == {2}
        assert set(top(frag, 1).nodes) == {1}

    def test_ties_break_on_ascending_symbol_id_so_results_reproduce(self) -> None:
        frag = make_frag(make_element(3), make_element(1), scores={3: {"io": 0.5}, 1: {"io": 0.5}})
        assert set(top(frag, 1, by="io").nodes) == {1}

    def test_a_node_without_evidence_scores_0(self) -> None:
        frag = make_frag(make_element(1), make_element(2), scores={1: {"io": 0.5}})
        assert set(top(frag, 1, by="io").nodes) == {1}
        assert score_of(frag, 2, "io") == 0.0


class TestDegree:
    def _ctx(self) -> EvalContext:
        return make_context(
            (Edge(1, 2, "calls"), Edge(3, 2, "calls"), Edge(2, 4, "calls"), Edge(1, 5, "contains"))
        )

    def _frag(self) -> Frag:
        return make_frag(*(make_element(i) for i in range(1, 6)))

    def test_a_max_in_degree_selects_entry_points(self) -> None:
        assert set(degree(self._frag(), self._ctx(), max_in=0).nodes) == {1, 3, 5}

    def test_a_min_in_degree_finds_widely_called_code(self) -> None:
        assert set(degree(self._frag(), self._ctx(), min_in=2).nodes) == {2}

    def test_out_degree_conditions(self) -> None:
        assert set(degree(self._frag(), self._ctx(), min_out=1).nodes) == {1, 2, 3}

    def test_counted_per_edge_kind(self) -> None:
        """A `contains` edge must not count towards call degree."""
        assert 5 in degree(self._frag(), self._ctx(), edge="calls", max_in=0).nodes
        assert 5 not in degree(self._frag(), self._ctx(), edge="contains", max_in=0).nodes

    def test_edge_of_None_places_no_kind_restriction(self) -> None:
        assert 5 not in degree(self._frag(), self._ctx(), edge=None, max_in=0).nodes

    def test_degree_is_over_the_whole_graph_not_within_the_fragment(self) -> None:
        """ "This function is called from many places" is about its standing
        in the codebase."""
        partial = make_frag(make_element(2))
        assert set(degree(partial, self._ctx(), min_in=2).nodes) == {2}

    def test_returns_everything_unchanged_when_given_no_condition(self) -> None:
        assert len(degree(self._frag(), self._ctx())) == 5
