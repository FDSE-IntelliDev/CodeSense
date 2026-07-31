"""End-to-end tests for annotation queries.

The sample project's Java source is not on this machine (the paths in
`dependency_graph.json` point at another one), so these use a synthetic index
rather than `mini_index.json`. The correctness of extraction itself is covered
by `tests/unit/indexing/test_annotations.py` against inline Java source.
"""

from __future__ import annotations

import pytest

from codesense.indexing import build_expansion_table
from codesense.lang.java import JavaLanguage
from codesense.ql import Element, IndexField
from codesense.ql.context import EvalContext
from codesense.ql.operators import eval_unit, only, top
from codesense.ql.satisfiers import AnnotationSatisfier, LexicalSatisfier
from codesense.ql.store import (
    InMemoryEdgeStore,
    InMemoryPostingIndex,
    InMemorySymbolStore,
    Posting,
)
from codesense.ql.unit import QueryUnit, Term

#: symbol_id -> (name, its annotation-field terms, its annotation_arg-field terms)
FIXTURE = {
    1: ("getUser", ["@GetMapping", "get", "mapping"], ["api", "v1", "users"]),
    2: ("createUser", ["@PostMapping", "post", "mapping"], ["api", "v1", "users"]),
    3: ("evictUserCache", ["@CacheEvict", "cache", "evict"], ["usercache"]),
    4: ("appCached", ["@AppCache", "app", "cache"], []),
    5: ("plainHelper", [], []),
}


@pytest.fixture(scope="module")
def ctx() -> EvalContext:
    postings: dict[str, list[Posting]] = {}
    for symbol_id, (_, annotation_terms, arg_terms) in FIXTURE.items():
        for term in annotation_terms:
            postings.setdefault(term, []).append(Posting(symbol_id, IndexField.ANNOTATION))
        for term in arg_terms:
            postings.setdefault(term, []).append(Posting(symbol_id, IndexField.ANNOTATION_ARG))
    return EvalContext(
        symbols=InMemorySymbolStore(
            [
                Element(symbol_id=sid, name=name, kind="method", file="A.java", span=(sid, sid))
                for sid, (name, _, _) in FIXTURE.items()
            ]
        ),
        # 200 is a "large project" scale, so ICF does not block all these words
        postings=InMemoryPostingIndex(postings, total_symbols=200),
        expansion=build_expansion_table(language=JavaLanguage()),
        edges=InMemoryEdgeStore([]),
    )


def unit(name: str, satisfier: object) -> QueryUnit:
    return QueryUnit(name, satisfiers=(satisfier,))


class TestMetaExpansion:
    """ "All HTTP entry points" -- the query says only `@RequestMapping` and
    must match every kind of Mapping."""

    def test_a_general_annotation_expands_to_the_specific_ones(self, ctx: EvalContext) -> None:
        frag = eval_unit(unit("http", AnnotationSatisfier(names=("@RequestMapping",))), ctx)
        assert {e.name for e in frag} == {"getUser", "createUser"}

    def test_the_evidence_names_the_meta_annotation_relation(self, ctx: EvalContext) -> None:
        frag = eval_unit(unit("http", AnnotationSatisfier(names=("@RequestMapping",))), ctx)
        details = [h.detail for sid in frag.nodes for h in frag.evidence_for(sid).unit_hits]
        assert any("meta" in d for d in details)

    def test_something_without_a_meta_annotation_relation_is_not_pulled_in(
        self, ctx: EvalContext
    ) -> None:
        frag = eval_unit(unit("http", AnnotationSatisfier(names=("@RequestMapping",))), ctx)
        assert "evictUserCache" not in {e.name for e in frag}


class TestUnitMatching:
    """ "Cache-related methods" -- matched on split units, not a literal
    regex."""

    def test_a_project_annotation_and_a_framework_one_match_together(
        self, ctx: EvalContext
    ) -> None:
        """`@AppCache` is the project's own; `@CacheEvict` is Spring's."""
        frag = eval_unit(unit("cache", AnnotationSatisfier(units=(Term("cache"),))), ctx)
        assert {e.name for e in frag} == {"evictUserCache", "appCached"}

    def test_a_method_without_annotations_does_not_match(self, ctx: EvalContext) -> None:
        frag = eval_unit(unit("cache", AnnotationSatisfier(units=(Term("cache"),))), ctx)
        assert "plainHelper" not in {e.name for e in frag}


class TestAnnotationArguments:
    """Information in the arguments -- all of it lost if only names are
    indexed."""

    def test_can_be_queried_by_url_path_segment(self, ctx: EvalContext) -> None:
        frag = eval_unit(unit("users", AnnotationSatisfier(units=(Term("users"),))), ctx)
        assert {e.name for e in frag} == {"getUser", "createUser"}

    def test_the_hit_lands_in_the_annotation_arg_field(self, ctx: EvalContext) -> None:
        frag = eval_unit(unit("users", AnnotationSatisfier(units=(Term("users"),))), ctx)
        fields = {
            h.field for sid in frag.nodes for h in frag.evidence_for(sid).unit_hits if h.field
        }
        assert fields == {"annotation_arg"}

    def test_the_argument_field_weighs_less_than_the_name_field(self, ctx: EvalContext) -> None:
        by_name = eval_unit(unit("u", AnnotationSatisfier(units=(Term("cache"),))), ctx)
        by_arg = eval_unit(unit("u", AnnotationSatisfier(units=(Term("users"),))), ctx)
        best_name = max(by_name.evidence_for(s).scores["u"] for s in by_name.nodes)
        best_arg = max(by_arg.evidence_for(s).scores["u"] for s in by_arg.nodes)
        assert best_name > best_arg


class TestAnnotationBeatsLexical:
    """An annotation is a far stronger signal than lexical matching, and the
    weights should say so."""

    def test_for_the_same_word_an_annotation_hit_scores_above_a_lexical_one(
        self, ctx: EvalContext
    ) -> None:
        annotation = eval_unit(unit("c", AnnotationSatisfier(units=(Term("cache"),))), ctx)
        lexical = eval_unit(unit("c", LexicalSatisfier(terms=(Term("cache"),), weight=0.5)), ctx)
        best_annotation = max(annotation.evidence_for(s).scores["c"] for s in annotation.nodes)
        best_lexical = max(lexical.evidence_for(s).scores["c"] for s in lexical.nodes)
        assert best_annotation > best_lexical

    def test_composes_with_the_narrowing_operators(self, ctx: EvalContext) -> None:
        frag = eval_unit(unit("cache", AnnotationSatisfier(units=(Term("cache"),))), ctx)
        assert len(top(only(frag, kind="method"), 1, by="cache")) == 1
