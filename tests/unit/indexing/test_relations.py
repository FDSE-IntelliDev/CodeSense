from __future__ import annotations

import math

import pytest

from codesense.indexing.relations import build_relation_edges, deduplicate_edge_rows
from codesense.lang import RelationFact


def test_builds_open_relation_kinds_and_keeps_evidence() -> None:
    rows = build_relation_edges(
        (
            RelationFact(
                2,
                1,
                "implements",
                site=(7, 4),
                confidence=0.7,
                provenance="java_supertypes_simple",
            ),
        ),
        symbol_ids={1, 2},
    )

    assert rows == [
        {
            "source_id": 2,
            "target_id": 1,
            "kind": "implements",
            "site": [7, 4],
            "confidence": 0.7,
            "provenance": "java_supertypes_simple",
        }
    ]


def test_duplicate_relation_keeps_strongest_fact_deterministically() -> None:
    rows = build_relation_edges(
        (
            RelationFact(2, 1, "extends", confidence=0.7, provenance="z"),
            RelationFact(2, 1, "extends", confidence=0.9, provenance="b"),
            RelationFact(2, 1, "extends", confidence=0.9, provenance="a"),
        ),
        symbol_ids={1, 2},
    )

    assert len(rows) == 1
    assert rows[0]["confidence"] == 0.9
    assert rows[0]["provenance"] == "a"


def test_edge_row_deduplication_also_handles_existing_graph_rows() -> None:
    rows = deduplicate_edge_rows(
        (
            {
                "source_id": 2,
                "target_id": 1,
                "kind": "implements",
                "site": None,
                "confidence": 0.6,
                "provenance": "graph",
            },
            {
                "source_id": 2,
                "target_id": 1,
                "kind": "implements",
                "site": [7, 4],
                "confidence": 0.8,
                "provenance": "adapter",
            },
        )
    )

    assert len(rows) == 1
    assert rows[0]["confidence"] == 0.8
    assert rows[0]["provenance"] == "adapter"


@pytest.mark.parametrize(
    "fact",
    (
        RelationFact(9, 1, "extends"),
        RelationFact(1, 9, "extends"),
        RelationFact(1, 1, "extends"),
        RelationFact(2, 1, ""),
        RelationFact(2, 1, "extends", confidence=-0.1),
        RelationFact(2, 1, "extends", confidence=1.1),
        RelationFact(2, 1, "extends", confidence=math.nan),
    ),
)
def test_invalid_relation_contract_is_rejected(fact: RelationFact) -> None:
    with pytest.raises(ValueError):
        build_relation_edges((fact,), symbol_ids={1, 2})
