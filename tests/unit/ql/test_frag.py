"""`codesense.ql.frag` 的单元测试。

重点覆盖 ``docs/design/03-data-model.md`` 明确点名的几条不变式，
它们都是「实现时最容易漏」的：

- 证据不参与相等判断，否则 ``a | a != a``
- 交集必须合并证据，否则结果里会有说不出理由的元素
- 片段的边不得悬空
"""

from __future__ import annotations

import pytest

from codesense.ql import Edge, Element, Evidence, Frag, Path, UnitHit, Verdict


def make_element(symbol_id: int, name: str = "") -> Element:
    return Element(
        symbol_id=symbol_id,
        name=name or f"sym{symbol_id}",
        kind="method",
        file="A.java",
        span=(symbol_id, symbol_id + 1),
    )


def make_frag(*symbol_ids: int, edges: tuple[Edge, ...] = ()) -> Frag:
    return Frag(
        nodes={sid: make_element(sid) for sid in symbol_ids},
        edges={e.key: e for e in edges},
    )


def hit(unit: str, detail: str = "x", score: float = 1.0) -> UnitHit:
    return UnitHit(unit=unit, signal="lexical", detail=detail, score=score)


class TestFragInvariants:
    def test_边不得指向片段外的节点(self) -> None:
        with pytest.raises(ValueError, match="不在片段里"):
            Frag(nodes={1: make_element(1)}, edges={(1, 2, "calls"): Edge(1, 2, "calls")})

    def test_映射是只读的_frozen挡不住原地改内容(self) -> None:
        frag = make_frag(1)
        with pytest.raises(TypeError):
            frag.nodes[2] = make_element(2)  # type: ignore[index]

    def test_片段不可哈希(self) -> None:
        with pytest.raises(TypeError):
            hash(make_frag(1))

    def test_构造时传入的字典之后被改不影响片段(self) -> None:
        nodes = {1: make_element(1)}
        frag = Frag(nodes=nodes)
        nodes[2] = make_element(2)
        assert len(frag) == 1


class TestEquality:
    def test_相等只看节点与边_不看证据(self) -> None:
        left = Frag(nodes={1: make_element(1)}, evidence={1: Evidence((hit("io"),))})
        right = Frag(nodes={1: make_element(1)}, evidence={1: Evidence((hit("disk"),))})
        assert left == right

    def test_幂等_并上自己等于自己(self) -> None:
        frag = make_frag(1, 2)
        assert frag | frag == frag

    def test_证据不同的片段并起来仍等于原片段(self) -> None:
        left = Frag(nodes={1: make_element(1)}, evidence={1: Evidence((hit("io"),))})
        right = Frag(nodes={1: make_element(1)}, evidence={1: Evidence((hit("disk"),))})
        assert left | right == left

    def test_和非片段比较返回_NotImplemented(self) -> None:
        assert make_frag(1) != "不是片段"


class TestAlgebra:
    def test_并(self) -> None:
        assert set((make_frag(1, 2) | make_frag(2, 3)).nodes) == {1, 2, 3}

    def test_交(self) -> None:
        assert set((make_frag(1, 2) & make_frag(2, 3)).nodes) == {2}

    def test_差(self) -> None:
        assert set((make_frag(1, 2) - make_frag(2)).nodes) == {1}

    def test_交集合并两边的证据(self) -> None:
        """设计文档点名「最容易漏」的一条。"""
        left = Frag(nodes={1: make_element(1)}, evidence={1: Evidence((hit("io"),))})
        right = Frag(nodes={1: make_element(1)}, evidence={1: Evidence((hit("perf"),))})
        units = {h.unit for h in (left & right).evidence_for(1).unit_hits}
        assert units == {"io", "perf"}

    def test_并集合并两边的证据(self) -> None:
        left = Frag(nodes={1: make_element(1)}, evidence={1: Evidence((hit("io"),))})
        right = Frag(nodes={1: make_element(1)}, evidence={1: Evidence((hit("perf"),))})
        units = {h.unit for h in (left | right).evidence_for(1).unit_hits}
        assert units == {"io", "perf"}

    def test_证据合并保序去重(self) -> None:
        same = Evidence((hit("io", "buf"), hit("perf", "cache")))
        merged = same.merge(Evidence((hit("io", "buf"),)))
        assert [h.detail for h in merged.unit_hits] == ["buf", "cache"]

    def test_差集保留自己的证据(self) -> None:
        left = Frag(
            nodes={1: make_element(1), 2: make_element(2)},
            evidence={1: Evidence((hit("io"),)), 2: Evidence((hit("perf"),))},
        )
        result = left - make_frag(2)
        assert [h.unit for h in result.evidence_for(1).unit_hits] == ["io"]
        assert result.evidence_for(2).unit_hits == ()

    def test_差集丢掉悬空的边(self) -> None:
        frag = make_frag(1, 2, edges=(Edge(1, 2, "calls"),))
        assert (frag - make_frag(2)).edges == {}

    def test_交集保留两端都在的边(self) -> None:
        left = make_frag(1, 2, edges=(Edge(1, 2, "calls"),))
        both = left & make_frag(1, 2)
        assert (1, 2, "calls") in both.edges

    def test_交集丢掉只有一端在的边(self) -> None:
        left = make_frag(1, 2, edges=(Edge(1, 2, "calls"),))
        assert (left & make_frag(1)).edges == {}


class TestProjection:
    def test_roots_是入度为0的节点(self) -> None:
        frag = make_frag(1, 2, 3, edges=(Edge(1, 2, "calls"), Edge(2, 3, "calls")))
        assert set(frag.roots().nodes) == {1}

    def test_leaves_是出度为0的节点(self) -> None:
        frag = make_frag(1, 2, 3, edges=(Edge(1, 2, "calls"), Edge(2, 3, "calls")))
        assert set(frag.leaves().nodes) == {3}

    def test_没有边时_roots_和_leaves_都是全体(self) -> None:
        frag = make_frag(1, 2)
        assert set(frag.roots().nodes) == set(frag.leaves().nodes) == {1, 2}

    def test_only_nodes_丢掉边和路径见证(self) -> None:
        frag = Frag(
            nodes={1: make_element(1), 2: make_element(2)},
            edges={(1, 2, "calls"): Edge(1, 2, "calls")},
            witnesses=(Path((1, 2), (Edge(1, 2, "calls"),)),),
        )
        bare = frag.only_nodes()
        assert bare.edges == {}
        assert bare.witnesses == ()
        assert set(bare.nodes) == {1, 2}

    def test_only_nodes_保留证据(self) -> None:
        frag = Frag(nodes={1: make_element(1)}, evidence={1: Evidence((hit("io"),))})
        assert frag.only_nodes().evidence_for(1).unit_hits != ()

    def test_induced_丢掉不完整的路径见证(self) -> None:
        frag = Frag(
            nodes={sid: make_element(sid) for sid in (1, 2, 3)},
            edges={(1, 2, "calls"): Edge(1, 2, "calls")},
            witnesses=(Path((1, 2), (Edge(1, 2, "calls"),)),),
        )
        assert frag.induced([1, 3]).witnesses == ()

    def test_induced_忽略片段里没有的_id(self) -> None:
        assert set(make_frag(1, 2).induced([2, 999]).nodes) == {2}


class TestEvidence:
    def test_scores_按单元汇总(self) -> None:
        ev = Evidence((hit("io", "a", 0.5), hit("io", "b", 0.25), hit("perf", "c", 1.0)))
        assert ev.scores == {"io": 0.75, "perf": 1.0}

    def test_scores_是只读的派生量(self) -> None:
        with pytest.raises(TypeError):
            Evidence((hit("io"),)).scores["io"] = 9.0  # type: ignore[index]

    def test_判定带理由(self) -> None:
        ev = Evidence(verdicts=(Verdict("llm", "yes", "它是登录入口"),))
        assert ev.verdicts[0].reason == "它是登录入口"

    def test_取不存在节点的证据返回空而不抛(self) -> None:
        assert make_frag(1).evidence_for(999) == Evidence()


class TestContainerProtocol:
    def test_len_是节点数(self) -> None:
        assert len(make_frag(1, 2, 3)) == 3

    def test_迭代产出元素(self) -> None:
        assert {e.symbol_id for e in make_frag(1, 2)} == {1, 2}

    def test_in_按_symbol_id(self) -> None:
        assert 1 in make_frag(1)
        assert 2 not in make_frag(1)

    def test_空片段为假(self) -> None:
        assert not Frag()
        assert make_frag(1)

    def test_path_长度是边数(self) -> None:
        assert len(Path((1, 2, 3), (Edge(1, 2, "calls"), Edge(2, 3, "calls")))) == 2
