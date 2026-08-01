"""Unit tests for the on-disk index and the `Project` facade.

No tree-sitter and no network: the payload is built by hand, which is also the
point of `Index` taking one rather than a repository path.
"""

from __future__ import annotations

import json
from pathlib import Path

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
                "confidence": 1.0,
                "provenance": "derived_container",
            }
        ],
    }


def make_index(**meta: object) -> Index:
    base = {"project": "demo", "root": "/src/demo", "built_at": "2026-01-01T00:00:00+00:00"}
    return Index(meta=IndexMeta(**{**base, "symbols": 2, "edges": 1, **meta}), payload=payload())


class TestRoundTrip:
    def test_saves_and_reloads(self, tmp_path: Path) -> None:
        make_index().save(tmp_path)
        assert len(Index.load(tmp_path)) == 2

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


class TestLoadFailures:
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
        assert ctx.symbols.count() == 2
        assert len(ctx.postings.lookup("alloc")) == 2

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
        assert len(Project(make_index())) == 2

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

    def test_no_llm_means_no_judge_injected(self) -> None:
        """The context keeps its NullJudge, so `intent`'s fallback path is
        genuinely exercised."""
        from codesense.ql.judge import NullJudge

        assert isinstance(Project(make_index()).context.judge, NullJudge)

    def test_open_rejects_a_missing_directory(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            Project.open(tmp_path / "nothing")

    def test_build_rejects_a_non_directory(self, tmp_path: Path) -> None:
        target = tmp_path / "file.txt"
        target.write_text("x")
        with pytest.raises(NotADirectoryError):
            Project.build(target, verbose=False)
