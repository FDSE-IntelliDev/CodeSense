"""Tests for the emitted script.

**Equivalence is the thing that matters most**: the script must produce the
same result as the `Plan` it claims to represent. Once they diverge the
script becomes a plausible-looking fiction -- worse than having none, because
people will reason about the system from it.

Two divergences happened before this test existed: `only(kind=...)` rendered
as a hard filter where the plan weighted a preference, and `Cohere` reranked
inline in the script where the plan only marked the neighbourhood. Both times
the script narrowed harder than the plan.
"""

from __future__ import annotations

from codesense.ql import Edge, Element, IndexField
from codesense.ql.compile import QuerySpec, plan, to_script
from codesense.ql.context import EvalContext
from codesense.ql.store import (
    InMemoryEdgeStore,
    InMemoryExpansionTable,
    InMemoryPostingIndex,
    InMemorySymbolStore,
    Posting,
)

TOTAL = 400


def make_context(*, with_file_target: bool = False) -> EvalContext:
    postings = {
        "rare": [Posting(i, IndexField.NAME) for i in range(1, 30)],
        "common": [Posting(i, IndexField.NAME) for i in range(1, 160)],
        "@Cacheable": [Posting(i, IndexField.ANNOTATION) for i in range(1, 12)],
        "static": [Posting(i, IndexField.MODIFIER) for i in range(1, 8)],
    }
    declarations = [
        Element(
            symbol_id=i,
            name=f"s{i}",
            kind="method" if i % 3 else "class",
            file="A.java",
            span=(1, 2),
        )
        for i in range(1, TOTAL + 1)
    ]
    files = [Element(TOTAL + 1, "A.java", "file", "A.java", (1, 400))] if with_file_target else []
    target_edges = (
        [Edge(i, TOTAL + 1, "in_file") for i in range(1, TOTAL + 1)] if with_file_target else []
    )
    return EvalContext(
        symbols=InMemorySymbolStore([*declarations, *files]),
        postings=InMemoryPostingIndex(postings, total_symbols=TOTAL),
        expansion=InMemoryExpansionTable({}),
        edges=InMemoryEdgeStore(
            [Edge(i, i + 1, "calls") for i in range(1, 120)]
            + [Edge(i, i + 2, "contains") for i in range(1, 60)]
            + target_edges
        ),
        declaration_count=TOTAL,
    )


def make_spec(**overrides: object) -> QuerySpec:
    payload: dict[str, object] = {
        "query": "find cache-related methods",
        "units": [
            {"name": "narrow", "terms": ["rare"], "annotations": ["@Cacheable"]},
            {"name": "wide", "terms": ["common"], "modifiers": ["static"]},
        ],
        "kinds": ["method"],
    }
    payload.update(overrides)
    return QuerySpec.from_dict(payload)


def run_script(source: str, ctx: EvalContext) -> set[int]:
    namespace: dict[str, object] = {"ctx": ctx}
    exec(compile(source, "<generated>", "exec"), namespace)  # noqa: S102
    return set(namespace["answer"].nodes)  # type: ignore[union-attr]


class TestEquivalence:
    """Running the script must give what running the `Plan` gives."""

    def test_the_basic_case(self) -> None:
        ctx = make_context()
        spec = make_spec()
        execution = plan(spec, ctx)
        assert run_script(to_script(execution, spec), ctx) == set(execution.run(ctx).current.nodes)

    def test_with_a_kind_preference(self) -> None:
        """Happened once: `only(kind=...)` rendered as a hard filter where
        the plan weighted a preference."""
        ctx = make_context()
        spec = make_spec(kinds=["class"])
        execution = plan(spec, ctx)
        assert run_script(to_script(execution, spec), ctx) == set(execution.run(ctx).current.nodes)

    def test_with_intent_judging(self) -> None:
        ctx = make_context()
        spec = make_spec(concept="does this do caching")
        execution = plan(spec, ctx)
        assert run_script(to_script(execution, spec), ctx) == set(execution.run(ctx).current.nodes)

    def test_a_single_unit(self) -> None:
        ctx = make_context()
        spec = QuerySpec.from_dict({"query": "q", "units": [{"name": "only", "terms": ["rare"]}]})
        execution = plan(spec, ctx)
        assert run_script(to_script(execution, spec), ctx) == set(execution.run(ctx).current.nodes)

    def test_with_a_file_result_target(self) -> None:
        ctx = make_context(with_file_target=True)
        spec = make_spec(target=["file"])
        execution = plan(spec, ctx)
        source = to_script(execution, spec)

        assert "project(" in source
        assert run_script(source, ctx) == set(execution.run(ctx).current.nodes)


class TestReadability:
    """The script is meant to be read, so its shape is a requirement too."""

    def test_is_valid_python(self) -> None:
        ctx = make_context()
        spec = make_spec()
        compile(to_script(plan(spec, ctx), spec), "<generated>", "exec")

    def test_the_header_records_the_query_and_the_index(self) -> None:
        """The same script gives different results on a different index, and
        without recording it nothing is reproducible."""
        ctx = make_context()
        spec = make_spec()
        source = to_script(plan(spec, ctx), spec, index="netty - 42221 symbols")
        assert "find cache-related methods" in source
        assert "netty - 42221 symbols" in source

    def test_comments_explain_why_the_steps_are_ordered_this_way(self) -> None:
        """Operator semantics live in the design docs; the script only says
        why the steps are ordered this way."""
        ctx = make_context()
        spec = make_spec(concept="does this do caching")
        source = to_script(plan(spec, ctx), spec)
        assert "start with" in source
        assert "intent goes last" in source

    def test_one_variable_per_unit(self) -> None:
        ctx = make_context()
        spec = make_spec()
        source = to_script(plan(spec, ctx), spec)
        assert "narrow = QueryUnit(" in source

    def test_the_result_is_named_answer(self) -> None:
        ctx = make_context()
        spec = make_spec()
        assert "\nanswer = frag" in to_script(plan(spec, ctx), spec)

    def test_relevance_makes_it_into_the_script(self) -> None:
        """Term weights are part of the compilation result, and only what is
        visible can be edited."""
        ctx = make_context()
        spec = QuerySpec.from_dict({"query": "q", "units": [{"name": "u", "terms": ["rare"]}]})
        source = to_script(plan(spec, ctx), spec)
        assert 'Term("rare")' in source
