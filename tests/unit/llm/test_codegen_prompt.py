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


def test_codegen_prompt_documents_contains_direction_and_container_kinds() -> None:
    spec = " ".join(OPERATOR_SPEC.lower().split())

    assert "container to its direct member" in spec
    assert "forward projection from a type finds its members" in spec
    assert "backward projection from a member finds its owning type" in spec
    assert 'backward over `contains` with `kind="method"`' in spec
    for kind in ("class", "interface", "record", "enum"):
        assert kind in spec


def test_codegen_prompt_protects_non_empty_anchor_from_empty_intersections() -> None:
    spec = " ".join(OPERATOR_SPEC.lower().split())

    assert "primary anchor" in spec
    assert "never intersect a non-empty fragment with an empty fragment" in spec
    assert "fall back to the primary anchor" in spec


def test_repair_prompt_includes_script_and_compact_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prompts: list[str] = []
    monkeypatch.setattr(
        ScriptGenerator,
        "_ask",
        lambda self, prompt: (
            prompts.append(prompt) or "```python\nanswer = eval_unit(unit, ctx)\n```"
        ),
    )

    repaired = ScriptGenerator(LlmConfig(api_key="test")).repair(
        "find allocators",
        "demo",
        "answer = eval_unit(unit)",
        "line 1: eval_unit() missing a required argument: 'ctx'",
        symbols=10,
        edges=20,
    )

    assert repaired == "answer = eval_unit(unit, ctx)"
    assert "answer = eval_unit(unit)" in prompts[0]
    assert "line 1: eval_unit()" in prompts[0]
    assert "eval_unit(unit, ctx)" in prompts[0]


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
