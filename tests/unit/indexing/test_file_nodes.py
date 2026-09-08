"""File Elements and their declaration ownership edges."""

from __future__ import annotations

from pathlib import Path

from codesense.indexing import build_index
from codesense.lang import Declaration, ReferenceUse, ScanResult
from codesense.ql import Edge, Element, Frag
from codesense.ql.context import EvalContext
from codesense.ql.operators import project
from codesense.ql.store import (
    InMemoryEdgeStore,
    InMemoryExpansionTable,
    InMemoryPostingIndex,
    InMemorySymbolStore,
)


class ToyLanguage:
    """A minimal adapter whose non-empty lines are declarations."""

    name = "toy"
    file_globs = ("*.toy",)
    skip_parts: tuple[str, ...] = ()
    indexed_kinds = frozenset({"function"})
    container_kinds: frozenset[str] = frozenset()

    def scan(self, source: str) -> ScanResult:
        if source == "parse failure":
            raise ValueError("invalid toy source")
        return ScanResult(
            declarations=tuple(
                Declaration(name=line, kind="function", line=line_number)
                for line_number, line in enumerate(source.splitlines(), 1)
                if line
            )
        )

    def expansions(self) -> dict:
        return {}


class ReferenceLanguage(ToyLanguage):
    """A two-file adapter exposing both declaration and reference facts."""

    indexed_kinds = frozenset({"class", "method"})
    container_kinds = frozenset({"class"})

    def scan(self, source: str) -> ScanResult:
        if source == "target":
            return ScanResult((Declaration("PageRequest", "class", line=1, end_line=2),))
        return ScanResult(
            declarations=(Declaration("run", "method", line=2, end_line=4),),
            references=(
                ReferenceUse(
                    "PageRequest",
                    line=1,
                    column=7,
                    relation="imports",
                    qualified_name="org.example.PageRequest",
                ),
                ReferenceUse("PageRequest", line=3, column=8),
            ),
        )


def context_from_payload(payload: dict) -> EvalContext:
    """Construct the public operator context from an in-memory build payload."""
    symbols = tuple(
        Element(
            symbol_id=row["symbol_id"],
            name=row["name"],
            kind=row["kind"],
            file=row["file"],
            span=tuple(row["span"]),
            signature=row["signature"],
            container=row["container"],
            doc=row["doc"],
            language=row["language"],
            modifiers=frozenset(row["modifiers"]),
        )
        for row in payload["symbols"]
    )
    edges = tuple(
        Edge(
            source_id=row["source_id"],
            target_id=row["target_id"],
            kind=row["kind"],
            site=tuple(row["site"]) if row.get("site") is not None else None,
            confidence=row["confidence"],
            provenance=row["provenance"],
        )
        for row in payload["edges"]
    )
    return EvalContext(
        symbols=InMemorySymbolStore(symbols),
        postings=InMemoryPostingIndex({}, total_symbols=payload["declaration_count"]),
        expansion=InMemoryExpansionTable({}),
        edges=InMemoryEdgeStore(edges),
        declaration_count=payload["declaration_count"],
    )


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


def test_pipeline_materializes_reference_edges_and_aggregates_stats(tmp_path: Path) -> None:
    (tmp_path / "client.toy").write_text("client", encoding="utf-8")
    (tmp_path / "target.toy").write_text("target", encoding="utf-8")

    result = build_index(tmp_path, languages=[ReferenceLanguage()])
    rows = {row["name"]: row for row in result.payload["symbols"]}
    edge_keys = {
        (row["source_id"], row["target_id"], row["kind"]) for row in result.payload["edges"]
    }

    assert edge_keys >= {
        (rows["run"]["symbol_id"], rows["PageRequest"]["symbol_id"], "references"),
        (rows["client.toy"]["symbol_id"], rows["PageRequest"]["symbol_id"], "imports"),
        (rows["client.toy"]["symbol_id"], rows["PageRequest"]["symbol_id"], "references"),
    }
    assert result.stats.references == 2
    assert result.stats.imports == 1
    assert result.stats.reference_ambiguous == 0
    assert result.stats.reference_unresolved == 0


def test_backward_project_reaches_pipeline_recorded_reference_owners(tmp_path: Path) -> None:
    (tmp_path / "client.toy").write_text("client", encoding="utf-8")
    (tmp_path / "target.toy").write_text("target", encoding="utf-8")
    result = build_index(tmp_path, languages=[ReferenceLanguage()])
    context = context_from_payload(result.payload)
    rows = {row["name"]: row for row in result.payload["symbols"]}
    target_id = rows["PageRequest"]["symbol_id"]
    target = Frag(nodes=context.symbols.get_many((target_id,)))

    owners = project(target, context, edge="references", direction="backward")

    assert set(owners.nodes) == {
        rows["run"]["symbol_id"],
        rows["client.toy"]["symbol_id"],
    }
