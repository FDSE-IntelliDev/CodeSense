"""End-to-end Java hierarchy relation indexing and querying."""

from __future__ import annotations

from pathlib import Path

import pytest

from codesense.index import Index, IndexMeta
from codesense.indexing import build_index
from codesense.lang.java import JavaLanguage
from codesense.ql import Frag
from codesense.ql.operators import project, reach


@pytest.mark.slow
def test_java_project_builds_queryable_type_and_method_relations(tmp_path: Path) -> None:
    pytest.importorskip("tree_sitter_languages")
    (tmp_path / "Port.java").write_text(
        "package demo; interface Port { void run(String value); }",
        encoding="utf-8",
    )
    (tmp_path / "Base.java").write_text(
        "package demo; class Base { public void run(String value) {} }",
        encoding="utf-8",
    )
    (tmp_path / "Worker.java").write_text(
        """package demo;
class Worker extends Base implements Port {
    @Override public void run(String value) {}
}
""",
        encoding="utf-8",
    )

    result = build_index(tmp_path, languages=[JavaLanguage()])
    rows = {
        (row["name"], row["kind"], row["container"]): row
        for row in result.payload["symbols"]
        if row["kind"] != "file"
    }
    context = Index(
        IndexMeta("demo", str(tmp_path), "2026-09-20T00:00:00+00:00"),
        result.payload,
    ).to_context()

    port_id = rows[("Port", "interface", "")]["symbol_id"]
    worker_id = rows[("Worker", "class", "")]["symbol_id"]
    base_id = rows[("Base", "class", "")]["symbol_id"]
    worker_run_id = rows[("run", "method", "Worker")]["symbol_id"]
    port_run_id = rows[("run", "method", "Port")]["symbol_id"]
    port = Frag(nodes=context.symbols.get_many((port_id,)))
    worker = Frag(nodes=context.symbols.get_many((worker_id,)))

    implementations = project(
        port,
        context,
        edge="implements",
        direction="backward",
        kind="class",
    )
    method_targets = project(
        Frag(nodes=context.symbols.get_many((worker_run_id,))),
        context,
        edge="implements",
    )

    assert [item.name for item in implementations] == ["Worker"]
    assert set(reach(worker, context, edge="extends", hops=1).nodes) == {base_id}
    assert set(method_targets.nodes) == {port_run_id}
    assert result.stats.relation_counts["extends"] == 1
    assert result.stats.relation_counts["implements"] == 2
    assert result.stats.relation_counts["overrides"] == 1
