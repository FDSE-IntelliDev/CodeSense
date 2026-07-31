"""Edges: containment, and calls resolved as far as the AST allows.

Two kinds, and the confidence gap between them is the honest part:

    contains   derived from the container field. A fact, confidence 1.0, and
               the cheapest structural win available -- materialising it takes
               orphan symbols from 33% to 2%.
    calls      resolved through the receiver's declared type where possible,
               by name where not. **Never 1.0**: this is not a real call
               graph, and pretending otherwise would let `hop` return paths
               that do not exist.

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

from codesense.lang.base import Declaration

__all__ = ["GraphBuilder", "TypeTable"]

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


class GraphBuilder:
    """Collects what edges need, then builds them once the scan is complete."""

    def __init__(self, container_kinds: frozenset[str], callable_kinds: frozenset[str]) -> None:
        self._container_kinds = container_kinds
        self._callable_kinds = callable_kinds
        self.types = TypeTable()
        self.stats = GraphStats()
        self._by_qualified: dict[str, int] = {}
        self._by_simple_name: dict[str, list[int]] = defaultdict(list)
        self._containers: list[tuple[int, str]] = []
        self._invocations: list[tuple[int, str, dict[str, str], Sequence[Any]]] = []

    def observe(self, declaration: Declaration, symbol_id: int) -> None:
        """Record what this declaration contributes to the graph."""
        owner = declaration.container.split(".")[-1]
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
            qualified = (
                f"{declaration.container}.{declaration.name}"
                if declaration.container
                else declaration.name
            )
            self._by_qualified.setdefault(qualified, symbol_id)

    def build(self) -> list[dict[str, Any]]:
        edges = self._contains()
        self.stats.contains = len(edges)
        edges += self._calls()
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
                    {
                        "source_id": caller,
                        "target_id": target,
                        "kind": "calls",
                        "confidence": round(confidence, 4),
                        "provenance": provenance,
                    }
                    for target in targets
                    if target != caller
                )
        self.stats.calls = len(edges)
        return edges


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
