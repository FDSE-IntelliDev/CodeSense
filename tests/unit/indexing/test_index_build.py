"""Tests for posting accumulation and the compound-segmentation pass."""

from __future__ import annotations

from codesense.indexing.postings import PostingTable
from codesense.text.split import Splitter


def table() -> PostingTable:
    postings = PostingTable()
    postings.add("iostat", 1, "name")
    postings.add("io", 2, "name")
    postings.add("stat", 3, "name")
    return postings


class TestPostingTable:
    def test_counts_repeats_as_term_frequency(self) -> None:
        postings = PostingTable()
        postings.add("buf", 1, "name")
        postings.add("buf", 1, "name")
        assert postings.flatten()["buf"][0]["tf"] == 2

    def test_keeps_fields_apart(self) -> None:
        """`buffer` in a name and in a doc are evidence of different
        strength."""
        postings = PostingTable()
        postings.add("buf", 1, "name")
        postings.add("buf", 1, "doc")
        assert len(postings.flatten()["buf"]) == 2

    def test_vocabulary_counts_distinct_symbols(self) -> None:
        postings = PostingTable()
        postings.add("buf", 1, "name")
        postings.add("buf", 1, "doc")
        postings.add("buf", 2, "name")
        assert postings.vocabulary()["buf"] == 2

    def test_flattens_in_a_stable_order(self) -> None:
        assert list(table().flatten()) == sorted(table().flatten())


class TestRefine:
    def test_pieces_inherit_the_compounds_symbols(self) -> None:
        """The postings already say which symbols carry `iostat`, which is why
        this needs no second parse."""
        postings = table()
        postings.refine(Splitter({"io": 500, "stat": 200, "iostat": 3}).segment)
        assert {p["symbol_id"] for p in postings.flatten()["io"]} == {1, 2}

    def test_keeps_the_compound(self) -> None:
        postings = table()
        postings.refine(Splitter({"io": 500, "stat": 200, "iostat": 3}).segment)
        assert "iostat" in postings.flatten()

    def test_reports_how_many_were_segmented(self) -> None:
        postings = table()
        assert postings.refine(Splitter({"io": 500, "stat": 200, "iostat": 3}).segment) == 1

    def test_segmenting_nothing_changes_nothing(self) -> None:
        postings = table()
        before = postings.flatten()
        postings.refine(lambda term: ())
        assert postings.flatten() == before
