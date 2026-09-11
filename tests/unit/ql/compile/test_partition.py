"""Statistical grouping sees canonical terms through project grounding."""

from __future__ import annotations

from codesense.ql import Element, IndexField
from codesense.ql.compile import partition, validate_groups
from codesense.ql.context import EvalContext
from codesense.ql.store import (
    Expansion,
    InMemoryEdgeStore,
    InMemoryExpansionTable,
    InMemoryPostingIndex,
    InMemorySymbolStore,
    Posting,
)


def grounded_context() -> EvalContext:
    return EvalContext(
        symbols=InMemorySymbolStore(
            [
                Element(1, "Buffer", "class", "A.java", (1, 2)),
                Element(2, "Cache", "class", "B.java", (1, 2)),
            ]
        ),
        postings=InMemoryPostingIndex(
            {
                "buf": [Posting(1, IndexField.NAME)],
                "cache": [Posting(2, IndexField.NAME)],
            },
            total_symbols=10,
        ),
        expansion=InMemoryExpansionTable(
            {
                "buffer": [Expansion("buf", 0.9, "prefix")],
                "storage": [Expansion("cache", 0.8, "ctx")],
            }
        ),
        edges=InMemoryEdgeStore(()),
    )


def test_partition_retains_terms_that_only_ground_through_expansion() -> None:
    clusters = partition(["buffer", "storage"], grounded_context())

    assert clusters[0].terms == ("buffer", "storage")


def test_group_validation_uses_grounded_overlap() -> None:
    checked, notes = validate_groups(
        {"memory": ["buffer", "buf"], "cache": ["storage", "cache"]},
        grounded_context(),
    )

    assert checked == {"memory": ["buffer", "buf"], "cache": ["storage", "cache"]}
    assert notes == []
