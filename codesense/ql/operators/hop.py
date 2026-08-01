"""`hop` and `reach`: path operators over the graph.

`hop` is the operator the design turns on. It is not a filter but the thing
that **connects semantics scattered across several elements**: in
"performance code calls disk code", the two units land on different elements
and the graph is what joins them.

Design: ``docs/design/05-operators.md`` and ``docs/design/10-graph.md``.
"""

from __future__ import annotations

import logging
from collections import deque
from collections.abc import Iterator, Mapping, Sequence

from codesense.ql.context import EvalContext
from codesense.ql.frag import Edge, Evidence, Frag, Path
from codesense.ql.store.base import EdgeStore

__all__ = ["DEFAULT_MAX_DEGREE", "DEFAULT_MAX_PATHS", "hop", "reach"]

_log = logging.getLogger(__name__)

#: Ceiling on paths. **A default is mandatory**: path count grows
#: exponentially with hops, and at the measured average degree of about 4.4,
#: ``hops=(1,5)`` means roughly 2133 paths per start node.
DEFAULT_MAX_PATHS = 10_000

#: Per-node degree ceiling. Nodes above it are not expanded -- utility
#: methods such as `Result.success` and `Assert.judge` are called by
#: everything, so paths through them carry almost no information while
#: dominating the enumeration.
DEFAULT_MAX_DEGREE = 64

_FORWARD = "forward"
_BACKWARD = "backward"
_ANY = "any"
_DIRECTIONS = (_FORWARD, _BACKWARD, _ANY)


def hop(
    src: Frag,
    dst: Frag,
    ctx: EvalContext,
    *,
    edge: str | Sequence[str] = "calls",
    direction: str = _FORWARD,
    hops: int | tuple[int, int] = (1, 3),
    via: Frag | None = None,
    avoid: Frag | None = None,
    min_confidence: float = 0.0,
    max_paths: int | None = DEFAULT_MAX_PATHS,
    max_degree: int | None = DEFAULT_MAX_DEGREE,
) -> Frag:
    """Paths between two fragments that satisfy a graph constraint.

    The parameters are ``hops`` and ``direction`` rather than the draft's
    ``len`` and ``dir``, which shadow builtins and would make `len()`
    unusable inside the body.

    ``hops`` is a **closed interval**, not a ceiling: ``hops=(2, 2)`` means
    exactly two hops, and "indirectly rather than directly called" is a real
    intent. An integer means exactly that many hops.

    The returned fragment carries every node and edge on the paths, plus the
    path witnesses themselves.
    """
    lo, hi = _normalise_hops(hops)
    kinds = _normalise_kinds(edge)
    if direction not in _DIRECTIONS:
        raise ValueError(f"direction must be one of {_DIRECTIONS}, got {direction!r}")
    if not src or not dst:
        return Frag()

    # First compute the shortest distance to dst backwards from the targets.
    # Distances only, no paths, so it is cheap -- but it lets the forward
    # enumeration prune whole branches that can never reach dst.
    to_dst = _distances(ctx.edges, dst.nodes.keys(), _flip(direction), hi, kinds, min_confidence)

    finder = _PathFinder(
        edges=ctx.edges,
        targets=frozenset(dst.nodes),
        to_dst=to_dst,
        lo=lo,
        hi=hi,
        kinds=kinds,
        direction=direction,
        min_confidence=min_confidence,
        avoid=frozenset(avoid.nodes) if avoid else frozenset(),
        via=frozenset(via.nodes) if via else None,
        max_degree=max_degree,
    )
    paths = finder.run(src.nodes.keys(), max_paths)
    if finder.truncated:
        _log.warning(
            "hop hit max_paths=%s and truncated; the result is incomplete. "
            "Tightening the hops ceiling or adding avoid would complete it",
            max_paths,
        )
    return _to_frag(paths, src, dst, ctx)


def reach(
    src: Frag,
    ctx: EvalContext,
    *,
    edge: str | Sequence[str] = "calls",
    direction: str = _FORWARD,
    hops: int | tuple[int, int] = (1, 3),
    min_confidence: float = 0.0,
) -> Frag:
    """Where you can get to from here. No ``dst`` -- this explores rather
    than verifying a constraint.

    Returns nodes only, not paths: witnesses rarely pay for themselves while
    exploring.
    """
    lo, hi = _normalise_hops(hops)
    kinds = _normalise_kinds(edge)
    if direction not in _DIRECTIONS:
        raise ValueError(f"direction must be one of {_DIRECTIONS}, got {direction!r}")

    dist = _distances(ctx.edges, src.nodes.keys(), direction, hi, kinds, min_confidence)
    found = {sid for sid, d in dist.items() if lo <= d <= hi}
    return Frag(nodes=ctx.symbols.get_many(found))


class _PathFinder:
    """Path enumeration with pruning.

    DFS on an explicit stack rather than recursion: paths can be long and
    recursion would hit Python's depth limit.
    """

    def __init__(
        self,
        *,
        edges: EdgeStore,
        targets: frozenset[int],
        to_dst: Mapping[int, int],
        lo: int,
        hi: int,
        kinds: tuple[str, ...] | None,
        direction: str,
        min_confidence: float,
        avoid: frozenset[int],
        via: frozenset[int] | None,
        max_degree: int | None,
    ) -> None:
        self._edges = edges
        self._targets = targets
        self._to_dst = to_dst
        self._lo = lo
        self._hi = hi
        self._kinds = kinds
        self._direction = direction
        self._min_confidence = min_confidence
        self._avoid = avoid
        self._via = via
        self._max_degree = max_degree
        self.truncated = False

    def run(self, starts: Sequence[int] | frozenset[int], max_paths: int | None) -> list[Path]:
        found: list[Path] = []
        for start in sorted(starts):
            if start in self._avoid or not self._worth_exploring(start, 0):
                continue
            for path in self._walk(start):
                found.append(path)
                if max_paths is not None and len(found) >= max_paths:
                    self.truncated = True
                    return found
        return found

    def _walk(self, start: int) -> Iterator[Path]:
        # Stack entries: (node, nodes so far, edges so far)
        stack: list[tuple[int, tuple[int, ...], tuple[Edge, ...]]] = [(start, (start,), ())]
        while stack:
            node, nodes, path_edges = stack.pop()
            depth = len(path_edges)
            if depth >= self._lo and node in self._targets and self._via_ok(nodes):
                yield Path(nodes=nodes, edges=path_edges)
            if depth >= self._hi:
                continue
            if depth > 0 and self._too_busy(node):
                continue
            for edge, nxt in self._neighbours(node):
                if nxt in nodes or nxt in self._avoid:
                    continue
                if not self._worth_exploring(nxt, depth + 1):
                    continue
                stack.append((nxt, (*nodes, nxt), (*path_edges, edge)))

    def _too_busy(self, node: int) -> bool:
        """Hub throttling. Start nodes are exempt: the caller asked for them."""
        if self._max_degree is None:
            return False
        return self._edges.degree(node, kinds=self._kinds) > self._max_degree

    def _worth_exploring(self, node: int, depth: int) -> bool:
        """Whether the remaining hops can still reach dst.

        ``to_dst`` holds **shortest** distances, so the prune is admissible
        and discards no path that really exists.
        """
        remaining = self._to_dst.get(node)
        return remaining is not None and depth + remaining <= self._hi

    def _via_ok(self, nodes: tuple[int, ...]) -> bool:
        return self._via is None or bool(self._via.intersection(nodes))

    def _neighbours(self, node: int) -> list[tuple[Edge, int]]:
        return _neighbours(self._edges, node, self._direction, self._kinds, self._min_confidence)


def _distances(
    edges: EdgeStore,
    seeds: Sequence[int] | frozenset[int],
    direction: str,
    limit: int,
    kinds: tuple[str, ...] | None,
    min_confidence: float,
) -> dict[int, int]:
    """Shortest hop counts from the seeds, by BFS. Distances only, no paths."""
    dist = {sid: 0 for sid in seeds}
    queue: deque[int] = deque(dist)
    while queue:
        node = queue.popleft()
        depth = dist[node]
        if depth >= limit:
            continue
        for _edge, nxt in _neighbours(edges, node, direction, kinds, min_confidence):
            if nxt not in dist:
                dist[nxt] = depth + 1
                queue.append(nxt)
    return dist


def _neighbours(
    edges: EdgeStore,
    node: int,
    direction: str,
    kinds: tuple[str, ...] | None,
    min_confidence: float,
) -> list[tuple[Edge, int]]:
    """One step in the given direction, returning (edge taken, node reached)."""
    out: list[tuple[Edge, int]] = []
    if direction in (_FORWARD, _ANY):
        for e in edges.out_edges(node, kinds=kinds, min_confidence=min_confidence):
            out.append((e, e.target_id))
    if direction in (_BACKWARD, _ANY):
        for e in edges.in_edges(node, kinds=kinds, min_confidence=min_confidence):
            out.append((e, e.source_id))
    return out


def _flip(direction: str) -> str:
    if direction == _FORWARD:
        return _BACKWARD
    if direction == _BACKWARD:
        return _FORWARD
    return _ANY


def _normalise_hops(hops: int | tuple[int, int]) -> tuple[int, int]:
    lo, hi = (hops, hops) if isinstance(hops, int) else hops
    if lo < 0 or hi < lo:
        raise ValueError(f"hops must be a non-negative closed interval, got {hops!r}")
    return lo, hi


def _normalise_kinds(edge: str | Sequence[str]) -> tuple[str, ...] | None:
    if isinstance(edge, str):
        return (edge,)
    kinds = tuple(edge)
    # An empty sequence means any kind, not "match nothing" -- the latter has
    # no use case.
    return kinds or None


def _to_frag(paths: Sequence[Path], src: Frag, dst: Frag, ctx: EvalContext) -> Frag:
    """Assemble paths into a fragment, carrying over the evidence from both
    endpoints.

    Intermediate nodes carry no unit evidence: they are in the result because
    of **structure**, not because they matched a term.
    """
    if not paths:
        return Frag()

    # A node missing from the symbol store leaves a path incomplete, and such
    # paths are dropped whole -- half a path is not a partial result, it is a
    # wrong one.
    known = ctx.symbols.get_many({sid for p in paths for sid in p.nodes})
    keep = tuple(p for p in paths if all(sid in known for sid in p.nodes))
    if not keep:
        return Frag()

    # Take nodes only from surviving paths, or orphans with neither edges nor
    # witnesses are left behind.
    nodes = {sid: known[sid] for p in keep for sid in p.nodes}

    evidence: dict[int, Evidence] = {}
    for source in (src, dst):
        for sid, ev in source.evidence.items():
            if sid not in nodes:
                continue
            # A node can start one path and end another; keep both reasons.
            existing = evidence.get(sid)
            evidence[sid] = ev if existing is None else existing.merge(ev)

    return Frag(
        nodes=nodes,
        edges={e.key: e for p in keep for e in p.edges},
        evidence=evidence,
        witnesses=keep,
    )
