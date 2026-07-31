"""Unit tests for `codesense.ql.store` and `codesense.ql.fields`."""

from __future__ import annotations

import math

import pytest

from codesense.ql import DEFAULT_FIELD_WEIGHTS, Edge, Element, FieldWeights, IndexField
from codesense.ql.store import (
    Expansion,
    InMemoryEdgeStore,
    InMemoryExpansionTable,
    InMemoryPostingIndex,
    InMemorySymbolStore,
    Posting,
)


def make_element(symbol_id: int) -> Element:
    return Element(
        symbol_id=symbol_id,
        name=f"sym{symbol_id}",
        kind="method",
        file="A.java",
        span=(symbol_id, symbol_id + 1),
    )


class TestIndexField:
    def test_usable_directly_as_a_string(self) -> None:
        assert IndexField.NAME == "name"
        assert f"{IndexField.DOC}" == "doc"

    def test_the_value_set_is_constrained(self) -> None:
        with pytest.raises(ValueError, match="body"):
            IndexField("body")


class TestFieldWeights:
    def test_the_name_field_weighs_most(self) -> None:
        w = DEFAULT_FIELD_WEIGHTS
        assert w.weight(IndexField.NAME) > w.weight(IndexField.ANNOTATION)
        assert w.weight(IndexField.ANNOTATION) > w.weight(IndexField.DOC)

    def test_an_unknown_field_scores_0_rather_than_silently_1(self) -> None:
        assert DEFAULT_FIELD_WEIGHTS.weight("body") == 0.0

    def test_accepts_both_a_string_and_the_enum(self) -> None:
        w = DEFAULT_FIELD_WEIGHTS
        assert w.weight("name") == w.weight(IndexField.NAME)

    def test_custom_weights_can_be_injected(self) -> None:
        assert FieldWeights(name=0.5).weight(IndexField.NAME) == 0.5

    def test_the_weight_table_is_read_only(self) -> None:
        with pytest.raises(TypeError):
            DEFAULT_FIELD_WEIGHTS.as_mapping()["name"] = 9.0  # type: ignore[index]


class TestSymbolStore:
    def test_get_one(self) -> None:
        store = InMemorySymbolStore([make_element(1)])
        assert store.get(1) is not None
        assert store.get(999) is None

    def test_get_many_simply_omits_what_is_missing(self) -> None:
        store = InMemorySymbolStore([make_element(1), make_element(2)])
        assert set(store.get_many([1, 2, 999])) == {1, 2}

    def test_count_is_the_total_number_of_symbols(self) -> None:
        assert InMemorySymbolStore([make_element(1), make_element(2)]).count() == 2


class TestPostingIndex:
    def test_exact_lookup(self) -> None:
        idx = InMemoryPostingIndex({"buf": [Posting(1, IndexField.NAME)]}, total_symbols=10)
        assert len(idx.lookup("buf")) == 1

    def test_a_miss_returns_an_empty_sequence_not_None(self) -> None:
        idx = InMemoryPostingIndex({}, total_symbols=10)
        assert idx.lookup("buffer") == ()

    def test_does_no_fuzzy_matching_whatsoever(self) -> None:
        """The index stays exact; all the fuzziness lives in the expansion
        table."""
        idx = InMemoryPostingIndex({"buf": [Posting(1, IndexField.NAME)]}, total_symbols=10)
        assert idx.lookup("buffer") == ()

    def test_df_counts_distinct_symbols_not_postings(self) -> None:
        """A symbol hit in both name and doc counts once."""
        idx = InMemoryPostingIndex(
            {"buf": [Posting(1, IndexField.NAME), Posting(1, IndexField.DOC)]},
            total_symbols=10,
        )
        info = idx.term_info("buf")
        assert info is not None
        assert info.df == 1

    def test_icf_is_symbol_level(self) -> None:
        idx = InMemoryPostingIndex(
            {"get": [Posting(i, IndexField.NAME) for i in range(207)]},
            total_symbols=1718,
        )
        info = idx.term_info("get")
        assert info is not None
        assert info.icf == pytest.approx(math.log(1718 / 207))

    def test_a_generic_word_has_lower_icf_than_a_rare_one(self) -> None:
        idx = InMemoryPostingIndex(
            {
                "get": [Posting(i, IndexField.NAME) for i in range(207)],
                "login": [Posting(i, IndexField.NAME) for i in range(20)],
            },
            total_symbols=1718,
        )
        assert idx.term_info("get").icf < idx.term_info("login").icf  # type: ignore[union-attr]

    def test_stats_for_an_unknown_term_are_None(self) -> None:
        assert InMemoryPostingIndex({}, total_symbols=10).term_info("x") is None

    def test_icf_is_0_rather_than_raising_when_df_is_0(self) -> None:
        idx = InMemoryPostingIndex({"x": []}, total_symbols=10)
        assert idx.term_info("x").icf == 0.0  # type: ignore[union-attr]

    def test_every_term_can_be_iterated(self) -> None:
        idx = InMemoryPostingIndex({"a": [], "b": []}, total_symbols=10)
        assert set(idx.terms()) == {"a", "b"}


class TestExpansionTable:
    def test_ordered_by_descending_score(self) -> None:
        table = InMemoryExpansionTable(
            {"buffer": [Expansion("bfr", 0.72, "subseq"), Expansion("buf", 0.91, "prefix")]}
        )
        assert [e.target for e in table.expand("buffer")] == ["buf", "bfr"]

    def test_a_miss_returns_an_empty_sequence(self) -> None:
        assert InMemoryExpansionTable({}).expand("buffer") == ()

    def test_the_reason_travels_with_the_expansion(self) -> None:
        table = InMemoryExpansionTable({"@RequestMapping": [Expansion("@GetMapping", 1.0, "meta")]})
        assert table.expand("@RequestMapping")[0].reason == "meta"


class TestEdgeStore:
    def _store(self) -> InMemoryEdgeStore:
        return InMemoryEdgeStore(
            [
                Edge(1, 2, "calls", confidence=0.95),
                Edge(1, 3, "calls_virtual", confidence=0.6),
                Edge(2, 3, "contains", confidence=1.0),
            ]
        )

    def test_outgoing_edges(self) -> None:
        assert {e.target_id for e in self._store().out_edges(1)} == {2, 3}

    def test_incoming_edges(self) -> None:
        assert {e.source_id for e in self._store().in_edges(3)} == {1, 2}

    def test_filters_by_kind(self) -> None:
        assert {e.target_id for e in self._store().out_edges(1, kinds=["calls"])} == {2}

    def test_filters_by_confidence_blocking_virtual_dispatch(self) -> None:
        edges = self._store().out_edges(1, min_confidence=0.9)
        assert {e.kind for e in edges} == {"calls"}

    def test_an_orphan_returns_an_empty_sequence(self) -> None:
        assert self._store().out_edges(999) == ()

    def test_degree_counts_both_directions(self) -> None:
        assert self._store().degree(3) == 2

    def test_degree_can_be_restricted_by_kind(self) -> None:
        assert self._store().degree(1, kinds=["calls"]) == 1
