"""Unit tests for the search entry point.

The lexical route needs no model, so it carries most of these. What matters
about the LLM routes is not what they return but that **a failure degrades
instead of crashing** -- a search that dies because an endpoint returned 401 is
worse than one that quietly does less.
"""

from __future__ import annotations

import importlib

import pytest

from codesense.index import Index
from codesense.ql.frag import Evidence, Frag, UnitHit
from codesense.ql.operators import score_of
from codesense.search import ROUTES, Hit, SearchResult, _cohere, _rank, search
from tests.unit.test_index import make_index

#: Filler symbols, so ICF means something.
#:
#: Not padding for its own sake: `icf_ratio` is ``log(N/df)/log(N)``, so in a
#: two-symbol index a term on both symbols scores exactly zero and every hit is
#: discarded. Ranking cannot be tested at all without a corpus to be rare
#: *within*.
FILLER = 30


class Exploding:
    """LLM-shaped object whose first endpoint access fails."""

    model = "boom"

    def __getattr__(self, name: str) -> object:
        raise RuntimeError("endpoint is down")


def make_searchable_index() -> Index:
    index = make_index()
    symbols = index.payload["symbols"]
    postings = index.payload["postings"]
    index.payload["edges"].extend(
        [
            {
                "source_id": 1,
                "target_id": 3,
                "kind": "in_file",
                "site": [10, 1],
                "confidence": 1.0,
                "provenance": "source_path",
            },
            {
                "source_id": 2,
                "target_id": 3,
                "kind": "in_file",
                "site": [20, 1],
                "confidence": 1.0,
                "provenance": "source_path",
            },
        ]
    )
    first_filler_id = max(symbol["symbol_id"] for symbol in symbols) + 1
    for i in range(first_filler_id, first_filler_id + FILLER):
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
    index.payload["declaration_count"] += FILLER
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
        result = search("alloc", ctx, route="codegen", llm=Exploding())
        assert result.route == "lexical"
        assert any("fell back" in note for note in result.notes)

    def test_every_route_is_reachable(self) -> None:
        assert set(ROUTES) == {"codegen", "planned", "lexical"}

    def test_planned_route_executes_the_validated_spec(
        self, ctx, monkeypatch: pytest.MonkeyPatch
    ) -> None:  # type: ignore[no-untyped-def]
        monkeypatch.setattr(
            "codesense.llm.QueryUnderstanding.understand",
            lambda *args: {
                "terms": {"alloc": 1.0},
                "groups": {},
                "relations": (),
                "annotations": (),
                "concept": "",
            },
        )

        result = search(
            "alloc",
            ctx,
            route="planned",
            llm=object(),
            vocabulary=(("alloc", 2),),
        )

        assert result.route == "planned"
        assert result.hits

    def test_planned_target_is_used_when_the_caller_did_not_supply_one(
        self, ctx, monkeypatch: pytest.MonkeyPatch
    ) -> None:  # type: ignore[no-untyped-def]
        monkeypatch.setattr(
            "codesense.llm.QueryUnderstanding.understand",
            lambda *args: {
                "terms": {"alloc": 1.0},
                "groups": {},
                "relations": (),
                "annotations": (),
                "concept": "",
                "target": ["file"],
            },
        )

        result = search(
            "alloc",
            ctx,
            route="planned",
            llm=object(),
            vocabulary=(("alloc", 2),),
        )

        assert result.route == "planned"
        assert result.target == ("file",)
        assert {hit.kind for hit in result.hits} == {"file"}

    def test_explicit_empty_target_overrides_the_planned_target(
        self, ctx, monkeypatch: pytest.MonkeyPatch
    ) -> None:  # type: ignore[no-untyped-def]
        monkeypatch.setattr(
            "codesense.llm.QueryUnderstanding.understand",
            lambda *args: {
                "terms": {"alloc": 1.0},
                "groups": {},
                "relations": (),
                "annotations": (),
                "concept": "",
                "target": ["file"],
            },
        )

        result = search(
            "alloc",
            ctx,
            route="planned",
            llm=object(),
            vocabulary=(("alloc", 2),),
            target=(),
        )

        assert result.route == "planned"
        assert result.target == ()
        assert {hit.kind for hit in result.hits} == {"class", "method"}


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


class TestResultTarget:
    def test_explicit_file_target_returns_only_files(self, ctx) -> None:  # type: ignore[no-untyped-def]
        result = search("alloc", ctx, route="lexical", target="file")

        assert result.target == ("file",)
        assert {hit.kind for hit in result.hits} == {"file"}
        assert [hit.file for hit in result.hits] == ["a/Pooled.java"]

    @pytest.mark.parametrize("query", ["alloc file", "alloc files", "alloc 文件"])
    def test_file_nouns_infer_the_file_target(self, ctx, query: str) -> None:  # type: ignore[no-untyped-def]
        result = search(query, ctx, route="lexical")

        assert result.target == ("file",)
        assert {hit.kind for hit in result.hits} == {"file"}

    @pytest.mark.parametrize("query", ["alloc import", "alloc reference", "alloc references"])
    def test_relation_words_do_not_infer_a_file_target(self, ctx, query: str) -> None:  # type: ignore[no-untyped-def]
        result = search(query, ctx, route="lexical")

        assert result.target == ()
        assert all(hit.kind != "file" for hit in result.hits)

    def test_explicit_target_wins_over_query_inference(self, ctx) -> None:  # type: ignore[no-untyped-def]
        result = search("alloc files", ctx, route="lexical", target="unknown")

        assert result.target == ()
        assert {hit.kind for hit in result.hits} == {"class", "method"}

    def test_default_search_never_leaks_file_nodes(
        self, ctx, monkeypatch: pytest.MonkeyPatch
    ) -> None:  # type: ignore[no-untyped-def]
        file_frag = Frag(nodes=ctx.symbols.get_many((3,)))
        search_module = importlib.import_module("codesense.search")
        monkeypatch.setattr(
            search_module,
            "_codegen",
            lambda *args: (file_frag, "", [], ()),
        )

        result = search("alloc", ctx, route="codegen", llm=object())

        assert result.target == ()
        assert not result.hits

    def test_fallback_preserves_file_target(self, ctx) -> None:  # type: ignore[no-untyped-def]
        result = search("alloc files", ctx, route="codegen", llm=Exploding())

        assert result.route == "lexical"
        assert result.target == ("file",)
        assert {hit.kind for hit in result.hits} == {"file"}
        assert any("lexical approximation" in note for note in result.notes)


class TestStructuralCoherence:
    def test_boosts_existing_candidate_near_the_strongest_hit(self, ctx) -> None:  # type: ignore[no-untyped-def]
        def scored(value: float) -> Evidence:
            return Evidence(
                unit_hits=(
                    UnitHit(
                        unit="query",
                        signal=Evidence.COMBINED,
                        detail="query score",
                        score=value,
                    ),
                )
            )

        frag = Frag(
            nodes=ctx.symbols.get_many((1, 2, 3)),
            evidence={1: scored(0.9), 2: scored(0.7), 3: scored(0.8)},
        )

        result = _cohere(frag, ctx, seeds=1, boost=0.6)

        assert set(result.nodes) == {1, 2, 3}
        assert score_of(result, 1) == pytest.approx(0.9)
        assert score_of(result, 2) == pytest.approx(1.12)
        assert score_of(result, 3) == pytest.approx(0.8)
        ranked = _rank(result, 3)
        assert [hit.symbol_id for hit in ranked] == [2, 1, 3]
        assert ranked[0].score == pytest.approx(1.12)
        assert "calls/contains" in ranked[0].why
        assert "@graph" in ranked[0].why
        structural = [hit for hit in result.evidence_for(2).unit_hits if hit.signal == "structural"]
        assert len(structural) == 1
        assert structural[0].field == "graph"


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

    def test_reports_the_effective_target(self, ctx) -> None:  # type: ignore[no-untyped-def]
        text = search("alloc", ctx, route="lexical", target="file").explain()
        assert "target: file" in text

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
