"""`hop` / `reach` 的单元测试。

用一条链 1→2→3→4 加一条旁支 1→5 作基本地形，
需要更复杂拓扑的用例各自另建。
"""

from __future__ import annotations

import logging

import pytest

from codesense.ql import Edge, Element, Evidence, Frag, UnitHit
from codesense.ql.context import EvalContext
from codesense.ql.operators import hop, reach
from codesense.ql.store import (
    InMemoryEdgeStore,
    InMemoryExpansionTable,
    InMemoryPostingIndex,
    InMemorySymbolStore,
)

CHAIN = (Edge(1, 2, "calls"), Edge(2, 3, "calls"), Edge(3, 4, "calls"), Edge(1, 5, "calls"))


def make_element(symbol_id: int) -> Element:
    return Element(
        symbol_id=symbol_id, name=f"s{symbol_id}", kind="method", file="A.java", span=(1, 2)
    )


def make_context(
    edges: tuple[Edge, ...] = CHAIN, *, symbol_ids: range | tuple[int, ...] = range(1, 8)
) -> EvalContext:
    return EvalContext(
        symbols=InMemorySymbolStore([make_element(i) for i in symbol_ids]),
        postings=InMemoryPostingIndex({}, total_symbols=100),
        expansion=InMemoryExpansionTable({}),
        edges=InMemoryEdgeStore(edges),
    )


def frag(*symbol_ids: int, evidence: dict[int, Evidence] | None = None) -> Frag:
    return Frag(
        nodes={i: make_element(i) for i in symbol_ids},
        evidence=evidence or {},
    )


class TestHopBasics:
    def test_找到直接调用(self) -> None:
        result = hop(frag(1), frag(2), make_context())
        assert [p.nodes for p in result.witnesses] == [(1, 2)]

    def test_找到间接调用(self) -> None:
        result = hop(frag(1), frag(3), make_context())
        assert result.witnesses[0].nodes == (1, 2, 3)

    def test_返回路径上的全部节点和边(self) -> None:
        result = hop(frag(1), frag(3), make_context())
        assert set(result.nodes) == {1, 2, 3}
        assert set(result.edges) == {(1, 2, "calls"), (2, 3, "calls")}

    def test_够不着时返回空片段(self) -> None:
        assert not hop(frag(5), frag(4), make_context())

    def test_空的_src_或_dst_直接返回空(self) -> None:
        ctx = make_context()
        assert not hop(Frag(), frag(3), ctx)
        assert not hop(frag(1), Frag(), ctx)

    def test_路径的边数等于跳数(self) -> None:
        result = hop(frag(1), frag(4), make_context())
        assert len(result.witnesses[0]) == 3


class TestHopRange:
    def test_hops_是闭区间不是上限(self) -> None:
        """`hops=(2,2)` 表示恰好两跳——「间接调用而非直接调用」是真实意图。"""
        ctx = make_context()
        assert hop(frag(1), frag(2), ctx, hops=(2, 2)).witnesses == ()
        assert hop(frag(1), frag(3), ctx, hops=(2, 2)).witnesses != ()

    def test_整数表示恰好该跳数(self) -> None:
        ctx = make_context()
        assert not hop(frag(1), frag(3), ctx, hops=1)
        assert hop(frag(1), frag(3), ctx, hops=2)

    def test_上界之外的够不着(self) -> None:
        assert not hop(frag(1), frag(4), make_context(), hops=(1, 2))

    def test_非法区间报错(self) -> None:
        with pytest.raises(ValueError, match="非负的闭区间"):
            hop(frag(1), frag(2), make_context(), hops=(3, 1))

    def test_负数报错(self) -> None:
        with pytest.raises(ValueError, match="非负的闭区间"):
            hop(frag(1), frag(2), make_context(), hops=-1)


class TestHopDirection:
    def test_反向(self) -> None:
        result = hop(frag(3), frag(1), make_context(), direction="backward")
        assert result.witnesses[0].nodes == (3, 2, 1)

    def test_正向找不到反向能找到(self) -> None:
        ctx = make_context()
        assert not hop(frag(3), frag(1), ctx)
        assert hop(frag(3), frag(1), ctx, direction="backward")

    def test_any_走无向(self) -> None:
        """「这两块有没有关系」不关心方向。"""
        ctx = make_context()
        assert hop(frag(5), frag(2), ctx, direction="any", hops=(1, 3))

    def test_非法方向报错(self) -> None:
        with pytest.raises(ValueError, match="direction"):
            hop(frag(1), frag(2), make_context(), direction="sideways")


class TestHopConstraints:
    def test_avoid_排除中间节点(self) -> None:
        assert not hop(frag(1), frag(3), make_context(), avoid=frag(2))

    def test_avoid_排除起点(self) -> None:
        assert not hop(frag(1), frag(3), make_context(), avoid=frag(1))

    def test_via_要求路径必须经过(self) -> None:
        ctx = make_context()
        assert hop(frag(1), frag(4), ctx, via=frag(2))
        assert not hop(frag(1), frag(4), ctx, via=frag(5))

    def test_按边类型过滤(self) -> None:
        ctx = make_context((Edge(1, 2, "calls"), Edge(2, 3, "contains")))
        assert not hop(frag(1), frag(3), ctx, edge="calls")
        assert hop(frag(1), frag(3), ctx, edge=["calls", "contains"])

    def test_按置信度过滤_挡掉虚分派(self) -> None:
        ctx = make_context((Edge(1, 2, "calls", confidence=0.6),))
        assert hop(frag(1), frag(2), ctx, min_confidence=0.5)
        assert not hop(frag(1), frag(2), ctx, min_confidence=0.9)


class TestHopBrakes:
    def _wide(self) -> tuple[Edge, ...]:
        """1 经 20 个中转节点都能到 999，共 20 条路径。"""
        return tuple(
            e for i in range(100, 120) for e in (Edge(1, i, "calls"), Edge(i, 999, "calls"))
        )

    def test_max_paths_截断(self) -> None:
        ctx = make_context(self._wide(), symbol_ids=(1, 999, *range(100, 120)))
        assert len(hop(frag(1), frag(999), ctx, max_paths=5).witnesses) == 5

    def test_截断必须_log_而不是静默(self) -> None:
        """静默截断会让人以为「结果就这么多」。"""
        ctx = make_context(self._wide(), symbol_ids=(1, 999, *range(100, 120)))
        logger = logging.getLogger("codesense.ql.operators.hop")
        records: list[logging.LogRecord] = []
        handler = logging.Handler()
        handler.emit = records.append  # type: ignore[method-assign]
        logger.addHandler(handler)
        try:
            hop(frag(1), frag(999), ctx, max_paths=5)
        finally:
            logger.removeHandler(handler)
        assert any("max_paths" in r.getMessage() for r in records)

    def test_不截断时不_log(self) -> None:
        ctx = make_context(self._wide(), symbol_ids=(1, 999, *range(100, 120)))
        logger = logging.getLogger("codesense.ql.operators.hop")
        records: list[logging.LogRecord] = []
        handler = logging.Handler()
        handler.emit = records.append  # type: ignore[method-assign]
        logger.addHandler(handler)
        try:
            hop(frag(1), frag(999), ctx, max_paths=1000)
        finally:
            logger.removeHandler(handler)
        assert not records

    def test_hub_节点不再展开(self) -> None:
        """工具方法被所有人调用，经过它们的路径几乎没有信息量。"""
        hub = tuple(Edge(i, 500, "calls") for i in range(1, 30)) + (Edge(500, 999, "calls"),)
        ctx = make_context(hub, symbol_ids=(*range(1, 30), 500, 999))
        assert not hop(frag(1), frag(999), ctx, max_degree=10)
        assert hop(frag(1), frag(999), ctx, max_degree=100)

    def test_hub_限流不影响起点(self) -> None:
        hub = tuple(Edge(1, i, "calls") for i in range(100, 130)) + (Edge(100, 999, "calls"),)
        ctx = make_context(hub, symbol_ids=(1, 999, *range(100, 130)))
        assert hop(frag(1), frag(999), ctx, max_degree=5)

    def test_路径内不重复节点(self) -> None:
        ring = (Edge(1, 2, "calls"), Edge(2, 3, "calls"), Edge(3, 1, "calls"))
        ctx = make_context(ring, symbol_ids=(1, 2, 3))
        for path in hop(frag(1), frag(3), ctx, hops=(1, 6)).witnesses:
            assert len(set(path.nodes)) == len(path.nodes)


class TestHopEvidence:
    def test_两端的证据被带进结果(self) -> None:
        src = frag(1, evidence={1: Evidence((UnitHit("perf", "lexical", "async"),))})
        dst = frag(3, evidence={3: Evidence((UnitHit("disk", "lexical", "swap"),))})
        result = hop(src, dst, make_context())
        assert result.evidence_for(1).unit_hits[0].unit == "perf"
        assert result.evidence_for(3).unit_hits[0].unit == "disk"

    def test_中间节点没有单元证据(self) -> None:
        """它在结果里是因为结构，不是因为命中了什么词。"""
        src = frag(1, evidence={1: Evidence((UnitHit("perf", "lexical", "async"),))})
        result = hop(src, frag(3), make_context())
        assert result.evidence_for(2).unit_hits == ()

    def test_同时是起点和终点的节点合并两边证据(self) -> None:
        ctx = make_context((Edge(1, 2, "calls"), Edge(2, 1, "calls")), symbol_ids=(1, 2))
        both = frag(1, 2, evidence={1: Evidence((UnitHit("a", "lexical", "x"),))})
        other = frag(1, 2, evidence={1: Evidence((UnitHit("b", "lexical", "y"),))})
        units = {h.unit for h in hop(both, other, ctx).evidence_for(1).unit_hits}
        assert {"a", "b"} <= units

    def test_符号表里没有的节点不进结果(self) -> None:
        ctx = make_context(symbol_ids=(1, 2))
        assert not hop(frag(1), frag(3), ctx)


class TestReach:
    def test_从这里能到哪(self) -> None:
        assert set(reach(frag(1), make_context(), hops=(1, 2)).nodes) == {2, 3, 5}

    def test_默认不含起点自己(self) -> None:
        assert 1 not in reach(frag(1), make_context()).nodes

    def test_hops_下界为0时含起点(self) -> None:
        assert 1 in reach(frag(1), make_context(), hops=(0, 1)).nodes

    def test_只返回节点不返回路径(self) -> None:
        assert reach(frag(1), make_context()).witnesses == ()

    def test_反向可达(self) -> None:
        assert set(reach(frag(4), make_context(), direction="backward", hops=(1, 3)).nodes) == {
            1,
            2,
            3,
        }

    def test_非法方向报错(self) -> None:
        with pytest.raises(ValueError, match="direction"):
            reach(frag(1), make_context(), direction="sideways")
