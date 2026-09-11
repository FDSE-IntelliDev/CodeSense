"""Grounding-aware construction of hand-writable query specs."""

from __future__ import annotations

from codesense.ql import Element, IndexField
from codesense.ql.compile import build_spec, infer_fields
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
from codesense.ql.unit import Term


def context(
    postings: dict[str, list[Posting]],
    expansion: dict[str, list[Expansion]] | None = None,
) -> EvalContext:
    symbol_ids = {posting.symbol_id for values in postings.values() for posting in values}
    return EvalContext(
        symbols=InMemorySymbolStore(
            Element(sid, f"symbol{sid}", "class", "A.java", (1, 2)) for sid in symbol_ids
        ),
        postings=InMemoryPostingIndex(postings, total_symbols=max(len(symbol_ids), 10)),
        expansion=InMemoryExpansionTable(expansion or {}),
        edges=InMemoryEdgeStore(()),
    )


def test_build_spec_keeps_grounded_term_metadata_and_notes_partial_misses() -> None:
    ctx = context(
        {"buf": [Posting(1, IndexField.NAME)]},
        {"buffer": [Expansion("buf", 0.9, "prefix")]},
    )
    semantic = Term("buffer", source="derived", weight=0.5, reason="project-related")

    spec, notes = build_spec("buffer latency", [semantic, Term("latency")], ctx)

    satisfier = spec.units[0].satisfiers[0]
    assert isinstance(satisfier, LexicalSatisfier)
    assert satisfier.terms == (semantic,)
    assert spec.kinds == ("class",)
    assert infer_fields(["buffer"], ctx) == (IndexField.NAME,)
    assert notes == ["ignored ungrounded term 'latency'"]


def test_validated_model_group_names_become_query_unit_names() -> None:
    ctx = context(
        {
            "client": [Posting(1, IndexField.NAME)],
            "caller": [Posting(1, IndexField.NAME)],
            "page": [Posting(2, IndexField.NAME)],
            "request": [Posting(2, IndexField.NAME)],
        }
    )

    spec, _ = build_spec(
        "client page",
        [Term("client"), Term("caller"), Term("page"), Term("request")],
        ctx,
        groups={"clients": ["client", "caller"], "pages": ["page", "request"]},
    )

    assert [unit.name for unit in spec.units] == ["clients", "pages"]


def test_build_spec_rejects_only_when_every_term_is_ungrounded() -> None:
    ctx = context({})

    try:
        build_spec("missing", [Term("missing")], ctx)
    except ValueError as exc:
        assert str(exc) == "not one term could be grounded in the index"
    else:
        raise AssertionError("ungrounded query should not produce an empty spec")
