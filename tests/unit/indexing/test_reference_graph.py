"""Resolution of project-local reference facts into graph edges."""

from __future__ import annotations

import pytest

from codesense.indexing import GraphBuilder
from codesense.lang import Declaration, Invocation, ReferenceUse


def edge_keys(edges: list[dict[str, object]]) -> set[tuple[int, int, str]]:
    """Return the public graph identity of every materialized edge."""
    return {(int(edge["source_id"]), int(edge["target_id"]), str(edge["kind"])) for edge in edges}


def edge(
    edges: list[dict[str, object]], source_id: int, target_id: int, kind: str
) -> dict[str, object]:
    """Select one edge by its deduplication key."""
    return next(
        item
        for item in edges
        if (item["source_id"], item["target_id"], item["kind"]) == (source_id, target_id, kind)
    )


@pytest.fixture
def built_reference_edges() -> tuple[GraphBuilder, list[dict[str, object]]]:
    builder = GraphBuilder(frozenset({"class"}), frozenset({"method"}))
    page_request = Declaration(
        name="PageRequest",
        kind="class",
        container="org.springframework.data.domain",
        line=1,
        end_line=3,
    )
    other_page_request = Declaration(
        name="PageRequest",
        kind="class",
        container="example.shadow",
        line=1,
        end_line=3,
    )
    client = Declaration(
        name="Client",
        kind="class",
        container="example",
        line=3,
        end_line=20,
    )
    method = Declaration(
        name="fetch",
        kind="method",
        container="example.Client",
        line=5,
        end_line=10,
    )
    for declaration, symbol_id in (
        (page_request, 1),
        (method, 2),
        (client, 3),
        (other_page_request, 4),
    ):
        builder.observe(declaration, symbol_id)
    builder.observe_file(
        "Client.java",
        10,
        ((client, 3), (method, 2)),
        (
            ReferenceUse(
                "PageRequest",
                line=7,
                column=18,
                qualified_name="org.springframework.data.domain.PageRequest",
            ),
            ReferenceUse(
                "PageRequest",
                line=1,
                column=7,
                relation="imports",
                qualified_name="org.springframework.data.domain.PageRequest",
            ),
            ReferenceUse("ExternalType", line=8, column=4),
        ),
    )
    return builder, builder.build()


def test_method_type_reference_prefers_the_unique_qualified_type(
    built_reference_edges: tuple[GraphBuilder, list[dict[str, object]]],
) -> None:
    _, edges = built_reference_edges

    assert edge_keys(edges) >= {(2, 1, "references")}
    resolved = edge(edges, 2, 1, "references")
    assert resolved["site"] == [7, 18]
    assert resolved["confidence"] == 1.0
    assert resolved["provenance"] == "qualified_name"
    assert (2, 4, "references") not in edge_keys(edges)


def test_smallest_enclosing_declaration_owns_the_reference(
    built_reference_edges: tuple[GraphBuilder, list[dict[str, object]]],
) -> None:
    _, edges = built_reference_edges

    assert (2, 1, "references") in edge_keys(edges)
    assert (3, 1, "references") not in edge_keys(edges)


def test_import_is_owned_by_the_file_and_also_counts_as_a_reference(
    built_reference_edges: tuple[GraphBuilder, list[dict[str, object]]],
) -> None:
    builder, edges = built_reference_edges

    assert edge_keys(edges) >= {
        (10, 1, "imports"),
        (10, 1, "references"),
    }
    imported = edge(edges, 10, 1, "imports")
    broad = edge(edges, 10, 1, "references")
    assert imported["site"] == broad["site"] == [1, 7]
    assert imported["provenance"] == broad["provenance"] == "qualified_name"
    assert builder.stats.imports == 1


def test_unresolved_external_reference_is_counted(
    built_reference_edges: tuple[GraphBuilder, list[dict[str, object]]],
) -> None:
    builder, edges = built_reference_edges

    assert not [item for item in edges if item.get("site") == [8, 4]]
    assert builder.stats.reference_unresolved == 1


def test_simple_name_ambiguity_splits_confidence_between_candidates() -> None:
    builder = GraphBuilder(frozenset({"class"}), frozenset({"method"}))
    for symbol_id, package in ((1, "one"), (2, "two")):
        builder.observe(
            Declaration("Widget", "class", container=package, line=1, end_line=2),
            symbol_id,
        )
    builder.observe_file(
        "Client.java",
        10,
        (),
        (ReferenceUse("Widget", line=4, column=2),),
    )

    references = [item for item in builder.build() if item["kind"] == "references"]

    assert edge_keys(references) == {
        (10, 1, "references"),
        (10, 2, "references"),
    }
    assert {item["confidence"] for item in references} == {0.4}
    assert {item["provenance"] for item in references} == {"simple_name"}


def test_missing_qualified_name_falls_back_to_a_unique_simple_name() -> None:
    builder = GraphBuilder(frozenset({"class"}), frozenset({"method"}))
    builder.observe(Declaration("PageRequest", "class", line=1, end_line=2), 1)
    builder.observe_file(
        "Client.java",
        10,
        (),
        (
            ReferenceUse(
                "PageRequest",
                line=1,
                column=7,
                relation="imports",
                qualified_name="org.springframework.data.domain.PageRequest",
            ),
        ),
    )

    imported = edge(builder.build(), 10, 1, "imports")

    assert imported["confidence"] == 0.8
    assert imported["provenance"] == "simple_name"


def test_ambiguous_reference_above_the_cap_is_not_materialized() -> None:
    builder = GraphBuilder(frozenset({"class"}), frozenset({"method"}))
    for symbol_id in range(1, 10):
        builder.observe(
            Declaration(
                "Shared",
                "class",
                container=f"package{symbol_id}",
                line=1,
                end_line=2,
            ),
            symbol_id,
        )
    builder.observe_file(
        "Client.java",
        20,
        (),
        (ReferenceUse("Shared", line=1),),
    )

    assert not [item for item in builder.build() if item["kind"] == "references"]
    assert builder.stats.reference_ambiguous == 1


def test_resolved_call_also_materializes_the_same_broad_reference() -> None:
    builder = GraphBuilder(frozenset({"class"}), frozenset({"method"}))
    service = Declaration("Service", "class", container="example", line=1, end_line=20)
    load = Declaration("load", "method", container="example.Service", line=3, end_line=6)
    caller = Declaration(
        "run",
        "method",
        container="example.Client",
        line=10,
        end_line=15,
        calls=(Invocation("load", receiver="service", line=12),),
        local_types=(("service", "Service"),),
    )
    for declaration, symbol_id in ((service, 1), (load, 2), (caller, 3)):
        builder.observe(declaration, symbol_id)
    builder.observe_file(
        "Client.java",
        10,
        ((caller, 3),),
        (ReferenceUse("load", line=13, column=4),),
    )

    edges = builder.build()
    call = edge(edges, 3, 2, "calls")
    broad = edge(edges, 3, 2, "references")

    assert call["site"] == broad["site"] == [12, 0]
    assert call["confidence"] == broad["confidence"] == 0.9
    assert call["provenance"] == broad["provenance"] == "typed_receiver"
    assert builder.stats.calls == 1
    assert builder.stats.references == 1


def test_legacy_call_without_a_line_keeps_a_none_site() -> None:
    builder = GraphBuilder(frozenset({"class"}), frozenset({"method"}))
    target = Declaration("load", "method", container="example.Service")
    caller = Declaration(
        "run",
        "method",
        container="example.Client",
        calls=(Invocation("load"),),
    )
    builder.observe(target, 1)
    builder.observe(caller, 2)

    call = edge(builder.build(), 2, 1, "calls")

    assert call["site"] is None
