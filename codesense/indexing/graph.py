"""Project graph edges resolved from language-neutral scan facts.

The confidence gap between the edge kinds is the honest part:

    contains   derived from the container field. A fact, confidence 1.0, and
               the cheapest structural win available -- materialising it takes
               orphan symbols from 33% to 2%.
    calls      resolved through the receiver's declared type where possible,
               by name where not. **Never 1.0**: this is not a real call
               graph, and pretending otherwise would let `hop` return paths
               that do not exist.
    references resolved through exact qualified names or capped simple-name
               candidates. Imports and calls also retain their precise edge
               while materialising this broad relation.

CodeQL is more accurate and needs a full build (`mvn compile` for netty).
Exhausting the type information already in the AST lifts unique resolution
from 13% to 44%, measured, for none of that cost. Virtual dispatch, generics
and cross-library calls stay out of reach -- those genuinely need CodeQL.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from codesense.lang.base import Declaration, ReferenceUse

__all__ = ["GraphBuilder", "GraphStats", "TypeTable"]

#: Most candidates one callee name may resolve to. A name like `get` matches
#: hundreds in a large project, and connecting them all turns the graph into
#: noise -- **better to miss an edge than to build a fictional graph**.
MAX_CALL_CANDIDATES = 8

#: Confidence ceiling for name-matched call edges, divided by how many
#: candidates the name reached: the more ambiguous, the less each edge means.
NAME_CALL_CONFIDENCE = 0.6

#: Call edges resolved through the receiver's type. Still short of 1.0 --
#: virtual dispatch is not handled.
TYPED_CALL_CONFIDENCE = 0.9

#: Most project declarations one simple reference name may resolve to. Above
#: this, materialising every candidate would create a noisy graph hub.
MAX_REFERENCE_CANDIDATES = 8

#: Exact project-local qualified names are the strongest reference fact.
QUALIFIED_REFERENCE_CONFIDENCE = 1.0

#: Simple names can be shadowed by imports or local declarations.
SIMPLE_REFERENCE_CONFIDENCE = 0.8


@dataclass
class TypeTable:
    """In-project type information, for resolving receivers.

    Populated during the scan and consulted after it, because a call can
    reference a type declared in a file not yet read.
    """

    fields: dict[str, dict[str, str]] = field(default_factory=lambda: defaultdict(dict))
    methods: dict[str, dict[str, list[int]]] = field(
        default_factory=lambda: defaultdict(lambda: defaultdict(list))
    )
    supertypes: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def lookup(self, type_name: str, method: str, depth: int = 3) -> list[int]:
        """Find a method on a type or its supertypes."""
        simple = type_name.split("<")[0].split(".")[-1].strip()
        seen: set[str] = set()
        queue = [(simple, depth)]
        while queue:
            current, left = queue.pop(0)
            if current in seen or left < 0:
                continue
            seen.add(current)
            found = self.methods.get(current, {}).get(method)
            if found:
                return found
            queue.extend((parent, left - 1) for parent in self.supertypes.get(current, ()))
        return []


@dataclass
class GraphStats:
    contains: int = 0
    calls: int = 0
    typed: int = 0
    ambiguous: int = 0
    unresolved: int = 0
    references: int = 0
    imports: int = 0
    reference_ambiguous: int = 0
    reference_unresolved: int = 0


@dataclass(frozen=True, slots=True)
class _FileReferences:
    """Reference facts waiting for all project declarations to be known."""

    path: str
    file_id: int
    declarations: tuple[tuple[Declaration, int], ...]
    references: tuple[ReferenceUse, ...]


class GraphBuilder:
    """Collects what edges need, then builds them once the scan is complete."""

    def __init__(self, container_kinds: frozenset[str], callable_kinds: frozenset[str]) -> None:
        self._container_kinds = container_kinds
        self._callable_kinds = callable_kinds
        self.types = TypeTable()
        self.stats = GraphStats()
        self._by_qualified: dict[str, int] = {}
        self._by_simple_name: dict[str, list[int]] = defaultdict(list)
        self._references_by_qualified: dict[str, list[int]] = defaultdict(list)
        self._references_by_simple_name: dict[str, list[int]] = defaultdict(list)
        self._containers: list[tuple[int, str]] = []
        self._invocations: list[tuple[int, str, dict[str, str], Sequence[Any]]] = []
        self._file_references: list[_FileReferences] = []

    def observe(self, declaration: Declaration, symbol_id: int) -> None:
        """Record what this declaration contributes to the graph."""
        owner = declaration.container.split(".")[-1]
        qualified = (
            f"{declaration.container}.{declaration.name}"
            if declaration.container
            else declaration.name
        )
        self._references_by_qualified[qualified].append(symbol_id)
        self._references_by_simple_name[declaration.name].append(symbol_id)
        if declaration.kind in self._callable_kinds:
            self._by_simple_name[declaration.name].append(symbol_id)
            self.types.methods[owner][declaration.name].append(symbol_id)
            if declaration.calls:
                self._invocations.append(
                    (symbol_id, owner, dict(declaration.local_types), declaration.calls)
                )
        elif declaration.signature and declaration.kind not in self._container_kinds:
            self.types.fields[owner][declaration.name] = declaration.signature
        if declaration.container:
            self._containers.append((symbol_id, declaration.container))
        if declaration.kind in self._container_kinds:
            if declaration.supertypes:
                self.types.supertypes[declaration.name] = declaration.supertypes
            self._by_qualified.setdefault(qualified, symbol_id)

    def observe_file(
        self,
        path: str,
        file_id: int,
        declarations: Sequence[tuple[Declaration, int]],
        references: Sequence[ReferenceUse],
    ) -> None:
        """Record one file's reference facts after its stable ID is assigned."""
        self._file_references.append(
            _FileReferences(
                path=path,
                file_id=file_id,
                declarations=tuple(declarations),
                references=tuple(references),
            )
        )

    def build(self) -> list[dict[str, Any]]:
        edges = _deduplicate_edges(self._contains() + self._calls() + self._references())
        self.stats.contains = sum(edge["kind"] == "contains" for edge in edges)
        self.stats.calls = sum(edge["kind"] == "calls" for edge in edges)
        self.stats.references = sum(edge["kind"] == "references" for edge in edges)
        self.stats.imports = sum(edge["kind"] == "imports" for edge in edges)
        return edges

    def _contains(self) -> list[dict[str, Any]]:
        return [
            {
                "source_id": self._by_qualified[container],
                "target_id": symbol_id,
                "kind": "contains",
                "confidence": 1.0,
                "provenance": "derived_container",
            }
            for symbol_id, container in self._containers
            if container in self._by_qualified and self._by_qualified[container] != symbol_id
        ]

    def _calls(self) -> list[dict[str, Any]]:
        edges: list[dict[str, Any]] = []
        for caller, owner, scope, calls in self._invocations:
            for call in calls:
                type_name = _receiver_type(call.receiver, scope, owner, self.types)
                targets = self.types.lookup(type_name, call.name) if type_name else []
                provenance, ceiling = "typed_receiver", TYPED_CALL_CONFIDENCE
                if not targets:
                    targets = self._by_simple_name.get(call.name, [])
                    provenance, ceiling = "name_match", NAME_CALL_CONFIDENCE
                    if not targets:
                        self.stats.unresolved += 1
                        continue
                    if len(targets) > MAX_CALL_CANDIDATES:
                        self.stats.ambiguous += 1
                        continue
                else:
                    self.stats.typed += 1
                confidence = ceiling / len(targets)
                edges.extend(
                    edge
                    for target in targets
                    if target != caller
                    for edge in _call_edges(
                        caller,
                        target,
                        site=[call.line, 0] if call.line else None,
                        confidence=round(confidence, 4),
                        provenance=provenance,
                    )
                )
        return edges

    def _references(self) -> list[dict[str, Any]]:
        """Resolve every recorded use through precomputed project name maps."""
        edges: list[dict[str, Any]] = []
        for record in self._file_references:
            intervals = tuple(
                (declaration.line, declaration.end_line, symbol_id)
                for declaration, symbol_id in record.declarations
            )
            for use in record.references:
                owner_id = _reference_owner(use.line, intervals, record.file_id)
                targets: Sequence[int] = ()
                if use.qualified_name:
                    targets = self._references_by_qualified.get(use.qualified_name, ())
                    provenance = "qualified_name"
                    ceiling = QUALIFIED_REFERENCE_CONFIDENCE
                if not targets:
                    targets = self._references_by_simple_name.get(use.name, ())
                    provenance = "simple_name"
                    ceiling = SIMPLE_REFERENCE_CONFIDENCE
                if not targets:
                    self.stats.reference_unresolved += 1
                    continue
                if len(targets) > MAX_REFERENCE_CANDIDATES:
                    self.stats.reference_ambiguous += 1
                    continue
                confidence = round(ceiling / len(targets), 4)
                for target_id in targets:
                    edges.append(
                        _reference_edge(
                            owner_id,
                            target_id,
                            kind=use.relation,
                            site=[use.line, use.column],
                            confidence=confidence,
                            provenance=provenance,
                        )
                    )
                    if use.relation == "imports":
                        edges.append(
                            _reference_edge(
                                owner_id,
                                target_id,
                                kind="references",
                                site=[use.line, use.column],
                                confidence=confidence,
                                provenance=provenance,
                            )
                        )
        return edges


def _reference_owner(line: int, intervals: Sequence[tuple[int, int, int]], file_id: int) -> int:
    """Choose the smallest indexed declaration containing a reference site."""
    containing = [
        (end_line - start_line, symbol_id)
        for start_line, end_line, symbol_id in intervals
        if start_line <= line <= end_line
    ]
    return min(containing)[1] if containing else file_id


def _reference_edge(
    source_id: int,
    target_id: int,
    *,
    kind: str,
    site: list[int] | None,
    confidence: float,
    provenance: str,
) -> dict[str, Any]:
    """Create one serialisable graph row with complete evidence metadata."""
    return {
        "source_id": source_id,
        "target_id": target_id,
        "kind": kind,
        "site": site,
        "confidence": confidence,
        "provenance": provenance,
    }


def _call_edges(
    source_id: int,
    target_id: int,
    *,
    site: list[int] | None,
    confidence: float,
    provenance: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Materialise a precise call and its broad reference counterpart."""
    return (
        _reference_edge(
            source_id,
            target_id,
            kind="calls",
            site=site,
            confidence=confidence,
            provenance=provenance,
        ),
        _reference_edge(
            source_id,
            target_id,
            kind="references",
            site=site,
            confidence=confidence,
            provenance=provenance,
        ),
    )


def _deduplicate_edges(edges: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep the highest-confidence row for each stable graph edge key."""
    unique: dict[tuple[int, int, str], dict[str, Any]] = {}
    for edge in edges:
        key = (edge["source_id"], edge["target_id"], edge["kind"])
        current = unique.get(key)
        if current is None or edge["confidence"] > current["confidence"]:
            unique[key] = edge
    return list(unique.values())


def _receiver_type(
    receiver: str, scope: dict[str, str], owner: str, table: TypeTable
) -> str | None:
    """Resolve a receiver expression to a type name, or None.

    Deliberately gives up rather than guessing: a call chain needs a type
    checker, and a wrong edge is worse than a missing one.
    """
    if not receiver or receiver == "this":
        return owner
    if not receiver.isidentifier():
        return None
    declared = scope.get(receiver) or table.fields.get(owner, {}).get(receiver)
    if declared:
        return declared
    # In a static call like `Foo.bar()` the receiver is itself the type name
    return receiver if receiver[:1].isupper() else None
