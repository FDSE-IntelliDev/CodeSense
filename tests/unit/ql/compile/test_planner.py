"""规划器与代价估计的单元测试。

重点是**顺序**——那是编译器里唯一做优化的地方。
"""

from __future__ import annotations

import pytest

from codesense.ql import Edge, Element, IndexField
from codesense.ql.compile import Boost, EvalUnit, Intent, Narrow, QuerySpec, estimate_unit, plan
from codesense.ql.compile.spec import normalise_hops, normalise_kinds
from codesense.ql.context import EvalContext
from codesense.ql.store import (
    InMemoryEdgeStore,
    InMemoryExpansionTable,
    InMemoryPostingIndex,
    InMemorySymbolStore,
    Posting,
)

TOTAL = 1000


def make_context(postings: dict[str, int], edges: tuple[Edge, ...] = ()) -> EvalContext:
    """``postings`` 给的是每个 term 命中多少符号。"""
    built: dict[str, list[Posting]] = {}
    for term, count in postings.items():
        built[term] = [Posting(i, IndexField.NAME) for i in range(1, count + 1)]
    return EvalContext(
        symbols=InMemorySymbolStore(
            [
                Element(symbol_id=i, name=f"s{i}", kind="method", file="A.java", span=(1, 2))
                for i in range(1, TOTAL + 1)
            ]
        ),
        postings=InMemoryPostingIndex(built, total_symbols=TOTAL),
        expansion=InMemoryExpansionTable({}),
        edges=InMemoryEdgeStore(edges),
    )


def spec(**overrides: object) -> QuerySpec:
    payload: dict[str, object] = {
        "query": "q",
        "units": [
            {"name": "wide", "terms": ["common"]},
            {"name": "narrow", "terms": ["rare"]},
        ],
    }
    payload.update(overrides)
    return QuerySpec.from_dict(payload)


class TestEstimate:
    def test_不执行就能估出规模(self) -> None:
        """靠的是倒排索引里的 df——这是排序能优化的前提。"""
        ctx = make_context({"rare": 10})
        unit = spec().units[1]
        assert estimate_unit(unit, ctx).rows == pytest.approx(10, abs=1)

    def test_多个词按独立假设求并(self) -> None:
        ctx = make_context({"a": 100, "b": 100})
        unit = QuerySpec.from_dict(
            {"query": "q", "units": [{"name": "u", "terms": ["a", "b"]}]}
        ).units[0]
        # 1000·(1 − 0.9²) = 190
        assert 150 < estimate_unit(unit, ctx).rows < 220

    def test_查不到的词不贡献规模(self) -> None:
        ctx = make_context({})
        unit = spec().units[0]
        assert estimate_unit(unit, ctx).rows == 0


class TestOrdering:
    def test_最窄的单元排最前(self) -> None:
        """工作集越早变小，后面每一步越便宜。"""
        ctx = make_context({"common": 400, "rare": 10})
        steps = plan(spec(), ctx).steps
        assert isinstance(steps[0], EvalUnit)
        assert steps[0].unit.name == "narrow"
        assert steps[0].seed

    def test_几乎命中全表的单元才丢掉(self) -> None:
        """门槛定得高是吃过亏的：0.5 会把唯一含答案的单元丢掉。"""
        ctx = make_context({"common": 980, "rare": 10})
        planned = plan(spec(), ctx)
        assert [s.unit.name for s in planned.steps if isinstance(s, EvalUnit)] == ["narrow"]
        assert any("丢掉单元" in why for why in planned.reasoning)

    def test_只是偏宽的单元保留但降权(self) -> None:
        """覆盖一半不算无用——降权就够了，丢掉的代价是答案没了。"""
        ctx = make_context({"common": 500, "rare": 10})
        names = [s.unit.name for s in plan(spec(), ctx).steps if isinstance(s, EvalUnit)]
        assert set(names) == {"narrow", "wide"}

    def test_全都太宽泛时保留最窄的(self) -> None:
        """不能因为都宽就产出空计划。"""
        ctx = make_context({"common": 900, "rare": 800})
        planned = plan(spec(), ctx)
        assert any(isinstance(s, EvalUnit) for s in planned.steps)

    def test_intent_永远在最后(self) -> None:
        ctx = make_context({"common": 100, "rare": 10})
        steps = plan(spec(concept="判断它是否相关"), ctx).steps
        assert isinstance(steps[-1], Intent)

    def test_intent_之前先收窄(self) -> None:
        """它比查表贵几千倍，输入必须先压住。"""
        ctx = make_context({"common": 100, "rare": 10})
        steps = plan(spec(concept="判断"), ctx).steps
        assert isinstance(steps[-2], Narrow)
        assert steps[-2].limit is not None

    def test_没有意图判定时不加_intent(self) -> None:
        ctx = make_context({"common": 100, "rare": 10})
        assert not any(isinstance(s, Intent) for s in plan(spec(), ctx).steps)


class TestGraphDirection:
    def test_从小的一侧出发(self) -> None:
        """`hop` 的代价随起点数线性增长。"""
        ctx = make_context({"common": 400, "rare": 10}, (Edge(1, 2, "calls"),))
        planned = plan(spec(graph=[{"src": "wide", "dst": "narrow"}]), ctx)
        boosts = [s for s in planned.steps if isinstance(s, Boost)]
        assert boosts[0].src_name == "narrow"
        assert any("反向" in why for why in planned.reasoning)

    def test_两侧规模接近时保持原方向(self) -> None:
        ctx = make_context({"common": 100, "rare": 90}, (Edge(1, 2, "calls"),))
        planned = plan(spec(graph=[{"src": "wide", "dst": "narrow"}]), ctx)
        boosts = [s for s in planned.steps if isinstance(s, Boost)]
        assert boosts[0].src_name == "wide"

    def test_单元被丢掉时跳过对应的图约束(self) -> None:
        ctx = make_context({"common": 980, "rare": 10}, (Edge(1, 2, "calls"),))
        planned = plan(spec(graph=[{"src": "wide", "dst": "narrow"}]), ctx)
        assert not any(isinstance(s, Boost) for s in planned.steps)
        assert any("跳过图约束" in why for why in planned.reasoning)


class TestExecution:
    def test_单元之间取并集不是交集(self) -> None:
        """交集会把答案杀光——单元本来就落在不同元素上。"""
        ctx = make_context({"common": 100, "rare": 10})
        state = plan(spec(), ctx).run(ctx)
        assert len(state.current) > 10

    def test_试跑可以跳过_intent(self) -> None:
        ctx = make_context({"common": 100, "rare": 10})
        state = plan(spec(concept="判断"), ctx).run(ctx, skip=(Intent,))
        assert any(t.skipped == "试跑跳过" for t in state.trace)

    def test_轨迹记录预估与实际(self) -> None:
        ctx = make_context({"common": 100, "rare": 10})
        state = plan(spec(), ctx).run(ctx)
        assert all(t.estimated >= 0 and t.actual >= 0 for t in state.trace if not t.skipped)

    def test_计划可以打印(self) -> None:
        ctx = make_context({"common": 100, "rare": 10})
        text = plan(spec(concept="判断"), ctx).explain(ctx)
        assert "执行计划" in text and "intent" in text


class TestSpecRobustness:
    """规格来自 LLM，不变式得自己守。"""

    def test_跳数只给一个值(self) -> None:
        assert normalise_hops([2]) == (1, 2)

    def test_跳数为空(self) -> None:
        assert normalise_hops([]) == (1, 2)

    def test_跳数给整数(self) -> None:
        assert normalise_hops(3) == (3, 3)

    def test_跳数颠倒时自动纠正(self) -> None:
        assert normalise_hops([3, 1]) == (1, 3)

    def test_class_映射到接口与枚举(self) -> None:
        """模型说 class 时心里含接口——不映射就会静默丢结果。"""
        assert "interface" in normalise_kinds(["class"])

    def test_method_映射到构造函数(self) -> None:
        assert "constructor" in normalise_kinds(["method"])

    def test_未知种类原样保留(self) -> None:
        assert normalise_kinds(["widget"]) == ("widget",)

    def test_单元名重复报错(self) -> None:
        with pytest.raises(ValueError, match="重复"):
            QuerySpec.from_dict(
                {
                    "query": "q",
                    "units": [{"name": "a", "terms": ["x"]}, {"name": "a", "terms": ["y"]}],
                }
            )

    def test_图约束引用不存在的单元报错(self) -> None:
        with pytest.raises(ValueError, match="不存在的单元"):
            QuerySpec.from_dict(
                {
                    "query": "q",
                    "units": [{"name": "a", "terms": ["x"]}],
                    "graph": [{"src": "a", "dst": "缺失"}],
                }
            )

    def test_没有可执行条件的单元报错(self) -> None:
        with pytest.raises(ValueError, match="没有任何可执行的条件"):
            QuerySpec.from_dict({"query": "q", "units": [{"name": "a"}]})

    def test_没有单元报错(self) -> None:
        with pytest.raises(ValueError, match="至少要有一个单元"):
            QuerySpec.from_dict({"query": "q", "units": []})
