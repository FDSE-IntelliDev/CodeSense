"""Cost estimates use the same grounded postings as execution."""

from __future__ import annotations

from codesense.ql import Element, IndexField
from codesense.ql.compile import estimate_unit
from codesense.ql.context import EvalContext
from codesense.ql.satisfiers import LexicalSatisfier
from codesense.ql.store import (
    Expansion,
    InMemoryEdgeStore,
    InMemoryExpansionTable,
    InMemoryPostingIndex,
    InMemorySymbolStore,
    Posting,
)
from codesense.ql.unit import QueryUnit, Term


def test_grounded_term_has_the_same_estimate_as_the_project_spelling() -> None:
    ctx = EvalContext(
        symbols=InMemorySymbolStore(
            Element(sid, f"s{sid}", "method", "A.java", (1, 2)) for sid in range(1, 11)
        ),
        postings=InMemoryPostingIndex(
            {"buf": [Posting(1, IndexField.NAME), Posting(2, IndexField.NAME)]},
            total_symbols=10,
        ),
        expansion=InMemoryExpansionTable({"buffer": [Expansion("buf", 0.9, "prefix")]}),
        edges=InMemoryEdgeStore(()),
    )
    canonical = QueryUnit("canonical", satisfiers=(LexicalSatisfier(terms=(Term("buffer"),)),))
    project_term = QueryUnit("project", satisfiers=(LexicalSatisfier(terms=(Term("buf"),)),))

    assert estimate_unit(canonical, ctx).rows == estimate_unit(project_term, ctx).rows == 2
    assert estimate_unit(canonical, ctx).cost == 2


def test_exact_and_expansion_overlap_is_counted_once() -> None:
    ctx = EvalContext(
        symbols=InMemorySymbolStore(
            Element(sid, f"s{sid}", "method", "A.java", (1, 2)) for sid in range(1, 11)
        ),
        postings=InMemoryPostingIndex(
            {"buffer": [Posting(1, IndexField.NAME), Posting(2, IndexField.NAME)]},
            total_symbols=10,
        ),
        expansion=InMemoryExpansionTable({"buffer": [Expansion("buffer", 0.9, "prefix")]}),
        edges=InMemoryEdgeStore(()),
    )
    unit = QueryUnit("buffer", satisfiers=(LexicalSatisfier(terms=(Term("buffer"),)),))

    estimate = estimate_unit(unit, ctx)

    assert estimate.rows == 2
    assert estimate.cost == 2
