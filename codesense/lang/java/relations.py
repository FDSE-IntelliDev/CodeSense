"""Project-wide Java relation derivation from scanner facts."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from codesense.lang.base import (
    Declaration,
    IndexedDeclaration,
    RelationBatch,
    RelationContext,
    RelationDiagnostics,
    RelationFact,
)

__all__ = ["derive_java_relations"]

_TYPE_KINDS = frozenset({"class", "interface", "enum", "record", "annotation_type"})
_TYPE_RELATIONS = {
    ("class", "class"): "extends",
    ("class", "interface"): "implements",
    ("interface", "interface"): "extends",
    ("enum", "interface"): "implements",
    ("record", "interface"): "implements",
}


@dataclass(frozen=True, slots=True)
class _Resolution:
    target: IndexedDeclaration | None = None
    confidence: float = 0.0
    provenance: str = ""
    diagnostic: str = ""


def derive_java_relations(context: RelationContext) -> RelationBatch:
    """Resolve direct Java type relations after project symbol IDs exist."""
    types = tuple(item for item in context.declarations if item.declaration.kind in _TYPE_KINDS)
    by_qualified = {item.declaration.qualified_name: item for item in types}
    by_simple: dict[str, list[IndexedDeclaration]] = defaultdict(list)
    for item in types:
        by_simple[item.declaration.name].append(item)

    facts: list[RelationFact] = []
    unresolved = ambiguous = skipped = 0
    for source in types:
        for parent_name in source.declaration.supertypes:
            resolution = _resolve_parent(source, parent_name, by_qualified, by_simple)
            if resolution.diagnostic == "unresolved":
                unresolved += 1
                continue
            if resolution.diagnostic == "ambiguous":
                ambiguous += 1
                continue
            target = resolution.target
            if target is None:
                continue
            relation = _TYPE_RELATIONS.get((source.declaration.kind, target.declaration.kind))
            if relation is None or source.symbol_id == target.symbol_id:
                skipped += 1
                continue
            facts.append(
                RelationFact(
                    source.symbol_id,
                    target.symbol_id,
                    relation,
                    site=(source.declaration.line, source.declaration.column),
                    confidence=resolution.confidence,
                    provenance=resolution.provenance,
                )
            )
    return RelationBatch(
        facts=tuple(facts),
        diagnostics=RelationDiagnostics(
            unresolved=unresolved,
            ambiguous=ambiguous,
            skipped=skipped,
        ),
    )


def _resolve_parent(
    source: IndexedDeclaration,
    name: str,
    by_qualified: dict[str, IndexedDeclaration],
    by_simple: dict[str, list[IndexedDeclaration]],
) -> _Resolution:
    """Prefer the unique same-package parent, then a global unique name."""
    package = _package(source.declaration)
    qualified_name = f"{package}.{name}" if package else name
    exact = by_qualified.get(qualified_name)
    if exact is not None:
        return _Resolution(exact, 0.8, "java_supertypes_same_package")

    candidates = by_simple.get(name, ())
    same_package = tuple(
        candidate for candidate in candidates if _package(candidate.declaration) == package
    )
    if len(same_package) == 1:
        return _Resolution(same_package[0], 0.8, "java_supertypes_same_package")
    if len(same_package) > 1:
        return _Resolution(diagnostic="ambiguous")
    if len(candidates) == 1:
        return _Resolution(candidates[0], 0.7, "java_supertypes_simple")
    if candidates:
        return _Resolution(diagnostic="ambiguous")
    return _Resolution(diagnostic="unresolved")


def _structural_name(declaration: Declaration) -> str:
    return (
        f"{declaration.container}.{declaration.name}" if declaration.container else declaration.name
    )


def _package(declaration: Declaration) -> str:
    structural = _structural_name(declaration)
    qualified = declaration.qualified_name
    if qualified == structural:
        return ""
    suffix = f".{structural}"
    return qualified[: -len(suffix)] if qualified.endswith(suffix) else ""
