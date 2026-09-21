"""Exact-first grounding shared by compilation and execution."""

from __future__ import annotations

from codesense.ql import IndexField
from codesense.ql.context import EvalContext
from codesense.ql.store import (
    Expansion,
    InMemoryEdgeStore,
    InMemoryExpansionTable,
    InMemoryPostingIndex,
    InMemorySymbolStore,
    Posting,
)
from codesense.ql.term_resolution import (
    TermResolver,
    resolved_postings,
    resolved_surfaces,
    resolved_symbol_ids,
)


def context(
    postings: dict[str, list[Posting]],
    expansion: dict[str, list[Expansion]],
) -> EvalContext:
    return EvalContext(
        symbols=InMemorySymbolStore(()),
        postings=InMemoryPostingIndex(postings, total_symbols=10),
        expansion=InMemoryExpansionTable(expansion),
        edges=InMemoryEdgeStore(()),
    )


def test_out_of_vocabulary_term_resolves_to_a_project_surface() -> None:
    ctx = context(
        {"buf": [Posting(1, IndexField.NAME)]},
        {"buffer": [Expansion("buf", 0.9, "prefix")]},
    )

    assert [surface.target for surface in resolved_surfaces("buffer", ctx)] == ["buf"]
    assert resolved_symbol_ids("buffer", ctx) == frozenset({1})


def test_query_term_is_casefolded_before_exact_lookup() -> None:
    ctx = context(
        {"yamllines": [Posting(1, IndexField.NAME)]},
        {},
    )

    assert [surface.target for surface in resolved_surfaces("YamlLines", ctx)] == ["yamllines"]
    assert resolved_symbol_ids("YAMLLINES", ctx) == frozenset({1})


def test_exact_surface_is_first_and_wins_duplicate_targets() -> None:
    ctx = context(
        {
            "buffer": [Posting(1, IndexField.NAME)],
            "buf": [Posting(2, IndexField.NAME)],
        },
        {
            "buffer": [
                Expansion("buffer", 0.99, "prefix"),
                Expansion("buf", 0.8, "prefix"),
                Expansion("buf", 0.7, "subseq"),
            ]
        },
    )

    surfaces = resolved_surfaces("buffer", ctx)

    assert [(item.target, item.reason) for item in surfaces] == [
        ("buffer", "exact"),
        ("buf", "prefix"),
    ]


def test_missing_expansion_targets_are_ignored_in_stable_order() -> None:
    ctx = context(
        {
            "buf": [Posting(2, IndexField.NAME)],
            "buffered": [Posting(3, IndexField.DOC)],
        },
        {
            "buffer": [
                Expansion("missing", 0.99, "ctx"),
                Expansion("buf", 0.9, "prefix"),
                Expansion("buffered", 0.8, "subseq"),
            ]
        },
    )

    assert [item.target for item in resolved_surfaces("buffer", ctx)] == ["buf", "buffered"]


def test_postings_are_deduplicated_by_symbol_and_field() -> None:
    duplicate = Posting(1, IndexField.NAME)
    ctx = context(
        {
            "buffer": [duplicate],
            "buf": [duplicate, duplicate, Posting(1, IndexField.DOC)],
        },
        {"buffer": [Expansion("buf", 0.9, "prefix")]},
    )

    assert resolved_postings("buffer", ctx) == (
        Posting(1, IndexField.NAME),
        Posting(1, IndexField.DOC),
    )


def test_one_resolver_reuses_query_local_results() -> None:
    ctx = context(
        {"buf": [Posting(1, IndexField.NAME)]},
        {"buffer": [Expansion("buf", 0.9, "prefix")]},
    )
    resolver = TermResolver(ctx)

    first = resolver.postings("buffer")
    second = resolved_postings("buffer", ctx, resolver=resolver)

    assert first is second
