"""Edge-scoped semantic relation validation tests."""

from __future__ import annotations

from collections.abc import Sequence

from codesense.ql import Edge, Element, IndexField
from codesense.ql.compile import ScoredTerm, build_spec
from codesense.ql.compile.validate import LIFT_FLOOR, relation_lift, validate_relations
from codesense.ql.context import EvalContext
from codesense.ql.store import (
    InMemoryEdgeStore,
    InMemoryExpansionTable,
    InMemoryPostingIndex,
    InMemorySymbolStore,
    Posting,
)


def make_context(edges: Sequence[Edge]) -> EvalContext:
    elements = [
        Element(symbol_id=1, name="client", kind="class", file="Client.java", span=(1, 4)),
        Element(symbol_id=2, name="page", kind="class", file="Page.java", span=(1, 4)),
    ]
    postings = {
        "client": [Posting(1, IndexField.NAME)],
        "client_alias": [Posting(1, IndexField.NAME)],
        "page": [Posting(2, IndexField.NAME)],
        "page_alias": [Posting(2, IndexField.NAME)],
    }
    return EvalContext(
        symbols=InMemorySymbolStore(elements),
        postings=InMemoryPostingIndex(postings, total_symbols=2),
        expansion=InMemoryExpansionTable({}),
        edges=InMemoryEdgeStore(edges),
    )


def test_relation_lift_counts_only_the_proposed_edge_kinds() -> None:
    calls_only = make_context((Edge(1, 2, "calls"),))
    references_only = make_context((Edge(1, 2, "references"),))

    assert relation_lift(["client"], ["page"], calls_only, edge=("references",))[0] == 0.0
    assert relation_lift(["client"], ["page"], calls_only, edge=("calls",))[0] >= LIFT_FLOOR
    assert (
        relation_lift(["client"], ["page"], references_only, edge=("references",))[0] >= LIFT_FLOOR
    )


def test_accepted_relation_retains_edge_and_build_spec_copies_it() -> None:
    ctx = make_context((Edge(1, 2, "references"),))
    accepted, rejected = validate_relations(
        [("clients", "pages", ("references",))],
        {"clients": ["client"], "pages": ["page"]},
        ctx,
    )

    assert not rejected
    assert accepted[0].edge == ("references",)

    spec, _ = build_spec(
        "client references page",
        [ScoredTerm("client"), ScoredTerm("page")],
        ctx,
        groups={
            "clients": ["client", "client_alias"],
            "pages": ["page", "page_alias"],
        },
        relations=[("clients", "pages", ("references",))],
    )

    assert spec.graph[0].edge == ("references",)
