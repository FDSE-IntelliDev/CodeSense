"""Project-wide Java relation derivation from scanner facts."""

from __future__ import annotations

from collections import defaultdict, deque
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
_METHOD_SOURCE_KINDS = frozenset({"class", "interface", "enum", "record"})
_NON_VIRTUAL_SOURCE_MODIFIERS = frozenset({"static", "private"})
_NON_VIRTUAL_TARGET_MODIFIERS = frozenset({"static", "private", "final"})
MAX_ANCESTOR_DEPTH = 8
MAX_METHOD_CANDIDATES = 8


@dataclass(frozen=True, slots=True)
class _Resolution:
    target: IndexedDeclaration | None = None
    confidence: float = 0.0
    provenance: str = ""
    diagnostic: str = ""


def derive_java_relations(context: RelationContext) -> RelationBatch:
    """Resolve direct Java type and heuristic method relations."""
    types = tuple(item for item in context.declarations if item.declaration.kind in _TYPE_KINDS)
    by_qualified: dict[str, list[IndexedDeclaration]] = defaultdict(list)
    by_simple: dict[str, list[IndexedDeclaration]] = defaultdict(list)
    for item in types:
        by_qualified[item.declaration.qualified_name].append(item)
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

    parents_by_type: dict[int, list[int]] = defaultdict(list)
    for fact in facts:
        parents_by_type[fact.source_id].append(fact.target_id)

    types_by_id = {item.symbol_id: item for item in types}
    methods_by_owner: dict[int, dict[tuple[str, int], list[IndexedDeclaration]]] = defaultdict(
        lambda: defaultdict(list)
    )
    method_owners: dict[int, int] = {}
    for item in context.declarations:
        if item.declaration.kind != "method":
            continue
        owner = _method_owner(item, by_qualified)
        if owner is None:
            unresolved += 1
            continue
        method_owners[item.symbol_id] = owner.symbol_id
        key = (item.declaration.name, len(item.declaration.parameter_types))
        methods_by_owner[owner.symbol_id][key].append(item)

    for source in context.declarations:
        if source.declaration.kind != "method" or source.symbol_id not in method_owners:
            continue
        source_owner = types_by_id[method_owners[source.symbol_id]]
        if source.declaration.modifiers & _NON_VIRTUAL_SOURCE_MODIFIERS:
            skipped += 1
            continue
        class_match_depth: int | None = None
        key = (source.declaration.name, len(source.declaration.parameter_types))
        for ancestor_id, depth in _ancestors(source_owner.symbol_id, parents_by_type):
            ancestor = types_by_id[ancestor_id]
            candidates = methods_by_owner.get(ancestor_id, {}).get(key, ())
            if not candidates:
                continue
            if ancestor.declaration.kind == "class":
                if class_match_depth is not None and depth > class_match_depth:
                    continue
                class_match_depth = depth
            if len(candidates) > MAX_METHOD_CANDIDATES:
                ambiguous += 1
                continue
            eligible = [
                candidate
                for candidate in candidates
                if not candidate.declaration.modifiers & _NON_VIRTUAL_TARGET_MODIFIERS
            ]
            if not eligible:
                skipped += 1
                continue
            selected, confidence, provenance = _select_method(source, eligible)
            if selected is None:
                ambiguous += 1
                continue
            relation = _method_relation(source_owner.declaration.kind, ancestor.declaration.kind)
            if relation is None:
                skipped += 1
                continue
            facts.append(
                RelationFact(
                    source.symbol_id,
                    selected.symbol_id,
                    relation,
                    site=(source.declaration.line, source.declaration.column),
                    confidence=confidence,
                    provenance=provenance,
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
    by_qualified: dict[str, list[IndexedDeclaration]],
    by_simple: dict[str, list[IndexedDeclaration]],
) -> _Resolution:
    """Prefer the unique same-package parent, then a global unique name."""
    package = _package(source.declaration)
    qualified_name = f"{package}.{name}" if package else name
    exact = by_qualified.get(qualified_name, ())
    if len(exact) == 1:
        return _Resolution(exact[0], 0.8, "java_supertypes_same_package")
    if len(exact) > 1:
        return _Resolution(diagnostic="ambiguous")

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


def _method_owner(
    method: IndexedDeclaration,
    by_qualified: dict[str, list[IndexedDeclaration]],
) -> IndexedDeclaration | None:
    """Resolve the method's qualified owner without guessing by simple name."""
    owner_name, separator, _ = method.declaration.qualified_name.rpartition(".")
    if not separator:
        return None
    candidates = by_qualified.get(owner_name, ())
    return candidates[0] if len(candidates) == 1 else None


def _ancestors(
    source_id: int,
    parents_by_type: dict[int, list[int]],
) -> tuple[tuple[int, int], ...]:
    """Return shortest-path ancestors in stable breadth-first order."""
    found: list[tuple[int, int]] = []
    visited = {source_id}
    pending = deque((parent, 1) for parent in parents_by_type.get(source_id, ()))
    while pending:
        ancestor_id, depth = pending.popleft()
        if ancestor_id in visited or depth > MAX_ANCESTOR_DEPTH:
            continue
        visited.add(ancestor_id)
        found.append((ancestor_id, depth))
        pending.extend((parent, depth + 1) for parent in parents_by_type.get(ancestor_id, ()))
    return tuple(found)


def _select_method(
    source: IndexedDeclaration,
    candidates: list[IndexedDeclaration],
) -> tuple[IndexedDeclaration | None, float, str]:
    """Prefer one exact normalized signature, then one name/arity candidate."""
    exact = [
        candidate
        for candidate in candidates
        if candidate.declaration.parameter_types == source.declaration.parameter_types
    ]
    has_override = _has_override(source.declaration)
    if len(exact) == 1:
        return (
            exact[0],
            0.95 if has_override else 0.90,
            "java_override_exact" if has_override else "java_signature_match",
        )
    if not exact and len(candidates) == 1:
        return (
            candidates[0],
            0.80 if has_override else 0.65,
            "java_override_arity" if has_override else "java_name_arity",
        )
    return None, 0.0, ""


def _has_override(declaration: Declaration) -> bool:
    return any(
        annotation.name.rsplit(".", 1)[-1] == "Override" for annotation in declaration.annotations
    )


def _method_relation(source_kind: str, target_kind: str) -> str | None:
    if source_kind not in _METHOD_SOURCE_KINDS:
        return None
    if target_kind == "class":
        return "overrides"
    if target_kind == "interface":
        return "overrides" if source_kind == "interface" else "implements"
    return None


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
