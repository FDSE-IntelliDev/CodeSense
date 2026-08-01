"""Unit tests for `codesense.ql.frag`.

The focus is the invariants ``docs/design/03-data-model.md`` names
explicitly, all of which are the ones easiest to miss when implementing:

- evidence takes no part in equality, or ``a | a != a``
- intersection must merge evidence, or the result holds elements with no
  stateable reason
- a fragment's edges must not dangle
"""

from __future__ import annotations

import pytest

from codesense.ql import Edge, Element, Evidence, Frag, Path, UnitHit, Verdict


def make_element(symbol_id: int, name: str = "") -> Element:
    return Element(
        symbol_id=symbol_id,
        name=name or f"sym{symbol_id}",
        kind="method",
        file="A.java",
        span=(symbol_id, symbol_id + 1),
    )


def make_frag(*symbol_ids: int, edges: tuple[Edge, ...] = ()) -> Frag:
    return Frag(
        nodes={sid: make_element(sid) for sid in symbol_ids},
        edges={e.key: e for e in edges},
    )


def hit(unit: str, detail: str = "x", score: float = 1.0) -> UnitHit:
    return UnitHit(unit=unit, signal="lexical", detail=detail, score=score)


class TestFragInvariants:
    def test_edges_must_not_point_outside_the_fragment(self) -> None:
        with pytest.raises(ValueError, match="outside the fragment"):
            Frag(nodes={1: make_element(1)}, edges={(1, 2, "calls"): Edge(1, 2, "calls")})

    def test_the_mappings_are_read_only_frozen_alone_would_not_stop_mutation(self) -> None:
        frag = make_frag(1)
        with pytest.raises(TypeError):
            frag.nodes[2] = make_element(2)  # type: ignore[index]

    def test_a_fragment_is_unhashable(self) -> None:
        with pytest.raises(TypeError):
            hash(make_frag(1))

    def test_mutating_the_dict_passed_at_construction_does_not_affect_the_fragment(self) -> None:
        nodes = {1: make_element(1)}
        frag = Frag(nodes=nodes)
        nodes[2] = make_element(2)
        assert len(frag) == 1


class TestEquality:
    def test_equality_looks_at_nodes_and_edges_not_evidence(self) -> None:
        left = Frag(nodes={1: make_element(1)}, evidence={1: Evidence((hit("io"),))})
        right = Frag(nodes={1: make_element(1)}, evidence={1: Evidence((hit("disk"),))})
        assert left == right

    def test_idempotent_union_with_itself_is_itself(self) -> None:
        frag = make_frag(1, 2)
        assert frag | frag == frag

    def test_union_with_differing_evidence_still_equals_the_original(self) -> None:
        left = Frag(nodes={1: make_element(1)}, evidence={1: Evidence((hit("io"),))})
        right = Frag(nodes={1: make_element(1)}, evidence={1: Evidence((hit("disk"),))})
        assert left | right == left

    def test_comparing_against_a_non_fragment_returns_NotImplemented(self) -> None:
        assert make_frag(1) != "not a fragment"


class TestAlgebra:
    def test_union(self) -> None:
        assert set((make_frag(1, 2) | make_frag(2, 3)).nodes) == {1, 2, 3}

    def test_intersection(self) -> None:
        assert set((make_frag(1, 2) & make_frag(2, 3)).nodes) == {2}

    def test_difference(self) -> None:
        assert set((make_frag(1, 2) - make_frag(2)).nodes) == {1}

    def test_intersection_merges_evidence_from_both_sides(self) -> None:
        """The one the design doc names as easiest to miss."""
        left = Frag(nodes={1: make_element(1)}, evidence={1: Evidence((hit("io"),))})
        right = Frag(nodes={1: make_element(1)}, evidence={1: Evidence((hit("perf"),))})
        units = {h.unit for h in (left & right).evidence_for(1).unit_hits}
        assert units == {"io", "perf"}

    def test_union_merges_evidence_from_both_sides(self) -> None:
        left = Frag(nodes={1: make_element(1)}, evidence={1: Evidence((hit("io"),))})
        right = Frag(nodes={1: make_element(1)}, evidence={1: Evidence((hit("perf"),))})
        units = {h.unit for h in (left | right).evidence_for(1).unit_hits}
        assert units == {"io", "perf"}

    def test_evidence_merging_deduplicates_in_order(self) -> None:
        same = Evidence((hit("io", "buf"), hit("perf", "cache")))
        merged = same.merge(Evidence((hit("io", "buf"),)))
        assert [h.detail for h in merged.unit_hits] == ["buf", "cache"]

    def test_difference_keeps_its_own_evidence(self) -> None:
        left = Frag(
            nodes={1: make_element(1), 2: make_element(2)},
            evidence={1: Evidence((hit("io"),)), 2: Evidence((hit("perf"),))},
        )
        result = left - make_frag(2)
        assert [h.unit for h in result.evidence_for(1).unit_hits] == ["io"]
        assert result.evidence_for(2).unit_hits == ()

    def test_difference_drops_dangling_edges(self) -> None:
        frag = make_frag(1, 2, edges=(Edge(1, 2, "calls"),))
        assert (frag - make_frag(2)).edges == {}

    def test_intersection_keeps_edges_with_both_ends_present(self) -> None:
        left = make_frag(1, 2, edges=(Edge(1, 2, "calls"),))
        both = left & make_frag(1, 2)
        assert (1, 2, "calls") in both.edges

    def test_intersection_drops_edges_with_only_one_end_present(self) -> None:
        left = make_frag(1, 2, edges=(Edge(1, 2, "calls"),))
        assert (left & make_frag(1)).edges == {}


class TestProjection:
    def test_roots_are_the_nodes_with_in_degree_0(self) -> None:
        frag = make_frag(1, 2, 3, edges=(Edge(1, 2, "calls"), Edge(2, 3, "calls")))
        assert set(frag.roots().nodes) == {1}

    def test_leaves_are_the_nodes_with_out_degree_0(self) -> None:
        frag = make_frag(1, 2, 3, edges=(Edge(1, 2, "calls"), Edge(2, 3, "calls")))
        assert set(frag.leaves().nodes) == {3}

    def test_without_edges_roots_and_leaves_are_everything(self) -> None:
        frag = make_frag(1, 2)
        assert set(frag.roots().nodes) == set(frag.leaves().nodes) == {1, 2}

    def test_only_nodes_drops_edges_and_path_witnesses(self) -> None:
        frag = Frag(
            nodes={1: make_element(1), 2: make_element(2)},
            edges={(1, 2, "calls"): Edge(1, 2, "calls")},
            witnesses=(Path((1, 2), (Edge(1, 2, "calls"),)),),
        )
        bare = frag.only_nodes()
        assert bare.edges == {}
        assert bare.witnesses == ()
        assert set(bare.nodes) == {1, 2}

    def test_only_nodes_keeps_evidence(self) -> None:
        frag = Frag(nodes={1: make_element(1)}, evidence={1: Evidence((hit("io"),))})
        assert frag.only_nodes().evidence_for(1).unit_hits != ()

    def test_induced_drops_incomplete_path_witnesses(self) -> None:
        frag = Frag(
            nodes={sid: make_element(sid) for sid in (1, 2, 3)},
            edges={(1, 2, "calls"): Edge(1, 2, "calls")},
            witnesses=(Path((1, 2), (Edge(1, 2, "calls"),)),),
        )
        assert frag.induced([1, 3]).witnesses == ()

    def test_induced_ignores_ids_absent_from_the_fragment(self) -> None:
        assert set(make_frag(1, 2).induced([2, 999]).nodes) == {2}


class TestEvidence:
    def test_scores_aggregate_per_unit(self) -> None:
        ev = Evidence((hit("io", "a", 0.5), hit("io", "b", 0.25), hit("perf", "c", 1.0)))
        assert ev.scores == {"io": 0.75, "perf": 1.0}

    def test_scores_is_a_read_only_derived_value(self) -> None:
        with pytest.raises(TypeError):
            Evidence((hit("io"),)).scores["io"] = 9.0  # type: ignore[index]

    def test_a_verdict_carries_a_reason(self) -> None:
        ev = Evidence(verdicts=(Verdict("llm", "yes", "it is the login entry point"),))
        assert ev.verdicts[0].reason == "it is the login entry point"

    def test_evidence_for_a_missing_node_is_empty_rather_than_raising(self) -> None:
        assert make_frag(1).evidence_for(999) == Evidence()


class TestContainerProtocol:
    def test_len_is_the_node_count(self) -> None:
        assert len(make_frag(1, 2, 3)) == 3

    def test_iteration_yields_elements(self) -> None:
        assert {e.symbol_id for e in make_frag(1, 2)} == {1, 2}

    def test_in_works_on_symbol_id(self) -> None:
        assert 1 in make_frag(1)
        assert 2 not in make_frag(1)

    def test_an_empty_fragment_is_falsy(self) -> None:
        assert not Frag()
        assert make_frag(1)

    def test_a_paths_length_is_its_edge_count(self) -> None:
        assert len(Path((1, 2, 3), (Edge(1, 2, "calls"), Edge(2, 3, "calls")))) == 2
