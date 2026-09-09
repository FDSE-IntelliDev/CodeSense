"""End-to-end acceptance tests for file targets and Java reference edges."""

from __future__ import annotations

from pathlib import Path

import pytest

from codesense import Project
from codesense.llm import ScriptGenerator
from codesense.ql.operators import eval_unit, only
from codesense.ql.operators import project as project_op
from codesense.ql.satisfiers import LexicalSatisfier
from codesense.ql.unit import QueryUnit, Term

pytestmark = pytest.mark.slow
pytest.importorskip("tree_sitter_languages")

QUERY = "Find Java files containing references to the PageRequest class."

REFERENCE_SCRIPT = """\
page_request_unit = QueryUnit(
    "page_request",
    satisfiers=(LexicalSatisfier(terms=(Term("page"), Term("request"))),),
)
page_request = only(eval_unit(page_request_unit, ctx), kind="class")
referencers = project(
    page_request,
    ctx,
    edge="references",
    direction="backward",
)
answer = project(
    referencers,
    ctx,
    edge="in_file",
    kind="file",
    include_self=True,
)
"""

IMPORT_SCRIPT = """\
page_request_unit = QueryUnit(
    "page_request",
    satisfiers=(LexicalSatisfier(terms=(Term("page"), Term("request"))),),
)
page_request = only(eval_unit(page_request_unit, ctx), kind="class")
importers = project(
    page_request,
    ctx,
    edge="imports",
    direction="backward",
)
answer = project(
    importers,
    ctx,
    edge="in_file",
    kind="file",
    include_self=True,
)
"""


@pytest.fixture(scope="module")
def project(tmp_path_factory: pytest.TempPathFactory) -> Project:
    """Build the real Java fixture once for the graph and route assertions."""
    root = tmp_path_factory.mktemp("page-request-project")
    sources = {
        "PageRequest.java": """\
package demo;
public class PageRequest {}
""",
        "Controller.java": """\
package demo;
import demo.PageRequest;
public class Controller {
    public PageRequest list() { return new PageRequest(); }
}
""",
        "Unrelated.java": """\
package demo;
public class Unrelated { public String value() { return "none"; } }
""",
        "Empty.java": "",
    }
    for name, source in sources.items():
        (Path(root) / name).write_text(source, encoding="utf-8")
    return Project.build(root, index_dir=None, llm=object(), verbose=False)


def test_reference_graph_projects_only_the_referencing_file(project: Project) -> None:
    """A broken reference direction or owner projection must fail this test."""
    page_request_unit = QueryUnit(
        "page_request",
        satisfiers=(LexicalSatisfier(terms=(Term("page"), Term("request"))),),
    )
    page_request = only(
        eval_unit(page_request_unit, project.context),
        kind="class",
    )
    referencers = project_op(
        page_request,
        project.context,
        edge="references",
        direction="backward",
    )
    files = project_op(referencers, project.context, edge="in_file", kind="file")

    assert {element.file for element in files} == {"Controller.java"}


def test_codegen_reference_query_returns_only_the_referencing_file(
    project: Project,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The codegen route must execute the relation script without lexical fallback."""
    monkeypatch.setattr(ScriptGenerator, "generate", lambda *_args, **_kwargs: REFERENCE_SCRIPT)

    result = project.search(QUERY, route="codegen", target="file")

    assert result.route == "codegen"
    assert [hit.file for hit in result.hits] == ["Controller.java"]
    assert all(hit.kind == "file" for hit in result.hits)
    assert "PageRequest.java" not in {hit.file for hit in result.hits}


def test_codegen_import_query_returns_only_the_importing_file(
    project: Project,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The imports edge must select the file that owns the import statement."""
    monkeypatch.setattr(ScriptGenerator, "generate", lambda *_args, **_kwargs: IMPORT_SCRIPT)

    result = project.search(QUERY, route="codegen", target="file")

    assert result.route == "codegen"
    assert [hit.file for hit in result.hits] == ["Controller.java"]
    assert all(hit.kind == "file" for hit in result.hits)
    assert "PageRequest.java" not in {hit.file for hit in result.hits}
