"""Physical graph endpoint roles for logical declaration relations.

Query units always contain declarations because file nodes deliberately have
no postings. Typed graph edges do not always connect two declarations,
however, so validation and execution must share this projection contract.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

from codesense.ql.context import EvalContext

__all__ = [
    "EndpointStratum",
    "is_legacy_relation",
    "logical_destinations",
    "relation_endpoint_strata",
]

_LEGACY_EDGE_KINDS = frozenset(("calls", "contains"))


@dataclass(frozen=True, slots=True)
class EndpointStratum:
    """One homogeneous edge kind and pair of physical endpoint roles."""

    kind: str
    source_ids: frozenset[int]
    destination_ids: frozenset[int]
    source_population: int
    destination_population: int
    destination_to_logical: Mapping[int, frozenset[int]]


def is_legacy_relation(edge: Sequence[str]) -> bool:
    """Whether a tuple has the historical declaration-only semantics."""
    kinds = tuple(edge)
    return bool(kinds) and set(kinds) <= _LEGACY_EDGE_KINDS


def relation_endpoint_strata(
    src_declarations: Iterable[int],
    dst_declarations: Iterable[int],
    ctx: EvalContext,
    edge: Sequence[str],
) -> tuple[EndpointStratum, ...]:
    """Project logical declaration candidates into homogeneous edge strata.

    Ownership is resolved only from the supplied candidates through exact
    one-hop ``in_file`` edges. No symbol-table or graph-wide scan is needed.
    """
    src = frozenset(src_declarations)
    dst = frozenset(dst_declarations)
    src_owners = _ownership_by_declaration(src, ctx)
    dst_owners = _ownership_by_declaration(dst, ctx)
    src_files = frozenset(owner for owners in src_owners.values() for owner in owners)
    dst_files = frozenset(owner for owners in dst_owners.values() for owner in owners)
    declarations = ctx.population
    files = max(ctx.symbols.count() - declarations, 0) if ctx.declaration_count is not None else 0
    declaration_targets = MappingProxyType(
        {symbol_id: frozenset((symbol_id,)) for symbol_id in dst}
    )
    file_targets = MappingProxyType(_logical_declarations_by_owner(dst_owners))

    strata: list[EndpointStratum] = []
    # Preserve proposal order for deterministic details, but repeated kinds
    # are one logical constraint and must not be counted twice.
    for kind in dict.fromkeys(edge):
        if kind == "imports":
            strata.append(
                EndpointStratum(kind, src_files, dst, files, declarations, declaration_targets)
            )
        elif kind == "in_file":
            strata.append(EndpointStratum(kind, src, dst_files, declarations, files, file_targets))
        elif kind == "references":
            # A reference can originate in the smallest declaration or at
            # file scope (notably an import), so these sources are sampled
            # independently instead of competing for one global cap.
            strata.extend(
                (
                    EndpointStratum(
                        kind,
                        src,
                        dst,
                        declarations,
                        declarations,
                        declaration_targets,
                    ),
                    EndpointStratum(
                        kind,
                        src_files,
                        dst,
                        files,
                        declarations,
                        declaration_targets,
                    ),
                )
            )
        else:
            # Legacy and future declaration-to-declaration kinds retain the
            # ordinary endpoint role inside a typed/mixed tuple.
            strata.append(
                EndpointStratum(
                    kind,
                    src,
                    dst,
                    declarations,
                    declarations,
                    declaration_targets,
                )
            )
    return tuple(strata)


def logical_destinations(stratum: EndpointStratum, physical_ids: Iterable[int]) -> set[int]:
    """Map reached physical destinations back to logical declaration IDs."""
    return {
        logical_id
        for physical_id in physical_ids
        for logical_id in stratum.destination_to_logical.get(physical_id, ())
    }


def _ownership_by_declaration(
    declarations: Iterable[int], ctx: EvalContext
) -> dict[int, frozenset[int]]:
    """Return exact owner file IDs for each supplied declaration."""
    return {
        symbol_id: frozenset(
            relation.target_id for relation in ctx.edges.out_edges(symbol_id, kinds=("in_file",))
        )
        for symbol_id in declarations
    }


def _logical_declarations_by_owner(
    owners: Mapping[int, frozenset[int]],
) -> dict[int, frozenset[int]]:
    grouped: dict[int, set[int]] = {}
    for declaration_id, file_ids in owners.items():
        for file_id in file_ids:
            grouped.setdefault(file_id, set()).add(declaration_id)
    return {file_id: frozenset(declarations) for file_id, declarations in grouped.items()}
