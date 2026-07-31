"""`only` / `top` / `degree` 的单元测试。"""

from __future__ import annotations

import pytest

from codesense.ql import Edge, Element, Evidence, Frag, UnitHit
from codesense.ql.context import EvalContext
from codesense.ql.operators import degree, only, score_of, top
from codesense.ql.store import (
    InMemoryEdgeStore,
    InMemoryExpansionTable,
    InMemoryPostingIndex,
    InMemorySymbolStore,
)


def make_element(symbol_id: int, **overrides: object) -> Element:
    defaults: dict[str, object] = {
        "symbol_id": symbol_id,
        "name": f"s{symbol_id}",
        "kind": "method",
        "file": "A.java",
        "span": (1, 2),
        "language": "java",
    }
    return Element(**{**defaults, **overrides})  # type: ignore[arg-type]


def make_frag(*elements: Element, scores: dict[int, dict[str, float]] | None = None) -> Frag:
    return Frag(
        nodes={e.symbol_id: e for e in elements},
        evidence={
            sid: Evidence(
                tuple(
                    UnitHit(unit=unit, signal="lexical", detail="x", score=value)
                    for unit, value in units.items()
                )
            )
            for sid, units in (scores or {}).items()
        },
    )


def make_context(edges: tuple[Edge, ...] = ()) -> EvalContext:
    return EvalContext(
        symbols=InMemorySymbolStore([]),
        postings=InMemoryPostingIndex({}, total_symbols=100),
        expansion=InMemoryExpansionTable({}),
        edges=InMemoryEdgeStore(edges),
    )


class TestOnly:
    def test_按元素类型筛(self) -> None:
        frag = make_frag(make_element(1, kind="method"), make_element(2, kind="class"))
        assert set(only(frag, kind="method").nodes) == {1}

    def test_接受多个类型(self) -> None:
        frag = make_frag(
            make_element(1, kind="method"),
            make_element(2, kind="class"),
            make_element(3, kind="variable"),
        )
        assert set(only(frag, kind=["method", "class"]).nodes) == {1, 2}

    def test_按文件筛(self) -> None:
        frag = make_frag(make_element(1, file="A.java"), make_element(2, file="B.java"))
        assert set(only(frag, file="B.java").nodes) == {2}

    def test_按语言筛(self) -> None:
        frag = make_frag(make_element(1, language="java"), make_element(2, language="python"))
        assert set(only(frag, language="python").nodes) == {2}

    def test_多个条件是与关系(self) -> None:
        frag = make_frag(
            make_element(1, kind="method", file="A.java"),
            make_element(2, kind="method", file="B.java"),
        )
        assert set(only(frag, kind="method", file="A.java").nodes) == {1}

    def test_None_表示条件不生效(self) -> None:
        frag = make_frag(make_element(1), make_element(2))
        assert len(only(frag, kind=None)) == 2

    def test_空序列筛掉全部_与_None_不同(self) -> None:
        frag = make_frag(make_element(1), make_element(2))
        assert not only(frag, kind=[])

    def test_where_逃生舱(self) -> None:
        frag = make_frag(make_element(1, name="getUser"), make_element(2, name="setUser"))
        assert set(only(frag, where=lambda e: e.name.startswith("get")).nodes) == {1}

    def test_保留证据(self) -> None:
        frag = make_frag(make_element(1), scores={1: {"io": 0.5}})
        assert only(frag, kind="method").evidence_for(1).scores["io"] == 0.5

    def test_丢掉悬空的边(self) -> None:
        frag = Frag(
            nodes={1: make_element(1), 2: make_element(2, kind="class")},
            edges={(1, 2, "calls"): Edge(1, 2, "calls")},
        )
        assert only(frag, kind="method").edges == {}


class TestTop:
    def _frag(self) -> Frag:
        return make_frag(
            make_element(1),
            make_element(2),
            make_element(3),
            scores={1: {"io": 0.9}, 2: {"io": 0.5}, 3: {"io": 0.7}},
        )

    def test_取前_n_名(self) -> None:
        assert set(top(self._frag(), 2, by="io").nodes) == {1, 3}

    def test_n_大于总数时全返回(self) -> None:
        assert len(top(self._frag(), 99, by="io")) == 3

    def test_n_为0返回空(self) -> None:
        assert not top(self._frag(), 0, by="io")

    def test_负数报错(self) -> None:
        with pytest.raises(ValueError, match="不能为负"):
            top(self._frag(), -1)

    def test_不给_by_时按所有单元之和(self) -> None:
        frag = make_frag(
            make_element(1),
            make_element(2),
            scores={1: {"io": 0.4, "perf": 0.4}, 2: {"io": 0.6}},
        )
        assert set(top(frag, 1).nodes) == {1}

    def test_按指定单元排序与总和不同(self) -> None:
        frag = make_frag(
            make_element(1),
            make_element(2),
            scores={1: {"io": 0.4, "perf": 0.9}, 2: {"io": 0.6}},
        )
        assert set(top(frag, 1, by="io").nodes) == {2}
        assert set(top(frag, 1).nodes) == {1}

    def test_并列时按_symbol_id_升序_结果可复现(self) -> None:
        frag = make_frag(make_element(3), make_element(1), scores={3: {"io": 0.5}, 1: {"io": 0.5}})
        assert set(top(frag, 1, by="io").nodes) == {1}

    def test_没有证据的节点分数为0(self) -> None:
        frag = make_frag(make_element(1), make_element(2), scores={1: {"io": 0.5}})
        assert set(top(frag, 1, by="io").nodes) == {1}
        assert score_of(frag, 2, "io") == 0.0


class TestDegree:
    def _ctx(self) -> EvalContext:
        return make_context(
            (Edge(1, 2, "calls"), Edge(3, 2, "calls"), Edge(2, 4, "calls"), Edge(1, 5, "contains"))
        )

    def _frag(self) -> Frag:
        return make_frag(*(make_element(i) for i in range(1, 6)))

    def test_入度上限取入口点(self) -> None:
        assert set(degree(self._frag(), self._ctx(), max_in=0).nodes) == {1, 3, 5}

    def test_入度下限找被广泛调用的(self) -> None:
        assert set(degree(self._frag(), self._ctx(), min_in=2).nodes) == {2}

    def test_出度条件(self) -> None:
        assert set(degree(self._frag(), self._ctx(), min_out=1).nodes) == {1, 2, 3}

    def test_按边类型算(self) -> None:
        """`contains` 边不该算进调用度数。"""
        assert 5 in degree(self._frag(), self._ctx(), edge="calls", max_in=0).nodes
        assert 5 not in degree(self._frag(), self._ctx(), edge="contains", max_in=0).nodes

    def test_edge_为_None_时不限类型(self) -> None:
        assert 5 not in degree(self._frag(), self._ctx(), edge=None, max_in=0).nodes

    def test_度数是全图的不是片段内的(self) -> None:
        """「这个函数被很多地方调用」问的是它在代码库里的地位。"""
        partial = make_frag(make_element(2))
        assert set(degree(partial, self._ctx(), min_in=2).nodes) == {2}

    def test_不给任何条件时原样返回(self) -> None:
        assert len(degree(self._frag(), self._ctx())) == 5
