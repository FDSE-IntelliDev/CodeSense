"""Code fragments and the elements they contain.

Only one kind of thing flows between operators: a `Frag`, an annotated
subgraph of the codebase. Design: ``docs/design/03-data-model.md``.

This module holds data and pure operations on it and does **no IO** -- no
database, no files, no config. Where fragments come from (the index, graph
traversal) is ``codesense.ql.store``'s job.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import ClassVar, TypeVar

__all__ = [
    "Edge",
    "EdgeKey",
    "Element",
    "Evidence",
    "Frag",
    "Path",
    "UnitHit",
    "Verdict",
]

#: Deduplication key for edges: (source, target, kind). Edges of different
#: kinds between the same pair are independent.
EdgeKey = tuple[int, int, str]

_T = TypeVar("_T")


@dataclass(frozen=True, slots=True)
class Element:
    """One element of the codebase.

    ``frozen`` because elements travel between operators, and any in-place
    change would hand tampered data back upstream. Extra information belongs
    in `Evidence`, not on the element.
    """

    symbol_id: int
    name: str
    kind: str
    file: str
    span: tuple[int, int]
    signature: str = ""
    container: str = ""
    language: str = ""
    doc: str = ""
    modifiers: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class Edge:
    """A directed relation between two elements.

    ``confidence`` and ``provenance`` are not decoration: dynamic dispatch,
    reflection and multiple implementations all make edges uncertain, so
    queries must be able to filter by confidence and results must be able to
    say where an edge came from.
    """

    source_id: int
    target_id: int
    kind: str
    site: tuple[int, int] | None = None
    confidence: float = 1.0
    provenance: str = ""

    @property
    def key(self) -> EdgeKey:
        return (self.source_id, self.target_id, self.kind)


@dataclass(frozen=True, slots=True)
class Path:
    """One path. Stores symbol ids only; the elements live in `Frag.nodes`.

    Otherwise a single element would be duplicated across hundreds of paths.
    """

    nodes: tuple[int, ...]
    edges: tuple[Edge, ...]

    def __len__(self) -> int:
        return len(self.edges)


@dataclass(frozen=True, slots=True)
class UnitHit:
    """One piece of evidence that a unit was satisfied.

    ``signal`` records which kind of signal did it. A unit can be satisfied
    by lexical matching, annotations, structural position, modifiers or
    semantic similarity, and lexical is the weakest of them.
    """

    unit: str
    signal: str
    detail: str
    field: str = ""
    score: float = 1.0
    span: tuple[int, int] | None = None


@dataclass(frozen=True, slots=True)
class Verdict:
    """One verdict, such as `intent`'s LLM judgement. A reason is mandatory."""

    source: str
    label: str
    reason: str = ""
    score: float = 1.0


@dataclass(frozen=True, slots=True)
class Evidence:
    """Why this element is in the result.

    Three constraints (``docs/design/03-data-model.md``):

    1. Append only, never overwrite -- explainability comes from the whole
       causal chain.
    2. Takes no part in equality, or ``a | a != a``. Enforced by `Frag.__eq__`.
    3. Must be serialisable -- it gets written out for people to inspect later.
    """

    unit_hits: tuple[UnitHit, ...] = ()
    verdicts: tuple[Verdict, ...] = ()

    #: Signal used by the summary evidence the `unit` operator appends. It
    #: carries the total after the unit's `combine` strategy has been applied
    #: and is the **authoritative** score for that unit.
    COMBINED: ClassVar[str] = "combined"

    @property
    def scores(self) -> Mapping[str, float]:
        """Scores per unit. Derived, never set directly.

        When summary evidence (``signal == "combined"``) exists it wins and
        nothing is summed. Summing is only one of three combine strategies,
        and hard-coding it here would contradict whatever the unit declared
        as well as double-count the summary itself.
        """
        combined: dict[str, float] = {}
        raw: dict[str, float] = {}
        for hit in self.unit_hits:
            if hit.signal == self.COMBINED:
                combined[hit.unit] = max(combined.get(hit.unit, 0.0), hit.score)
            else:
                raw[hit.unit] = raw.get(hit.unit, 0.0) + hit.score
        return MappingProxyType({**raw, **combined})

    def merge(self, other: Evidence) -> Evidence:
        """Merge two sets of evidence, order-preserving and deduplicated.

        Intersection must call this: when an element satisfies both sides,
        **both reasons have to survive**.
        """
        return Evidence(
            unit_hits=_dedup(self.unit_hits + other.unit_hits),
            verdicts=_dedup(self.verdicts + other.verdicts),
        )


def _dedup(items: tuple[_T, ...]) -> tuple[_T, ...]:
    """Order-preserving deduplication. Evidence items are frozen and hashable."""
    seen: set[_T] = set()
    out: list[_T] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return tuple(out)


@dataclass(frozen=True, slots=True, eq=False)
class Frag:
    """A fragment of the codebase: nodes, the edges among them, and the
    evidence for each node.

    The only type flowing between operators; every operator is
    ``Frag -> Frag``.

    Equality considers **nodes and edges only**, not evidence or path
    witnesses, or ``a | a != a``. Fragments are unhashable -- they are
    containers and have no business being dictionary keys.
    """

    nodes: Mapping[int, Element] = field(default_factory=dict)
    edges: Mapping[EdgeKey, Edge] = field(default_factory=dict)
    evidence: Mapping[int, Evidence] = field(default_factory=dict)
    witnesses: tuple[Path, ...] = ()

    __hash__ = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        # Freeze the mappings: frozen blocks rebinding, not mutation.
        object.__setattr__(self, "nodes", MappingProxyType(dict(self.nodes)))
        object.__setattr__(self, "edges", MappingProxyType(dict(self.edges)))
        object.__setattr__(self, "evidence", MappingProxyType(dict(self.evidence)))
        dangling = {
            side for key in self.edges for side in (key[0], key[1]) if side not in self.nodes
        }
        if dangling:
            raise ValueError(f"edges point at nodes outside the fragment: {sorted(dangling)}")

    # ---- basics ----

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Frag):
            return NotImplemented
        return dict(self.nodes) == dict(other.nodes) and dict(self.edges) == dict(other.edges)

    def __len__(self) -> int:
        return len(self.nodes)

    def __iter__(self) -> Iterator[Element]:
        return iter(self.nodes.values())

    def __contains__(self, symbol_id: object) -> bool:
        return symbol_id in self.nodes

    def __bool__(self) -> bool:
        return bool(self.nodes)

    def evidence_for(self, symbol_id: int) -> Evidence:
        """Evidence for one node; an empty `Evidence` if there is none."""
        return self.evidence.get(symbol_id, Evidence())

    # ---- fragment algebra ----

    def __or__(self, other: Frag) -> Frag:
        """Union: nodes, edges and evidence all merge."""
        return Frag(
            nodes={**self.nodes, **other.nodes},
            edges={**self.edges, **other.edges},
            evidence=_merge_evidence(self.evidence, other.evidence),
            witnesses=_dedup(self.witnesses + other.witnesses),
        )

    def __and__(self, other: Frag) -> Frag:
        """Intersection: nodes intersect, edges survive if both ends do, and
        **evidence merges**.

        The evidence merge is the easiest part to miss, and missing it
        produces results containing elements nobody can explain.
        """
        kept = self.nodes.keys() & other.nodes.keys()
        return _induced(
            nodes={sid: self.nodes[sid] for sid in kept},
            edges={**self.edges, **other.edges},
            evidence=_merge_evidence(self.evidence, other.evidence),
            witnesses=self.witnesses + other.witnesses,
        )

    def __sub__(self, other: Frag) -> Frag:
        """Difference: drop other's nodes and their edges, keep self's evidence."""
        kept = self.nodes.keys() - other.nodes.keys()
        return _induced(
            nodes={sid: self.nodes[sid] for sid in kept},
            edges=self.edges,
            evidence=self.evidence,
            witnesses=self.witnesses,
        )

    # ---- projections ----

    def induced(self, symbol_ids: Iterable[int]) -> Frag:
        """The subgraph induced by a subset of nodes."""
        kept = {sid for sid in symbol_ids if sid in self.nodes}
        return _induced(
            nodes={sid: self.nodes[sid] for sid in kept},
            edges=self.edges,
            evidence=self.evidence,
            witnesses=self.witnesses,
        )

    def roots(self) -> Frag:
        """Nodes with in-degree zero: path starts, nothing points at them."""
        has_incoming = {key[1] for key in self.edges}
        return self.induced(self.nodes.keys() - has_incoming)

    def leaves(self) -> Frag:
        """Nodes with out-degree zero: path ends, they point at nothing."""
        has_outgoing = {key[0] for key in self.edges}
        return self.induced(self.nodes.keys() - has_outgoing)

    def only_nodes(self) -> Frag:
        """Drop edges and witnesses, degrading to a plain set of nodes.

        An escape hatch: one line back to set semantics when the fragment
        structure is in the way.
        """
        return Frag(nodes=self.nodes, evidence=self.evidence)


def _merge_evidence(
    left: Mapping[int, Evidence], right: Mapping[int, Evidence]
) -> dict[int, Evidence]:
    merged = dict(left)
    for symbol_id, ev in right.items():
        existing = merged.get(symbol_id)
        merged[symbol_id] = ev if existing is None else existing.merge(ev)
    return merged


def _induced(
    *,
    nodes: Mapping[int, Element],
    edges: Mapping[EdgeKey, Edge],
    evidence: Mapping[int, Evidence],
    witnesses: tuple[Path, ...],
) -> Frag:
    """Trim dangling edges and paths so `Frag`'s invariant holds."""
    kept_edges = {key: edge for key, edge in edges.items() if key[0] in nodes and key[1] in nodes}
    kept_paths = tuple(p for p in witnesses if all(n in nodes for n in p.nodes))
    return Frag(
        nodes=nodes,
        edges=kept_edges,
        evidence={sid: ev for sid, ev in evidence.items() if sid in nodes},
        witnesses=_dedup(kept_paths),
    )
