"""Language-neutral validation and serialization for adapter relations."""

from __future__ import annotations

import math
from collections.abc import Collection, Mapping, Sequence
from typing import Any

from codesense.lang import RelationFact

__all__ = ["build_relation_edges", "deduplicate_edge_rows"]


def build_relation_edges(
    facts: Sequence[RelationFact],
    symbol_ids: Collection[int],
) -> list[dict[str, object]]:
    """Validate relation facts and retain one deterministic fact per edge."""
    known = set(symbol_ids)
    unique: dict[tuple[int, int, str], RelationFact] = {}
    for fact in facts:
        if fact.source_id not in known or fact.target_id not in known:
            raise ValueError("relation endpoint is not in the index")
        if fact.source_id == fact.target_id:
            raise ValueError("relation cannot be a self edge")
        if not fact.kind:
            raise ValueError("relation kind cannot be empty")
        if not math.isfinite(fact.confidence) or not 0.0 <= fact.confidence <= 1.0:
            raise ValueError("relation confidence must be finite and in [0, 1]")
        key = (fact.source_id, fact.target_id, fact.kind)
        current = unique.get(key)
        if current is None or _fact_order(fact) < _fact_order(current):
            unique[key] = fact
    return [_edge_row(unique[key]) for key in sorted(unique)]


def deduplicate_edge_rows(
    rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, object]]:
    """Keep the strongest deterministic row for every graph edge key."""
    unique: dict[tuple[int, int, str], Mapping[str, Any]] = {}
    for row in rows:
        key = (row["source_id"], row["target_id"], row["kind"])
        current = unique.get(key)
        if current is None or _row_order(row) < _row_order(current):
            unique[key] = row
    return [dict(unique[key]) for key in sorted(unique)]


def _fact_order(fact: RelationFact) -> tuple[float, str, tuple[int, int]]:
    return (-fact.confidence, fact.provenance, fact.site or (-1, -1))


def _row_order(row: Mapping[str, Any]) -> tuple[float, str, tuple[int, int]]:
    site = row.get("site")
    normalized_site = tuple(site) if site is not None else (-1, -1)
    return (-float(row["confidence"]), str(row["provenance"]), normalized_site)


def _edge_row(fact: RelationFact) -> dict[str, object]:
    return {
        "source_id": fact.source_id,
        "target_id": fact.target_id,
        "kind": fact.kind,
        "site": list(fact.site) if fact.site is not None else None,
        "confidence": fact.confidence,
        "provenance": fact.provenance,
    }
