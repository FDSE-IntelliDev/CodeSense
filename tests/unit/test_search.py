"""Unit tests for the search entry point.

The lexical route needs no model, so it carries most of these. What matters
about the LLM routes is not what they return but that **a failure degrades
instead of crashing** -- a search that dies because an endpoint returned 401 is
worse than one that quietly does less.
"""

from __future__ import annotations

import importlib
from collections.abc import Sequence

import pytest

from codesense.index import Index
from codesense.llm.config import LlmConfig
from codesense.ql.frag import Evidence, Frag, UnitHit, Verdict
from codesense.ql.judge import JudgeItem
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


class CountingJudge:
    """Keep every candidate while recording each element submitted to intent."""

    def __init__(self) -> None:
        self.items: list[JudgeItem] = []

    def judge(self, concept: str, items: Sequence[JudgeItem]) -> dict[int, Verdict]:
        self.items.extend(items)
        return {item.symbol_id: Verdict("test", "yes", f"matches {concept}", 1.0) for item in items}


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


def make_file_target_context(owners: Sequence[int]):  # type: ignore[no-untyped-def]
    """Build a realistic large declaration population with controlled file aggregation."""
    declaration_count = 1000
    file_count = max(owners, default=-1) + 1
    symbols = [
        {
            "symbol_id": symbol_id,
            "name": f"alloc{symbol_id}" if symbol_id <= len(owners) else f"Other{symbol_id}",
            "kind": "method",
            "file": (
                f"src/F{owners[symbol_id - 1]}.java"
                if symbol_id <= len(owners)
                else f"src/Other{symbol_id}.java"
            ),
            "span": [1, 2],
            "signature": "",
            "container": "demo",
            "doc": "",
            "language": "java",
            "modifiers": [],
        }
        for symbol_id in range(1, declaration_count + 1)
    ]
    symbols.extend(
        {
            "symbol_id": declaration_count + file_index + 1,
            "name": f"F{file_index}.java",
            "kind": "file",
            "file": f"src/F{file_index}.java",
            "span": [1, 2],
            "signature": "",
            "container": "",
            "doc": "",
            "language": "java",
            "modifiers": [],
        }
        for file_index in range(file_count)
    )
    index = make_index()
    index.payload = {
        "symbols": symbols,
        "postings": {
            "alloc": [
                {"symbol_id": symbol_id, "field": "name", "tf": 1}
                for symbol_id in range(1, len(owners) + 1)
            ]
        },
        "edges": [
            {
                "source_id": symbol_id,
                "target_id": declaration_count + owner + 1,
                "kind": "in_file",
                "site": [1, 1],
                "confidence": 1.0,
                "provenance": "source_path",
            }
            for symbol_id, owner in enumerate(owners, 1)
        ],
        "declaration_count": declaration_count,
    }
    return index.to_context()


def planned_understanding(*, target: object, concept: str = "") -> dict[str, object]:
    return {
        "terms": {"alloc": 1.0},
        "groups": {},
        "relations": (),
        "annotations": (),
        "concept": concept,
        "target": target,
    }


class MissingTargetPlanningResponse:
    """OpenAI-compatible response with a genuinely missing target field."""

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return {
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"terms":{"alloc":1.0},"groups":{},"relations":[],'
                            '"annotations":[],"concept":""}'
                        )
                    }
                }
            ]
        }


class MissingTargetPlanningSession:
    def post(self, *_args: object, **_kwargs: object) -> MissingTargetPlanningResponse:
        return MissingTargetPlanningResponse()


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

    def test_planned_missing_target_defers_to_lexical_output_inference(
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
            "Find Java files containing alloc",
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

    def test_structured_empty_target_suppresses_query_text_inference(
        self, ctx, monkeypatch: pytest.MonkeyPatch
    ) -> None:  # type: ignore[no-untyped-def]
        monkeypatch.setattr(
            "codesense.llm.QueryUnderstanding.understand",
            lambda *args: planned_understanding(target=()),
        )

        result = search(
            "find methods that write a file using alloc",
            ctx,
            route="planned",
            llm=object(),
            vocabulary=(("alloc", 2),),
        )

        assert result.route == "planned"
        assert result.target == ()
        assert {hit.kind for hit in result.hits} == {"class", "method"}

    @pytest.mark.parametrize("failure_point", ["build_spec", "plan"])
    def test_planned_file_target_survives_a_downstream_failure(
        self,
        ctx,
        monkeypatch: pytest.MonkeyPatch,
        failure_point: str,
    ) -> None:  # type: ignore[no-untyped-def]
        monkeypatch.setattr(
            "codesense.llm.QueryUnderstanding.understand",
            lambda *args: planned_understanding(target=("file",)),
        )

        def fail(*args: object, **kwargs: object) -> None:
            raise RuntimeError(f"forced {failure_point} failure")

        monkeypatch.setattr(f"codesense.ql.compile.{failure_point}", fail)

        result = search(
            "alloc",
            ctx,
            route="planned",
            llm=object(),
            vocabulary=(("alloc", 2),),
        )

        assert result.route == "lexical"
        assert result.target == ("file",)
        assert {hit.kind for hit in result.hits} == {"file"}
        assert any(f"RuntimeError: forced {failure_point} failure" in note for note in result.notes)

    def test_planned_empty_target_survives_failure_and_suppresses_inference(
        self, ctx, monkeypatch: pytest.MonkeyPatch
    ) -> None:  # type: ignore[no-untyped-def]
        monkeypatch.setattr(
            "codesense.llm.QueryUnderstanding.understand",
            lambda *args: planned_understanding(target=()),
        )

        def fail(*args: object, **kwargs: object) -> None:
            raise RuntimeError("forced plan failure")

        monkeypatch.setattr("codesense.ql.compile.plan", fail)

        result = search(
            "find methods that write a file using alloc",
            ctx,
            route="planned",
            llm=object(),
            vocabulary=(("alloc", 2),),
        )

        assert result.route == "lexical"
        assert result.target == ()
        assert {hit.kind for hit in result.hits} == {"class", "method"}
        assert any("RuntimeError: forced plan failure" in note for note in result.notes)

    @pytest.mark.parametrize("explicit_target", [(), "unsupported"])
    def test_explicit_empty_target_overrides_planned_target_during_fallback(
        self,
        ctx,
        monkeypatch: pytest.MonkeyPatch,
        explicit_target: object,
    ) -> None:  # type: ignore[no-untyped-def]
        monkeypatch.setattr(
            "codesense.llm.QueryUnderstanding.understand",
            lambda *args: planned_understanding(target=("file",)),
        )

        def fail(*args: object, **kwargs: object) -> None:
            raise RuntimeError("forced build failure")

        monkeypatch.setattr("codesense.ql.compile.build_spec", fail)

        result = search(
            "find Java files containing alloc",
            ctx,
            route="planned",
            llm=object(),
            vocabulary=(("alloc", 2),),
            target=explicit_target,  # type: ignore[arg-type]
        )

        assert result.route == "lexical"
        assert result.target == ()
        assert {hit.kind for hit in result.hits} == {"class", "method"}

    @pytest.mark.parametrize("judge_enabled", [False, True])
    def test_planned_judging_obeys_the_public_flag_and_precedes_file_projection(
        self,
        monkeypatch: pytest.MonkeyPatch,
        judge_enabled: bool,
    ) -> None:
        judge = CountingJudge()
        ctx = make_searchable_index().to_context(judge=judge)
        monkeypatch.setattr(
            "codesense.llm.QueryUnderstanding.understand",
            lambda *args: planned_understanding(
                target=("file",), concept="declarations that allocate buffers"
            ),
        )

        result = search(
            "alloc",
            ctx,
            route="planned",
            llm=object(),
            vocabulary=(("alloc", 2),),
            judge=judge_enabled,
        )

        assert result.route == "planned"
        assert result.target == ("file",)
        if judge_enabled:
            assert [item.symbol_id for item in judge.items] == [1, 2]
            assert {item.kind for item in judge.items} == {"class", "method"}
        else:
            assert judge.items == []

    def test_planned_late_failure_does_not_judge_fallback_twice(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        judge = CountingJudge()
        ctx = make_searchable_index().to_context(judge=judge)
        monkeypatch.setattr(
            "codesense.llm.QueryUnderstanding.understand",
            lambda *args: planned_understanding(
                target=("file",), concept="declarations that allocate buffers"
            ),
        )
        plan_module = importlib.import_module("codesense.ql.compile.plan")

        def fail(*args: object, **kwargs: object) -> None:
            raise RuntimeError("forced projection failure")

        monkeypatch.setattr(plan_module.ProjectTarget, "apply", fail)

        result = search(
            "alloc",
            ctx,
            route="planned",
            llm=object(),
            vocabulary=(("alloc", 2),),
            judge=True,
        )

        assert result.route == "lexical"
        assert result.target == ("file",)
        assert {hit.kind for hit in result.hits} == {"file"}
        assert [item.symbol_id for item in judge.items] == [1, 2]


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

    @pytest.mark.parametrize(
        "query",
        [
            "Find Java files containing alloc references",
            "List source files with alloc",
            "Show files matching alloc",
            "Return a file for alloc",
            "Which files contain alloc",
            "列出 Java 文件中包含 alloc 的项",
            "哪些文件包含 alloc",
        ],
    )
    def test_clear_output_requests_infer_the_file_target(self, ctx, query: str) -> None:  # type: ignore[no-untyped-def]
        result = search(query, ctx, route="lexical")

        assert result.target == ("file",)
        assert {hit.kind for hit in result.hits} == {"file"}

    @pytest.mark.parametrize(
        "query",
        [
            "find methods that write a file using alloc",
            "find methods that write files using alloc",
            "find methods that return a file using alloc",
            "find methods that list files using alloc",
            "find file-writing methods using alloc",
            "find file handlers using alloc",
            "查找返回文件的 alloc 方法",
            "查找文件处理 alloc 方法",
        ],
    )
    def test_file_in_object_position_does_not_infer_a_file_target(self, ctx, query: str) -> None:  # type: ignore[no-untyped-def]
        result = search(query, ctx, route="lexical")

        assert result.target == ()
        assert {hit.kind for hit in result.hits} == {"class", "method"}

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
        result = search("find files containing alloc", ctx, route="codegen", llm=Exploding())

        assert result.route == "lexical"
        assert result.target == ("file",)
        assert {hit.kind for hit in result.hits} == {"file"}
        assert any("lexical approximation" in note for note in result.notes)

    def test_codegen_returned_files_override_object_position_text(
        self, ctx, monkeypatch: pytest.MonkeyPatch
    ) -> None:  # type: ignore[no-untyped-def]
        source = (
            'unit = QueryUnit("q", satisfiers=(LexicalSatisfier(terms=(Term("alloc"),)),))\n'
            'answer = project(eval_unit(unit, ctx), ctx, edge="in_file", '
            'kind=("file",), include_self=True)\n'
        )
        monkeypatch.setattr(
            "codesense.llm.ScriptGenerator.generate",
            lambda *args, **kwargs: source,
        )

        result = search(
            "find methods that write a file using alloc",
            ctx,
            route="codegen",
            llm=object(),
        )

        assert result.target == ("file",)
        assert [hit.kind for hit in result.hits] == ["file"]

    def test_public_limit_above_one_hundred_reaches_the_planned_spec(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ctx = make_file_target_context(tuple(range(120)))
        monkeypatch.setattr(
            "codesense.llm.QueryUnderstanding.understand",
            lambda *args: planned_understanding(target=("file",)),
        )

        result = search(
            "alloc",
            ctx,
            route="planned",
            llm=object(),
            vocabulary=(("alloc", 120),),
            limit=120,
        )

        assert len(result.hits) == 120
        assert "[:120]" in result.script

    def test_small_public_limit_is_applied_after_file_aggregation(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ctx = make_file_target_context((0, 0, 1))
        monkeypatch.setattr(
            "codesense.llm.QueryUnderstanding.understand",
            lambda *args: planned_understanding(target=("file",)),
        )

        result = search(
            "alloc",
            ctx,
            route="planned",
            llm=object(),
            vocabulary=(("alloc", 3),),
            limit=2,
        )

        assert [hit.file for hit in result.hits] == ["src/F0.java", "src/F1.java"]
        assert result.script.index("project(") < result.script.index("[:2]")

    def test_missing_model_target_is_inferred_before_planned_limiting(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The real compiler boundary must preserve missing-target semantics."""
        ctx = make_file_target_context((0, 0, 1))
        monkeypatch.setattr("requests.Session", MissingTargetPlanningSession)

        result = search(
            "Find Java files containing alloc",
            ctx,
            route="planned",
            llm=LlmConfig(api_key="test"),
            vocabulary=(("alloc", 3),),
            limit=2,
        )

        assert result.route == "planned"
        assert result.target == ("file",)
        assert [hit.file for hit in result.hits] == ["src/F0.java", "src/F1.java"]
        assert result.script.index("project(") < result.script.index("[:2]")


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
