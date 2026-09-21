"""Prompt and safe namespace contracts for generated QL."""

from __future__ import annotations

import pytest

from codesense.llm.codegen import OPERATOR_SPEC, PROMPT, ScriptGenerator
from codesense.llm.config import LlmConfig
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
    assert "PageRequest" in OPERATOR_SPEC
    assert "The operator reference includes" not in PROMPT


def test_codegen_prompt_receives_the_runtime_judging_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prompts: list[str] = []
    monkeypatch.setattr(
        ScriptGenerator,
        "_ask",
        lambda self, prompt: prompts.append(prompt) or "answer = top(frag, 20)",
    )
    generator = ScriptGenerator(LlmConfig(api_key="test"))

    generator.generate(
        "find allocators",
        "demo",
        (("alloc", 2),),
        symbols=10,
        edges=20,
        judge_enabled=True,
    )

    assert "Semantic judging: enabled" in prompts[0]
    assert "When judging is enabled" in prompts[0]


def test_codegen_prompt_does_not_receive_the_project_vocabulary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prompts: list[str] = []
    monkeypatch.setattr(
        ScriptGenerator,
        "_ask",
        lambda self, prompt: prompts.append(prompt) or "answer = top(frag, 20)",
    )

    ScriptGenerator(LlmConfig(api_key="test")).generate(
        "find allocators",
        "demo",
        (("never_send_project_vocab", 999),),
        symbols=10,
        edges=20,
    )

    assert "never_send_project_vocab" not in prompts[0]
    assert "vocabulary of this codebase" not in prompts[0]


def test_codegen_prompt_documents_hierarchy_relations_and_direction() -> None:
    spec = OPERATOR_SPEC.lower()

    assert all(edge in spec for edge in ("extends", "implements", "overrides"))
    assert "concrete" in spec
    assert "abstract" in spec
    assert "confidence" in spec


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
