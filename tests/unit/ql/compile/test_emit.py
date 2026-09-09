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

import pytest

from codesense.ql import Edge, Element, IndexField
from codesense.ql.compile import Boost, Cohere, EvalUnit, Narrow, Plan, QuerySpec, plan, to_script
from codesense.ql.context import EvalContext
from codesense.ql.satisfiers import LexicalSatisfier
from codesense.ql.store import (
    InMemoryEdgeStore,
    InMemoryExpansionTable,
    InMemoryPostingIndex,
    InMemorySymbolStore,
    Posting,
)
from codesense.ql.unit import QueryUnit, Term

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


def relation_context(edges: tuple[Edge, ...]) -> EvalContext:
    """Small graph whose scores make losing a boost change the top result."""
    elements = [
        Element(1, "source", "method", "Source.java", (1, 2)),
        Element(2, "related", "method", "Related.java", (1, 2)),
        Element(3, "also-related", "method", "Other.java", (1, 2)),
        Element(10, "Source.java", "file", "Source.java", (1, 4)),
        Element(11, "Related.java", "file", "Related.java", (1, 4)),
    ]
    return EvalContext(
        symbols=InMemorySymbolStore(elements),
        postings=InMemoryPostingIndex(
            {
                "source": [Posting(1, IndexField.NAME)],
                "related": [Posting(2, IndexField.NAME)],
                "also-related": [Posting(3, IndexField.NAME)],
            },
            total_symbols=3,
        ),
        expansion=InMemoryExpansionTable({}),
        edges=InMemoryEdgeStore(edges),
        declaration_count=3,
    )


def weighted_unit(name: str, term: str, weight: float) -> QueryUnit:
    return QueryUnit(
        name,
        satisfiers=(LexicalSatisfier(terms=(Term(term),), weight=weight),),
    )


def relation_plan(
    edge: tuple[str, ...],
    *,
    cohere_edge: tuple[str, ...] = ("calls", "contains"),
) -> tuple[Plan, QuerySpec]:
    """Plan where node 1 wins lexically but boosted node 2 wins overall."""
    src = weighted_unit("src", "source", 1.0)
    dst = weighted_unit("dst", "related", 0.7)
    execution = Plan(
        steps=(
            EvalUnit(src, seed=True),
            EvalUnit(dst),
            Boost("src", "dst", edge=edge, hops=(1, 1)),
            Cohere(seeds=1, edge=cohere_edge, hops=(1, 1)),
            Narrow(limit=1),
        )
    )
    return execution, QuerySpec(query="relation", units=(src, dst), limit=1)


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

    def test_without_graph_or_coherence(self) -> None:
        ctx = relation_context(())
        unit = weighted_unit("only", "source", 1.0)
        execution = Plan(steps=(EvalUnit(unit, seed=True), Narrow(limit=1)))
        spec = QuerySpec(query="plain", units=(unit,), limit=1)

        assert run_script(to_script(execution, spec), ctx) == set(execution.run(ctx).current.nodes)

    def test_unit_named_like_boost_state_does_not_overwrite_its_definition(self) -> None:
        ctx = relation_context(())
        unit = weighted_unit("boosted", "source", 1.0)
        execution = Plan(steps=(EvalUnit(unit, seed=True), Narrow(limit=1)))
        spec = QuerySpec(query="reserved name", units=(unit,), limit=1)
        source = to_script(execution, spec)

        assert "boosted_2 = QueryUnit(" in source
        assert run_script(source, ctx) == set(execution.run(ctx).current.nodes)

    def test_unit_and_match_cache_names_cannot_overwrite_each_other(self) -> None:
        ctx = relation_context(())
        first = weighted_unit("a", "source", 1.0)
        second = weighted_unit("a_matches", "related", 0.7)
        execution = Plan(
            steps=(
                EvalUnit(first, seed=True),
                EvalUnit(second),
                Narrow(limit=2),
            )
        )
        spec = QuerySpec(query="cross-group collision", units=(first, second), limit=2)
        source = to_script(execution, spec)

        assert "a_matches_2 = eval_unit(a, ctx)" in source
        assert "a_matches_matches = eval_unit(a_matches, ctx)" in source
        assert run_script(source, ctx) == set(execution.run(ctx).current.nodes)

    @pytest.mark.parametrize("name", ("ctx", "eval_unit", "class", "set"))
    def test_unit_identifiers_avoid_runtime_import_keyword_and_builtin_names(
        self, name: str
    ) -> None:
        ctx = relation_context(())
        unit = weighted_unit(name, "source", 1.0)
        execution = Plan(steps=(EvalUnit(unit, seed=True), Narrow(limit=1)))
        spec = QuerySpec(query="reserved identifier", units=(unit,), limit=1)

        assert run_script(to_script(execution, spec), ctx) == set(execution.run(ctx).current.nodes)

    def test_with_a_file_result_target(self) -> None:
        ctx = make_context(with_file_target=True)
        spec = make_spec(target=["file"])
        execution = plan(spec, ctx)
        source = to_script(execution, spec)

        assert "project(" in source
        assert run_script(source, ctx) == set(execution.run(ctx).current.nodes)

    @pytest.mark.parametrize(
        ("edge", "edges"),
        [
            (("references",), (Edge(1, 2, "references"),)),
            (
                ("references",),
                (Edge(1, 10, "in_file"), Edge(10, 2, "references")),
            ),
            (("imports",), (Edge(1, 10, "in_file"), Edge(10, 2, "imports"))),
            (
                ("in_file",),
                (Edge(1, 11, "in_file"), Edge(2, 11, "in_file")),
            ),
        ],
        ids=("declaration-reference", "file-reference", "imports", "in-file"),
    )
    def test_typed_relation_boosts_match_the_plan(
        self,
        edge: tuple[str, ...],
        edges: tuple[Edge, ...],
    ) -> None:
        ctx = relation_context(edges)
        execution, spec = relation_plan(edge)
        planned = set(execution.run(ctx).current.nodes)

        assert planned == {2}
        assert run_script(to_script(execution, spec), ctx) == planned

    def test_mixed_relation_endpoint_roles_union_before_narrow(self) -> None:
        ctx = relation_context(
            (
                Edge(1, 10, "in_file"),
                Edge(1, 2, "calls"),
                Edge(10, 3, "imports"),
            )
        )
        src = weighted_unit("src", "source", 1.0)
        dst = QueryUnit(
            "dst",
            satisfiers=(
                LexicalSatisfier(
                    terms=(Term("related"), Term("also-related")),
                    weight=0.7,
                ),
            ),
        )
        execution = Plan(
            steps=(
                EvalUnit(src, seed=True),
                EvalUnit(dst),
                Boost("src", "dst", edge=("calls", "imports"), hops=(1, 1)),
                Cohere(seeds=1, hops=(1, 1)),
                Narrow(limit=2),
            )
        )
        spec = QuerySpec(query="mixed", units=(src, dst), limit=2)
        planned = set(execution.run(ctx).current.nodes)

        assert planned == {2, 3}
        assert run_script(to_script(execution, spec), ctx) == planned

    def test_multiple_boosts_accumulate(self) -> None:
        ctx = relation_context(
            (
                Edge(1, 2, "references"),
                Edge(1, 10, "in_file"),
                Edge(10, 3, "imports"),
            )
        )
        src = weighted_unit("src", "source", 1.0)
        first = weighted_unit("first", "related", 0.7)
        second = weighted_unit("second", "also-related", 0.65)
        execution = Plan(
            steps=(
                EvalUnit(src, seed=True),
                EvalUnit(first),
                EvalUnit(second),
                Boost("src", "first", edge=("references",), hops=(1, 1)),
                Boost("src", "second", edge=("imports",), hops=(1, 1)),
                Cohere(seeds=1, hops=(1, 1)),
                Narrow(limit=2),
            )
        )
        spec = QuerySpec(query="two constraints", units=(src, first, second), limit=2)
        planned = set(execution.run(ctx).current.nodes)

        assert planned == {2, 3}
        assert run_script(to_script(execution, spec), ctx) == planned

    def test_cohere_unions_with_an_earlier_boost(self) -> None:
        ctx = relation_context((Edge(1, 2, "references"), Edge(1, 3, "calls")))
        src = weighted_unit("src", "source", 1.0)
        first = weighted_unit("dst", "related", 0.7)
        second = weighted_unit("coherent", "also-related", 0.65)
        execution = Plan(
            steps=(
                EvalUnit(src, seed=True),
                EvalUnit(first),
                EvalUnit(second),
                Boost("src", "dst", edge=("references",), hops=(1, 1)),
                Cohere(seeds=1, edge=("calls",), hops=(1, 1)),
                Narrow(limit=2),
            )
        )
        spec = QuerySpec(query="constraint and coherence", units=(src, first, second), limit=2)
        planned = set(execution.run(ctx).current.nodes)

        assert planned == {2, 3}
        assert run_script(to_script(execution, spec), ctx) == planned


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

    def test_boost_state_is_initialized_once_and_only_union_updated(self) -> None:
        src = weighted_unit("src", "source", 1.0)
        first = weighted_unit("first", "related", 0.7)
        second = weighted_unit("second", "also-related", 0.65)
        execution = Plan(
            steps=(
                EvalUnit(src, seed=True),
                EvalUnit(first),
                EvalUnit(second),
                Boost("src", "first", edge=("references",), hops=(1, 1)),
                Boost("src", "second", edge=("calls",), hops=(1, 1)),
                Cohere(seeds=1, edge=("calls",), hops=(1, 1)),
                Narrow(limit=2),
            )
        )
        source = to_script(
            execution,
            QuerySpec(query="inspectable boosts", units=(src, first, second), limit=2),
        )

        assert source.count("boosted = set()") == 1
        assert source.count("boosted |=") == 3
        assert "relation_destinations(" in source
        assert "boosted = set(near.nodes)" not in source
