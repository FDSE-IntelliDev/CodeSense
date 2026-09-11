"""Edge-scoped semantic relation validation tests."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from codesense.ql import Edge, Element, IndexField
from codesense.ql.compile import ResultRelation, ScoredTerm, build_spec
from codesense.ql.compile.validate import LIFT_FLOOR, relation_lift, validate_relations
from codesense.ql.context import EvalContext
from codesense.ql.store import (
    Expansion,
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


def make_file_endpoint_context(
    edges: Sequence[Edge], *, include_ownership: bool = True
) -> EvalContext:
    """A posting-only declaration index with physical file ownership."""
    elements = [
        Element(symbol_id=1, name="client", kind="class", file="Client.java", span=(1, 4)),
        Element(symbol_id=2, name="page", kind="class", file="Page.java", span=(1, 4)),
        Element(symbol_id=3, name="other", kind="class", file="Other.java", span=(1, 4)),
        Element(symbol_id=10, name="Client.java", kind="file", file="Client.java", span=(1, 8)),
        Element(symbol_id=11, name="Page.java", kind="file", file="Page.java", span=(1, 8)),
        Element(symbol_id=12, name="Other.java", kind="file", file="Other.java", span=(1, 8)),
    ]
    postings = {
        "client": [Posting(1, IndexField.NAME)],
        "client_alias": [Posting(1, IndexField.NAME)],
        "owner": [Posting(1, IndexField.NAME)],
        "owner_alias": [Posting(1, IndexField.NAME)],
        "page": [Posting(2, IndexField.NAME)],
        "page_alias": [Posting(2, IndexField.NAME)],
        "other": [Posting(3, IndexField.NAME)],
    }
    ownership = (
        (
            Edge(1, 10, "in_file"),
            Edge(2, 11, "in_file"),
            Edge(3, 12, "in_file"),
        )
        if include_ownership
        else ()
    )
    return EvalContext(
        symbols=InMemorySymbolStore(elements),
        postings=InMemoryPostingIndex(postings, total_symbols=3),
        expansion=InMemoryExpansionTable({}),
        edges=InMemoryEdgeStore((*ownership, *edges)),
        declaration_count=3,
    )


def make_large_file_endpoint_context(size: int, edge: str) -> EvalContext:
    """Build homogeneous file-source strata around the sampling boundary."""
    target_id = size + 1
    file_offset = size + 1
    source_ids = range(1, size + 1)
    elements = [
        *(
            Element(
                symbol_id=symbol_id,
                name=f"client{symbol_id}",
                kind="class",
                file=f"Client{symbol_id}.java",
                span=(1, 4),
            )
            for symbol_id in source_ids
        ),
        Element(
            symbol_id=target_id,
            name="page",
            kind="class",
            file="Page.java",
            span=(1, 4),
        ),
        *(
            Element(
                symbol_id=file_offset + symbol_id,
                name=f"Client{symbol_id}.java",
                kind="file",
                file=f"Client{symbol_id}.java",
                span=(1, 8),
            )
            for symbol_id in source_ids
        ),
        Element(
            symbol_id=file_offset + target_id,
            name="Page.java",
            kind="file",
            file="Page.java",
            span=(1, 8),
        ),
    ]
    ownership = tuple(
        Edge(symbol_id, file_offset + symbol_id, "in_file") for symbol_id in source_ids
    ) + (Edge(target_id, file_offset + target_id, "in_file"),)
    relation_edges = tuple(
        Edge(file_offset + symbol_id, target_id, edge) for symbol_id in source_ids
    )
    return EvalContext(
        symbols=InMemorySymbolStore(elements),
        postings=InMemoryPostingIndex(
            {
                "clients": [Posting(symbol_id, IndexField.NAME) for symbol_id in source_ids],
                "page": [Posting(target_id, IndexField.NAME)],
            },
            total_symbols=size + 1,
        ),
        expansion=InMemoryExpansionTable({}),
        edges=InMemoryEdgeStore((*ownership, *relation_edges)),
        declaration_count=size + 1,
    )


def test_relation_lift_counts_only_the_proposed_edge_kinds() -> None:
    calls_only = make_context((Edge(1, 2, "calls"),))
    references_only = make_context((Edge(1, 2, "references"),))

    assert relation_lift(["client"], ["page"], calls_only, edge=("references",))[0] == 0.0
    assert relation_lift(["client"], ["page"], calls_only, edge=("calls",))[0] >= LIFT_FLOOR
    assert (
        relation_lift(["client"], ["page"], references_only, edge=("references",))[0] >= LIFT_FLOOR
    )


def test_relation_lift_resolves_canonical_terms_before_counting_edges() -> None:
    base = make_context((Edge(1, 2, "references"),))
    ctx = EvalContext(
        symbols=base.symbols,
        postings=base.postings,
        expansion=InMemoryExpansionTable(
            {
                "caller": [Expansion("client", 0.9, "ctx")],
                "pagination": [Expansion("page", 0.9, "ctx")],
            }
        ),
        edges=base.edges,
    )

    assert relation_lift(["caller"], ["pagination"], ctx, edge=("references",)) == relation_lift(
        ["client"], ["page"], ctx, edge=("references",)
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


def test_build_spec_keeps_a_result_bound_relation_out_of_graph_boosts() -> None:
    ctx = make_context(())

    spec, _ = build_spec(
        "things referencing page",
        [ScoredTerm("client"), ScoredTerm("page")],
        ctx,
        groups={
            "clients": ["client", "client_alias"],
            "pages": ["page", "page_alias"],
        },
        result_relation=ResultRelation(unit="pages", result_side="source", edge=("references",)),
    )

    assert spec.graph == ()
    assert spec.result_relation == ResultRelation(
        unit="pages", result_side="source", edge=("references",)
    )


def test_result_relation_anchor_tracks_a_group_folded_into_another_unit() -> None:
    ctx = make_context(())

    spec, _ = build_spec(
        "things referencing page",
        [ScoredTerm("client"), ScoredTerm("client_alias"), ScoredTerm("page")],
        ctx,
        groups={"clients": ["client", "client_alias"], "pages": ["page"]},
        result_relation=ResultRelation(unit="pages", result_side="source", edge=("references",)),
    )

    assert [unit.name for unit in spec.units] == ["clients"]
    assert spec.result_relation is not None
    assert spec.result_relation.unit == "clients"


def test_result_relation_rejects_an_ungrounded_anchor_group() -> None:
    ctx = make_context(())

    with pytest.raises(ValueError, match="result relation anchor"):
        build_spec(
            "things referencing missing",
            [ScoredTerm("client"), ScoredTerm("missing")],
            ctx,
            groups={"clients": ["client"], "missing": ["missing"]},
            result_relation=ResultRelation(
                unit="missing", result_side="source", edge=("references",)
            ),
        )


def test_imports_projects_the_source_declaration_group_to_owning_files() -> None:
    ctx = make_file_endpoint_context((Edge(10, 2, "imports"),))

    accepted, rejected = validate_relations(
        [("clients", "pages", ("imports",))],
        {"clients": ["client"], "pages": ["page"]},
        ctx,
    )

    assert not rejected
    assert accepted[0].edge == ("imports",)
    assert accepted[0].lift == pytest.approx(9.0)


def test_import_derived_reference_includes_owning_files_as_sources() -> None:
    ctx = make_file_endpoint_context((Edge(10, 2, "references"),))

    accepted, rejected = validate_relations(
        [("clients", "pages", ("references",))],
        {"clients": ["client"], "pages": ["page"]},
        ctx,
    )

    assert not rejected
    assert accepted[0].edge == ("references",)
    assert accepted[0].lift == pytest.approx(9.0)


def test_in_file_projects_the_destination_group_to_owning_files() -> None:
    ctx = make_file_endpoint_context(())

    accepted, rejected = validate_relations(
        [("clients", "owners", ("in_file",))],
        {"clients": ["client"], "owners": ["owner"]},
        ctx,
    )

    assert not rejected
    assert accepted[0].edge == ("in_file",)
    assert accepted[0].lift == pytest.approx(9.0)


def test_unrelated_file_edges_do_not_make_an_import_relation_pass() -> None:
    ctx = make_file_endpoint_context(
        (
            Edge(10, 3, "imports"),
            Edge(12, 2, "imports"),
        )
    )

    accepted, rejected = validate_relations(
        [("clients", "pages", ("imports",))],
        {"clients": ["client"], "pages": ["page"]},
        ctx,
    )

    assert not accepted
    assert len(rejected) == 1


def test_missing_ownership_rejects_a_file_endpoint_relation_cleanly() -> None:
    ctx = make_file_endpoint_context((), include_ownership=False)

    lift, detail = relation_lift(["client"], ["page"], ctx, edge=("imports",))

    assert lift == 0.0
    assert detail == "one side matched nothing"


def test_legacy_calls_contains_lift_and_two_tuple_behavior_are_unchanged() -> None:
    ctx = make_context((Edge(1, 2, "calls"),))
    contains_ctx = make_context((Edge(1, 2, "contains"),))

    assert relation_lift(["client"], ["page"], ctx) == (
        4.0,
        "1 crossings vs 0 expected (4.00x)",
    )
    assert relation_lift(["client"], ["page"], ctx, edge=("calls", "contains")) == (
        4.0,
        "1 crossings vs 0 expected (4.00x)",
    )
    assert relation_lift(["client"], ["page"], contains_ctx, edge=("contains",)) == (
        4.0,
        "1 crossings vs 0 expected (4.00x)",
    )
    accepted, rejected = validate_relations(
        [("clients", "pages")],
        {"clients": ["client"], "pages": ["page"]},
        ctx,
    )
    assert not rejected
    assert accepted[0].edge == ("calls", "contains")


def test_legacy_relation_checks_small_population_before_posting_matches() -> None:
    ctx = EvalContext(
        symbols=InMemorySymbolStore(
            [Element(symbol_id=1, name="only", kind="class", file="Only.java", span=(1, 2))]
        ),
        postings=InMemoryPostingIndex({}, total_symbols=1),
        expansion=InMemoryExpansionTable({}),
        edges=InMemoryEdgeStore(()),
    )

    assert relation_lift(["missing"], ["absent"], ctx) == (0.0, "too few symbols")


def test_mixed_endpoint_tuple_is_deterministic_and_filters_degrees_exactly() -> None:
    ctx = make_file_endpoint_context(
        (
            Edge(1, 2, "calls"),
            Edge(10, 2, "imports"),
            Edge(1, 3, "references"),
            Edge(10, 3, "references"),
        )
    )

    first = relation_lift(["client"], ["page"], ctx, edge=("calls", "imports"))
    second = relation_lift(["client"], ["page"], ctx, edge=("calls", "imports"))
    accepted, rejected = validate_relations(
        [("clients", "pages", ("calls", "imports"))],
        {"clients": ["client"], "pages": ["page"]},
        ctx,
    )

    assert first == second
    assert first[0] == pytest.approx(9.0)
    assert not rejected
    assert accepted[0].edge == ("calls", "imports")


@pytest.mark.parametrize("size", [399, 400, 401, 450])
def test_file_source_sampling_is_stratified_around_sample_cap(size: int) -> None:
    ctx = make_large_file_endpoint_context(size, "imports")

    lift, detail = relation_lift(["clients"], ["page"], ctx, edge=("imports",))

    assert lift >= LIFT_FLOOR
    assert detail.startswith(f"{size} crossings")


def test_import_derived_references_are_not_starved_after_400_declarations() -> None:
    size = 450
    target_id = size + 1
    file_id = size + 2
    elements = [
        *(Element(i, f"client{i}", "class", "Client.java", (1, 4)) for i in range(1, size + 1)),
        Element(target_id, "page", "class", "Page.java", (1, 4)),
        Element(file_id, "Client.java", "file", "Client.java", (1, 8)),
        Element(file_id + 1, "Page.java", "file", "Page.java", (1, 8)),
    ]
    ctx = EvalContext(
        symbols=InMemorySymbolStore(elements),
        postings=InMemoryPostingIndex(
            {
                "clients": [Posting(i, IndexField.NAME) for i in range(1, size + 1)],
                "page": [Posting(target_id, IndexField.NAME)],
            },
            total_symbols=size + 1,
        ),
        expansion=InMemoryExpansionTable({}),
        edges=InMemoryEdgeStore(
            (
                *(Edge(i, file_id, "in_file") for i in range(1, size + 1)),
                Edge(target_id, file_id + 1, "in_file"),
                Edge(file_id, target_id, "references"),
            )
        ),
        declaration_count=size + 1,
    )

    lift, _ = relation_lift(["clients"], ["page"], ctx, edge=("references",))

    assert lift >= LIFT_FLOOR


def test_mixed_calls_and_imports_keep_the_file_source_stratum_after_cap() -> None:
    size = 450
    ctx = make_large_file_endpoint_context(size, "imports")

    lift, _ = relation_lift(["clients"], ["page"], ctx, edge=("calls", "imports"))

    assert lift >= LIFT_FLOOR


def test_unrelated_edge_kinds_do_not_change_typed_crossing_or_degree() -> None:
    clean = make_file_endpoint_context((Edge(10, 2, "imports"),))
    noisy = make_file_endpoint_context(
        (
            Edge(10, 2, "imports"),
            Edge(10, 2, "references"),
            Edge(10, 3, "references"),
            Edge(10, 3, "calls"),
        )
    )

    assert relation_lift(["client"], ["page"], noisy, edge=("imports",)) == relation_lift(
        ["client"], ["page"], clean, edge=("imports",)
    )


def test_repeated_typed_edge_kind_is_counted_once() -> None:
    ctx = make_file_endpoint_context((Edge(10, 2, "imports"),))

    assert relation_lift(["client"], ["page"], ctx, edge=("imports", "imports")) == relation_lift(
        ["client"], ["page"], ctx, edge=("imports",)
    )


@pytest.mark.parametrize(
    ("edge", "dst_terms"),
    [
        (("imports",), ["page", "page_alias"]),
        (("in_file",), ["owner", "owner_alias"]),
    ],
)
def test_build_spec_retains_file_endpoint_relations_without_file_postings(
    edge: tuple[str, ...], dst_terms: list[str]
) -> None:
    extra_edges = (Edge(10, 2, "imports"),) if edge == ("imports",) else ()
    ctx = make_file_endpoint_context(extra_edges)

    spec, notes = build_spec(
        "client relation target",
        [ScoredTerm("client"), ScoredTerm(dst_terms[0])],
        ctx,
        groups={
            "clients": ["client", "client_alias"],
            "targets": dst_terms,
        },
        relations=[("clients", "targets", edge)],
    )

    assert ctx.postings.lookup("Client.java") == ()
    assert spec.graph[0].edge == edge
    assert any("accepted relation" in note for note in notes)
