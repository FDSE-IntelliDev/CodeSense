"""`hop` 与 `reach`：图上的路径算子。

`hop` 是整套设计的核心算子——它不是过滤器，是**把分散在多处的语义连成一个
整体**。「performance 相关的代码调用了 disk 相关的代码」这句话里两个单元
落在不同元素上，靠图连起来。

设计依据见 ``docs/design/05-operators.md`` 与 ``docs/design/10-graph.md``。
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

#: 路径数上限。**必须有默认值**：路径数随跳数指数增长，
#: 实测修复后平均度约 4.4，``hops=(1,5)`` 就是 2133 条路径/起点。
DEFAULT_MAX_PATHS = 10_000

#: 单节点度数上限。超过它的节点不再展开——工具方法（`Result.success`、
#: `Assert.judge`）会被所有人调用，经过它们的路径几乎没有信息量，
#: 却会主导路径枚举。
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
    """两个片段之间满足图约束的路径。

    参数取名 ``hops`` / ``direction`` 而不是设计初稿的 ``len`` / ``dir``：
    后者遮蔽内置名，函数体内就用不了 `len()` 了。

    ``hops`` 是**闭区间**不是上限——``hops=(2, 2)`` 表示恰好两跳，
    「间接调用而非直接调用」是真实意图。给整数表示恰好该跳数。

    返回的片段含路径上的全部节点、边，以及路径见证。
    """
    lo, hi = _normalise_hops(hops)
    kinds = _normalise_kinds(edge)
    if direction not in _DIRECTIONS:
        raise ValueError(f"direction 只能是 {_DIRECTIONS} 之一，收到 {direction!r}")
    if not src or not dst:
        return Frag()

    # 先从终点反向算「到 dst 的最短跳数」。只算距离不留路径，很便宜，
    # 但能让正向枚举把「再走也到不了」的分支整枝剪掉。
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
            "hop 触到 max_paths=%s 被截断，结果不完整；收紧 hops 上界或加 avoid 可以让它完整",
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
    """从这里出发能到哪。没有 ``dst``——它在探索，不在验证约束。

    只返回节点，不返回路径：探索场景下路径见证的开销通常不值得。
    """
    lo, hi = _normalise_hops(hops)
    kinds = _normalise_kinds(edge)
    if direction not in _DIRECTIONS:
        raise ValueError(f"direction 只能是 {_DIRECTIONS} 之一，收到 {direction!r}")

    dist = _distances(ctx.edges, src.nodes.keys(), direction, hi, kinds, min_confidence)
    found = {sid for sid, d in dist.items() if lo <= d <= hi}
    return Frag(nodes=ctx.symbols.get_many(found))


class _PathFinder:
    """带剪枝的路径枚举。

    用显式栈的 DFS 而不是递归：路径可以很长，递归会撞 Python 的栈深度限制。
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
        # 栈元素：(当前节点, 到这里的节点序列, 到这里的边序列)
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
        """hub 限流。起点不受限——调用方明确要求从那里出发。"""
        if self._max_degree is None:
            return False
        return self._edges.degree(node, kinds=self._kinds) > self._max_degree

    def _worth_exploring(self, node: int, depth: int) -> bool:
        """剩下的跳数还够不够走到 dst。

        ``to_dst`` 是**最短**距离，所以这个剪枝是可采纳的——
        不会砍掉任何真实存在的合法路径。
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
    """从 seeds 出发的最短跳数（BFS）。只算距离，不留路径。"""
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
    """沿指定方向走一步，返回 (走过的边, 到达的节点)。"""
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
        raise ValueError(f"hops 必须是非负的闭区间，收到 {hops!r}")
    return lo, hi


def _normalise_kinds(edge: str | Sequence[str]) -> tuple[str, ...] | None:
    if isinstance(edge, str):
        return (edge,)
    kinds = tuple(edge)
    # 空序列表示不限类型，而不是"什么都不匹配"——后者没有使用场景。
    return kinds or None


def _to_frag(paths: Sequence[Path], src: Frag, dst: Frag, ctx: EvalContext) -> Frag:
    """把路径集合装配成片段，并把两端片段的证据带过来。

    中间节点没有单元证据——它们在结果里是因为**结构**，不是因为命中了什么词。
    """
    if not paths:
        return Frag()

    # 符号表里查不到的节点会让路径不完整。这种路径整条丢掉——
    # 只留一半的路径不是「部分结果」，是错误结果。
    known = ctx.symbols.get_many({sid for p in paths for sid in p.nodes})
    keep = tuple(p for p in paths if all(sid in known for sid in p.nodes))
    if not keep:
        return Frag()

    # 节点只取还留在路径上的，否则会剩下一堆既无边也无路径的孤立节点。
    nodes = {sid: known[sid] for p in keep for sid in p.nodes}

    evidence: dict[int, Evidence] = {}
    for source in (src, dst):
        for sid, ev in source.evidence.items():
            if sid not in nodes:
                continue
            # 一个节点可能同时是某条路径的起点和另一条的终点，两边的理由都得留。
            existing = evidence.get(sid)
            evidence[sid] = ev if existing is None else existing.merge(ev)

    return Frag(
        nodes=nodes,
        edges={e.key: e for p in keep for e in p.edges},
        evidence=evidence,
        witnesses=keep,
    )
