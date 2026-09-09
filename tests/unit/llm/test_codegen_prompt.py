"""Prompt and safe namespace contracts for generated QL."""

from __future__ import annotations

from codesense.llm.codegen import OPERATOR_SPEC, PROMPT
from codesense.ql import run_script
from codesense.ql.context import EvalContext
from codesense.ql.store import (
    InMemoryEdgeStore,
    InMemoryExpansionTable,
    InMemoryPostingIndex,
    InMemorySymbolStore,
)
from codesense.search import _namespace


def test_codegen_prompt_documents_project_and_reference_edges() -> None:
    for term in ("project", "references", "imports", "in_file"):
        assert term in OPERATOR_SPEC
        assert term in PROMPT or term == "project"
    assert "PageRequest" in OPERATOR_SPEC


def test_project_is_available_to_safe_generated_scripts() -> None:
    ctx = EvalContext(
        symbols=InMemorySymbolStore(()),
        postings=InMemoryPostingIndex({}, total_symbols=0),
        expansion=InMemoryExpansionTable({}),
        edges=InMemoryEdgeStore(()),
    )
    script = (
        'unit = QueryUnit("q", satisfiers=(LexicalSatisfier(terms=(Term("x"),)),))\n'
        "answer = project(eval_unit(unit, ctx), ctx)"
    )

    # The script validator derives top-level call permissions from this one
    # injected namespace; no parallel whitelist should be necessary.
    result = run_script(script, _namespace(ctx))

    assert result.nodes == {}
