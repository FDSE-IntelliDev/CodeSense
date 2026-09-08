"""File Elements and their declaration ownership edges."""

from __future__ import annotations

from pathlib import Path

from codesense.indexing import build_index
from codesense.lang import Declaration


class ToyLanguage:
    """A minimal adapter whose non-empty lines are declarations."""

    name = "toy"
    file_globs = ("*.toy",)
    skip_parts: tuple[str, ...] = ()
    indexed_kinds = frozenset({"function"})
    container_kinds: frozenset[str] = frozenset()

    def scan(self, source: str) -> list[Declaration]:
        if source == "parse failure":
            raise ValueError("invalid toy source")
        return [
            Declaration(name=line, kind="function", line=line_number)
            for line_number, line in enumerate(source.splitlines(), 1)
            if line
        ]

    def expansions(self) -> dict:
        return {}


def test_appends_one_file_element_after_declarations(tmp_path: Path) -> None:
    (tmp_path / "b.toy").write_text("run\n", encoding="utf-8")
    (tmp_path / "a.toy").write_text("", encoding="utf-8")

    result = build_index(tmp_path, languages=[ToyLanguage()])
    declarations = [row for row in result.payload["symbols"] if row["kind"] != "file"]
    files = [row for row in result.payload["symbols"] if row["kind"] == "file"]

    assert [row["name"] for row in declarations] == ["run"]
    assert [row["file"] for row in files] == ["a.toy", "b.toy"]
    assert files[0]["span"] == [1, 1]
    assert result.payload["declaration_count"] == 1
    assert result.stats.declarations == 1
    assert result.stats.symbols == 3


def test_every_declaration_points_to_exactly_one_file(tmp_path: Path) -> None:
    (tmp_path / "a.toy").write_text("read\nwrite\n", encoding="utf-8")
    result = build_index(tmp_path, languages=[ToyLanguage()])
    edges = [edge for edge in result.payload["edges"] if edge["kind"] == "in_file"]

    assert len(edges) == 2
    assert {edge["source_id"] for edge in edges} == {1, 2}
    assert len({edge["target_id"] for edge in edges}) == 1
    assert all(edge["site"] is None for edge in edges)
    assert all(edge["confidence"] == 1.0 for edge in edges)
    assert all(edge["provenance"] == "source_path" for edge in edges)


def test_parse_failed_file_has_no_file_element(tmp_path: Path) -> None:
    (tmp_path / "failed.toy").write_text("parse failure", encoding="utf-8")
    (tmp_path / "ok.toy").write_text("run\n", encoding="utf-8")

    result = build_index(tmp_path, languages=[ToyLanguage()])

    assert [row["file"] for row in result.payload["symbols"] if row["kind"] == "file"] == ["ok.toy"]
    assert result.stats.files == 2
    assert result.stats.failed == 1


def test_file_elements_produce_no_postings(tmp_path: Path) -> None:
    (tmp_path / "unique_filename.toy").write_text("run\n", encoding="utf-8")

    result = build_index(tmp_path, languages=[ToyLanguage()])

    assert "unique_filename" not in result.payload["postings"]
