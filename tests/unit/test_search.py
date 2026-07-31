"""Unit tests for the search entry point.

The lexical route needs no model, so it carries most of these. What matters
about the LLM routes is not what they return but that **a failure degrades
instead of crashing** -- a search that dies because an endpoint returned 401 is
worse than one that quietly does less.
"""

from __future__ import annotations

import pytest

from codesense.index import Index
from codesense.search import ROUTES, Hit, SearchResult, search
from tests.unit.test_index import make_index

#: Filler symbols, so ICF means something.
#:
#: Not padding for its own sake: `icf_ratio` is ``log(N/df)/log(N)``, so in a
#: two-symbol index a term on both symbols scores exactly zero and every hit is
#: discarded. Ranking cannot be tested at all without a corpus to be rare
#: *within*.
FILLER = 30


def make_searchable_index() -> Index:
    index = make_index()
    symbols = index.payload["symbols"]
    postings = index.payload["postings"]
    for i in range(3, 3 + FILLER):
        symbols.append(
            {
                "symbol_id": i,
                "name": f"Unrelated{i}",
                "kind": "class",
                "file": f"b/U{i}.java",
                "span": [1, 2],
                "signature": "",
                "container": "io.y",
                "doc": "",
                "language": "java",
                "modifiers": [],
            }
        )
        postings.setdefault("unrelated", []).append({"symbol_id": i, "field": "name", "tf": 1})
    return index


@pytest.fixture
def ctx():  # type: ignore[no-untyped-def]
    return make_searchable_index().to_context()


class TestRouting:
    def test_rejects_an_unknown_route(self, ctx) -> None:  # type: ignore[no-untyped-def]
        with pytest.raises(ValueError, match="route must be one of"):
            search("x", ctx, route="telepathy")

    def test_without_an_llm_it_falls_back_to_lexical(self, ctx) -> None:  # type: ignore[no-untyped-def]
        """A search that works without a key is worth more than one that
        refuses."""
        assert search("alloc", ctx, route="codegen").route == "lexical"

    def test_a_failing_route_degrades_rather_than_raising(self, ctx) -> None:  # type: ignore[no-untyped-def]
        class Exploding:
            model = "boom"

            def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
                raise RuntimeError("endpoint is down")

        result = search("alloc", ctx, route="codegen", llm=Exploding())
        assert result.route == "lexical"
        assert any("fell back" in note for note in result.notes)

    def test_every_route_is_reachable(self) -> None:
        assert set(ROUTES) == {"codegen", "planned", "lexical"}


class TestLexicalRoute:
    def test_matches_the_querys_own_words(self, ctx) -> None:  # type: ignore[no-untyped-def]
        """Both carry `alloc` in their name, so they tie and the tie breaks on
        ascending symbol id -- which is what makes a result reproducible."""
        assert [h.name for h in search("alloc", ctx, route="lexical")] == [
            "PooledAllocator",
            "allocBuf",
        ]

    def test_reaches_a_project_spelling_through_grounding(self) -> None:
        """The query says `buffer`; the project writes `buf`. Closing that gap
        is what the expansion table is for."""
        index = make_searchable_index()
        index.expansion = {"buffer": [("buf", 0.75, "prefix")]}
        result = search("buffer", index.to_context(), route="lexical")
        assert "allocBuf" in [h.name for h in result]

    def test_drops_stopwords(self, ctx) -> None:  # type: ignore[no-untyped-def]
        """`and` is a real Java identifier, so ICF alone will not push it far
        enough down."""
        assert "and" not in search("alloc and buf", ctx, route="lexical").notes[0]

    def test_a_query_with_no_usable_word_says_so(self, ctx) -> None:  # type: ignore[no-untyped-def]
        result = search("quantum entanglement", ctx, route="lexical")
        assert not result.hits
        assert "vocabulary" in result.notes[0]

    def test_honours_the_limit(self, ctx) -> None:  # type: ignore[no-untyped-def]
        assert len(search("alloc", ctx, route="lexical", limit=1)) == 1

    def test_is_reproducible(self, ctx) -> None:  # type: ignore[no-untyped-def]
        first = [h.symbol_id for h in search("alloc", ctx, route="lexical")]
        assert first == [h.symbol_id for h in search("alloc", ctx, route="lexical")]


class TestHits:
    def test_ranks_from_one(self, ctx) -> None:  # type: ignore[no-untyped-def]
        assert search("alloc", ctx, route="lexical").hits[0].rank == 1

    def test_carries_a_location(self, ctx) -> None:  # type: ignore[no-untyped-def]
        found = {h.name: h for h in search("alloc", ctx, route="lexical")}
        assert found["allocBuf"].file == "a/Pooled.java"
        assert found["allocBuf"].line == 20

    def test_explains_why_it_matched(self, ctx) -> None:  # type: ignore[no-untyped-def]
        """Without this a result is unfalsifiable."""
        assert "alloc" in search("alloc", ctx, route="lexical").hits[0].why

    def test_why_omits_the_summary_hit(self, ctx) -> None:  # type: ignore[no-untyped-def]
        """It repeats the unit total, which is already the score column."""
        assert "combined" not in search("alloc", ctx, route="lexical").hits[0].why

    def test_scores_descend(self, ctx) -> None:  # type: ignore[no-untyped-def]
        scores = [h.score for h in search("alloc pooled", ctx, route="lexical")]
        assert scores == sorted(scores, reverse=True)


class TestSearchResult:
    def test_reports_the_route_and_timing(self, ctx) -> None:  # type: ignore[no-untyped-def]
        text = search("alloc", ctx, route="lexical").explain()
        assert "lexical" in text and "hits in" in text

    def test_explain_includes_the_script_when_there_is_one(self) -> None:
        """The artifact is the point: a result you cannot re-run is not much
        use."""
        result = SearchResult(query="q", script="answer = frag")
        assert "answer = frag" in result.explain()

    def test_iterates_over_hits(self) -> None:
        result = SearchResult(query="q", hits=[Hit(1, 2, "n", "method", "f.java", 3, 0.5)])
        assert [h.name for h in result] == ["n"]

    def test_len_counts_hits(self) -> None:
        assert len(SearchResult(query="q")) == 0
