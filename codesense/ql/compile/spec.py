"""Query specs: the compiler's intermediate representation.

Language compiles into one of these first (that step needs an LLM and lives
in `codesense.llm`), and `planner` then orders it into an execution plan.
The layer earns its place by being **hand-writable**: debugging need not go
through a model every time.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from codesense.ql.satisfiers import AnnotationSatisfier, LexicalSatisfier, ModifierSatisfier
from codesense.ql.unit import QueryUnit, Term

__all__ = ["GraphConstraint", "QuerySpec", "normalise_hops", "normalise_kinds"]

#: Default hop range for a graph constraint. The more hops, the weaker the
#: conclusion that two things are related.
DEFAULT_HOPS = (1, 2)

#: The model's vocabulary of kinds, mapped onto the index's.
#:
#: **A genuine impedance mismatch.** A model saying "class" means interfaces
#: and enums too, while the index treats `interface` as its own kind.
#: Without the mapping results vanish silently: on netty's zero-copy query
#: `FileRegion` is an interface, `kind=class` discarded it, and R@100 fell
#: from 50% to 25%.
KIND_ALIASES: dict[str, tuple[str, ...]] = {
    "class": ("class", "interface", "enum", "record", "annotation_type"),
    "type": ("class", "interface", "enum", "record", "annotation_type"),
    "interface": ("interface",),
    "enum": ("enum",),
    "record": ("record",),
    "method": ("method", "constructor"),
    "function": ("method", "constructor"),
    "constructor": ("constructor",),
    "field": ("field",),
    "variable": ("field",),
    "annotation": ("annotation_type",),
}


def normalise_kinds(raw: object) -> tuple[str, ...]:
    """Map the model's kinds onto the index's, passing unknown ones through."""
    if not raw:
        return ()
    values = [raw] if isinstance(raw, str) else list(raw)
    found: dict[str, None] = {}
    for item in values:
        if not isinstance(item, str):
            continue
        for kind in KIND_ALIASES.get(item.strip().lower(), (item.strip().lower(),)):
            found.setdefault(kind, None)
    return tuple(found)


def normalise_hops(raw: object) -> tuple[int, int]:
    """Coerce whatever the model gave into a valid closed interval."""
    if isinstance(raw, int):
        return (raw, raw) if raw >= 0 else DEFAULT_HOPS
    try:
        values = [int(v) for v in raw]  # type: ignore[union-attr]
    except (TypeError, ValueError):
        return DEFAULT_HOPS
    if not values:
        return DEFAULT_HOPS
    if len(values) == 1:
        values = [1, values[0]]
    low, high = max(values[0], 0), max(values[1], 0)
    return (low, high) if low <= high else (high, low)


@dataclass(frozen=True, slots=True)
class GraphConstraint:
    """A graph constraint between two units.

    The direction is **semantic** -- who calls whom. Which side execution
    actually starts from is the planner's decision, because that is a cost
    question rather than a meaning one.
    """

    src: str
    dst: str
    edge: tuple[str, ...] = ("calls", "contains")
    hops: tuple[int, int] = (1, 2)

    def __post_init__(self) -> None:
        """Specs come from an LLM, so the invariants defend themselves.

        Models really do return `hops: [2]` or even `[]`, and using that
        directly explodes with an IndexError somewhere far from the cause.
        """
        object.__setattr__(self, "hops", normalise_hops(self.hops))
        if not self.edge:
            object.__setattr__(self, "edge", ("calls", "contains"))


@dataclass(frozen=True, slots=True)
class QuerySpec:
    """The complete structure of one query."""

    query: str
    units: tuple[QueryUnit, ...]
    graph: tuple[GraphConstraint, ...] = ()
    concept: str = ""
    kinds: tuple[str, ...] = ()
    limit: int | None = None

    def __post_init__(self) -> None:
        if not self.units:
            raise ValueError("a query spec needs at least one unit")
        names = [unit.name for unit in self.units]
        if len(names) != len(set(names)):
            raise ValueError(f"duplicate unit names: {names}")
        known = set(names)
        for constraint in self.graph:
            unknown = {constraint.src, constraint.dst} - known
            if unknown:
                raise ValueError(f"graph constraint names unknown units: {sorted(unknown)}")

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> QuerySpec:
        """Build from the JSON an LLM produced. **Tolerant but never guessing**:
        a malformed structure raises."""
        units = tuple(_unit(item) for item in payload.get("units", ()))
        graph = tuple(
            GraphConstraint(
                src=str(item["src"]),
                dst=str(item["dst"]),
                edge=tuple(item.get("edge") or ("calls", "contains")),
                hops=normalise_hops(item.get("hops")),
            )
            for item in payload.get("graph", ())
            if isinstance(item, dict) and "src" in item and "dst" in item
        )
        return cls(
            query=str(payload.get("query", "")),
            units=units,
            graph=graph,
            concept=str(payload.get("concept") or ""),
            kinds=normalise_kinds(payload.get("kinds")),
            limit=payload.get("limit"),
        )


def _unit(payload: dict[str, Any]) -> QueryUnit:
    satisfiers: list[object] = []
    terms = [t for t in payload.get("terms", ()) if isinstance(t, str) and t]
    if terms:
        satisfiers.append(LexicalSatisfier(terms=tuple(Term(t.lower()) for t in terms)))
    annotations = [a for a in payload.get("annotations", ()) if isinstance(a, str) and a]
    if annotations:
        satisfiers.append(AnnotationSatisfier(names=tuple(annotations)))
    modifiers = [m for m in payload.get("modifiers", ()) if isinstance(m, str) and m]
    if modifiers:
        satisfiers.append(ModifierSatisfier(modifiers=tuple(modifiers)))
    if not satisfiers:
        raise ValueError(f"unit {payload.get('name')!r} has no executable condition")
    return QueryUnit(
        name=str(payload["name"]),
        concept=str(payload.get("concept") or ""),
        satisfiers=tuple(satisfiers),
    )


def units_named(spec: QuerySpec, names: Sequence[str]) -> tuple[QueryUnit, ...]:
    wanted = set(names)
    return tuple(unit for unit in spec.units if unit.name in wanted)
