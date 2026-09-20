"""Unit tests for the on-disk index and the `Project` facade.

No tree-sitter and no network: the payload is built by hand, which is also the
point of `Index` taking one rather than a repository path.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from codesense.index import FORMAT_VERSION, Index, IndexMeta
from codesense.project import Project


def payload() -> dict:
    return {
        "symbols": [
            {
                "symbol_id": 1,
                "name": "PooledAllocator",
                "kind": "class",
                "file": "a/Pooled.java",
                "span": [10, 40],
                "signature": "",
                "container": "io.x",
                "doc": "allocates pooled buffers",
                "language": "java",
                "modifiers": ["public", "final"],
            },
            {
                "symbol_id": 2,
                "name": "allocBuf",
                "kind": "method",
                "file": "a/Pooled.java",
                "span": [20, 25],
                "signature": "(int n) : ByteBuf",
                "container": "io.x.PooledAllocator",
                "doc": "",
                "language": "java",
                "modifiers": [],
            },
            {
                "symbol_id": 3,
                "name": "Pooled.java",
                "kind": "file",
                "file": "a/Pooled.java",
                "span": [1, 40],
                "signature": "",
                "container": "",
                "doc": "",
                "language": "java",
                "modifiers": [],
            },
        ],
        "postings": {
            "pooled": [{"symbol_id": 1, "field": "name", "tf": 1}],
            "alloc": [
                {"symbol_id": 1, "field": "name", "tf": 1},
                {"symbol_id": 2, "field": "name", "tf": 1},
            ],
            "buf": [{"symbol_id": 2, "field": "name", "tf": 1}],
        },
        "edges": [
            {
                "source_id": 1,
                "target_id": 2,
                "kind": "contains",
                "site": [20, 8],
                "confidence": 1.0,
                "provenance": "derived_container",
            },
            {
                "source_id": 2,
                "target_id": 1,
                "kind": "implements",
                "site": [21, 3],
                "confidence": 0.7,
                "provenance": "java_supertypes_simple",
            },
        ],
        "declaration_count": 2,
    }


def make_index(**meta: object) -> Index:
    base = {"project": "demo", "root": "/src/demo", "built_at": "2026-01-01T00:00:00+00:00"}
    return Index(
        meta=IndexMeta(**{**base, "symbols": 3, "declarations": 2, "files": 1, "edges": 1, **meta}),
        payload=payload(),
    )


class TestRoundTrip:
    def test_saves_and_reloads(self, tmp_path: Path) -> None:
        make_index().save(tmp_path)
        assert len(Index.load(tmp_path)) == 3

    def test_keeps_the_grounding_table(self, tmp_path: Path) -> None:
        index = make_index()
        index.expansion = {"buffer": [("buf", 0.75, "prefix")]}
        index.save(tmp_path)
        assert Index.load(tmp_path).expansion["buffer"] == [("buf", 0.75, "prefix")]

    def test_an_index_without_grounding_writes_no_expansion_file(self, tmp_path: Path) -> None:
        make_index().save(tmp_path)
        assert not (tmp_path / "expansion.json").exists()

    def test_meta_survives(self, tmp_path: Path) -> None:
        make_index(project="netty").save(tmp_path)
        assert Index.load(tmp_path).meta.project == "netty"

    def test_creates_the_directory(self, tmp_path: Path) -> None:
        make_index().save(tmp_path / "deep" / "nested")
        assert (tmp_path / "deep" / "nested" / "meta.json").is_file()

    def test_adapter_relation_evidence_survives_save_load_and_context(self, tmp_path: Path) -> None:
        make_index(edges=2).save(tmp_path)

        edge = next(
            edge
            for edge in Index.load(tmp_path).to_context().edges.out_edges(2)
            if edge.kind == "implements"
        )

        assert edge.site == (21, 3)
        assert edge.confidence == 0.7
        assert edge.provenance == "java_supertypes_simple"


class TestLoadFailures:
    def test_current_format_is_v3(self) -> None:
        assert FORMAT_VERSION == 3

    def test_a_directory_without_meta_is_not_an_index(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="not an index directory"):
            Index.load(tmp_path)

    def test_an_older_format_fails_loudly(self, tmp_path: Path) -> None:
        """Half-working is worse than not loading: the shape changed for a
        reason."""
        make_index().save(tmp_path)
        raw = json.loads((tmp_path / "meta.json").read_text())
        raw["format_version"] = FORMAT_VERSION - 1
        (tmp_path / "meta.json").write_text(json.dumps(raw))
        with pytest.raises(ValueError, match="rebuild it"):
            Index.load(tmp_path)

    def test_meta_is_written_last(self, tmp_path: Path) -> None:
        """Its presence marks the directory complete, so a crash mid-write
        leaves something that fails to load rather than something that loads
        and lies."""
        index = make_index()
        index.save(tmp_path)
        (tmp_path / "meta.json").unlink()
        with pytest.raises(FileNotFoundError):
            Index.load(tmp_path)


class TestVocabulary:
    def test_counts_distinct_symbols_not_postings(self) -> None:
        assert dict(make_index().vocabulary())["alloc"] == 2

    def test_commonest_first(self) -> None:
        assert make_index().vocabulary()[0][0] == "alloc"

    def test_limit_of_zero_returns_everything(self) -> None:
        assert len(make_index().vocabulary(0)) == 3

    def test_honours_a_limit(self) -> None:
        assert len(make_index().vocabulary(2)) == 2

    def test_is_ordered_reproducibly(self) -> None:
        assert make_index().vocabulary() == make_index().vocabulary()


class TestToContext:
    def test_builds_a_queryable_context(self) -> None:
        ctx = make_index().to_context()
        assert ctx.symbols.count() == 3
        assert len(ctx.postings.lookup("alloc")) == 2

    def test_edges_keep_their_site(self) -> None:
        edge = make_index().to_context().edges.out_edges(1)[0]
        assert edge.site == (20, 8)

    def test_context_separates_element_count_from_scoring_population(self) -> None:
        ctx = make_index().to_context()
        assert ctx.symbols.count() == 3
        assert ctx.population == 2
        assert ctx.postings.term_info("alloc").total_symbols == 2

    def test_context_infers_declarations_when_payload_has_no_count(self) -> None:
        index = make_index()
        del index.payload["declaration_count"]
        assert index.to_context().population == 2

    def test_carries_the_grounding_into_the_expansion_table(self) -> None:
        index = make_index()
        index.expansion = {"buffer": [("buf", 0.75, "prefix")]}
        assert index.to_context().expansion.expand("buffer")[0].target == "buf"

    def test_meta_annotations_are_there_without_grounding(self) -> None:
        """Framework relations are facts and come from the framework, not from
        this project's vocabulary."""
        assert make_index().to_context().expansion.expand("@Controller")

    def test_overrides_are_applied(self) -> None:
        from codesense.ql.judge import NullJudge

        judge = NullJudge()
        assert make_index().to_context(judge=judge).judge is judge

    def test_edges_load(self) -> None:
        assert len(make_index().to_context().edges.out_edges(1)) == 1


class TestProject:
    def test_wraps_an_index(self) -> None:
        assert len(Project(make_index())) == 3

    def test_describes_itself(self) -> None:
        assert "demo" in Project(make_index()).describe()

    def test_context_is_built_once(self) -> None:
        """Materialising it costs seconds and hundreds of megabytes on a real
        project."""
        project = Project(make_index())
        assert project.context is project.context

    def test_vocabulary_is_built_once(self) -> None:
        project = Project(make_index())
        assert project.vocabulary is project.vocabulary

    def test_vocabulary_is_bounded_and_filters_singletons(self) -> None:
        project = Project(make_index(), vocab_size=2, vocab_min_df=2)

        assert project.vocabulary == [("alloc", 2)]

    @pytest.mark.parametrize(
        ("options", "message"),
        [({"vocab_size": -1}, "vocab_size"), ({"vocab_min_df": 0}, "vocab_min_df")],
    )
    def test_rejects_invalid_vocabulary_parameters(
        self, options: dict[str, int], message: str
    ) -> None:
        with pytest.raises(ValueError, match=message):
            Project(make_index(), **options)

    def test_open_preserves_vocabulary_parameters(self, tmp_path: Path) -> None:
        make_index().save(tmp_path)

        project = Project.open(tmp_path, vocab_size=1, vocab_min_df=2)

        assert project.vocabulary == [("alloc", 2)]

    def test_no_llm_means_no_judge_injected(self) -> None:
        """The context keeps its NullJudge, so `intent`'s fallback path is
        genuinely exercised."""
        from codesense.ql.judge import NullJudge

        assert isinstance(Project(make_index()).context.judge, NullJudge)

    def test_search_forwards_the_file_target(self) -> None:
        index = make_index()
        index.payload["edges"].extend(
            [
                {
                    "source_id": symbol_id,
                    "target_id": 3,
                    "kind": "in_file",
                    "site": [line, 1],
                    "confidence": 1.0,
                    "provenance": "source_path",
                }
                for symbol_id, line in ((1, 10), (2, 20))
            ]
        )

        result = Project(index).search("pooled", route="lexical", target="file")

        assert result.target == ("file",)
        assert [hit.kind for hit in result.hits] == ["file"]

    def test_open_rejects_a_missing_directory(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            Project.open(tmp_path / "nothing")

    def test_build_rejects_a_non_directory(self, tmp_path: Path) -> None:
        target = tmp_path / "file.txt"
        target.write_text("x")
        with pytest.raises(NotADirectoryError):
            Project.build(target, verbose=False)

    def test_build_grounds_against_declarations_and_records_both_populations(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from codesense.indexing.pipeline import BuildResult, Stats

        result = BuildResult(
            payload=payload(),
            stats=Stats(
                files=2,
                failed=1,
                declarations=2,
                symbols=3,
                postings=3,
                edges=1,
                languages=("java",),
            ),
        )
        seen: dict[str, int] = {}

        monkeypatch.setattr(
            "codesense.indexing.pipeline.build_index", lambda *args, **kwargs: result
        )

        def ground(project_terms: object, total_symbols: int, **kwargs: object) -> SimpleNamespace:
            seen["total_symbols"] = total_symbols
            return SimpleNamespace(table={}, profile="lexical", status="ready", reason="")

        monkeypatch.setattr("codesense.indexing.grounding.ground_vocabulary_result", ground)

        project = Project.build(tmp_path, index_dir=None, verbose=False)

        assert seen["total_symbols"] == 2
        assert project.index.meta.declarations == 2
        assert project.index.meta.files == 1
