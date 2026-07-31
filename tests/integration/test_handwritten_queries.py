"""阶段 A 验收：手写 QL 覆盖不同查询类型，检验算子集合够不够。

``docs/design/07-mapping-to-current.md`` 的原话：

    **手写** 3–5 段 QL 覆盖不同查询类型，验证算子集合够不够。
    写不出来的地方就是缺的算子——这一步很便宜（几百行），
    但能避免把编译器建在错的算子集上。

所以本文件有两类测试：

- ``TestQueryN`` —— 能写出来的，顺便当回归测试
- ``TestGaps`` —— **写不出来的**，把缺口钉成可执行的断言，
  补上对应能力时这些测试会失败，提醒回来改

数据来自真实项目（``scripts/build_ql_fixture.py`` 从 youlai-boot 抽取），
不是编造的拓扑——真实数据的稀疏与命名习惯才是算子会碰到的东西。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from codesense.ql import Edge, Element, Frag, IndexField
from codesense.ql.context import EvalContext
from codesense.ql.operators import eval_unit, hop, reach
from codesense.ql.satisfiers import AnnotationSatisfier, LexicalSatisfier
from codesense.ql.store import (
    InMemoryEdgeStore,
    InMemoryExpansionTable,
    InMemoryPostingIndex,
    InMemorySymbolStore,
    Posting,
)
from codesense.ql.unit import QueryUnit, Term

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "ql" / "mini_index.json"


@pytest.fixture(scope="module")
def ctx() -> EvalContext:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    elements = [
        Element(
            symbol_id=s["symbol_id"],
            name=s["name"],
            kind=s["kind"],
            file=s["file"],
            span=tuple(s["span"]),
            signature=s["signature"],
            container=s["container"],
            language=s["language"],
        )
        for s in payload["symbols"]
    ]
    return EvalContext(
        symbols=InMemorySymbolStore(elements),
        postings=InMemoryPostingIndex(
            {
                term: [Posting(p["symbol_id"], IndexField(p["field"]), p["tf"]) for p in entries]
                for term, entries in payload["postings"].items()
            },
            total_symbols=len(elements),
        ),
        expansion=InMemoryExpansionTable({}),
        edges=InMemoryEdgeStore(
            [
                Edge(
                    source_id=e["source_id"],
                    target_id=e["target_id"],
                    kind=e["kind"],
                    confidence=e["confidence"],
                    provenance=e["provenance"],
                )
                for e in payload["edges"]
            ]
        ),
    )


def names(frag: Frag) -> set[str]:
    return {e.name for e in frag}


def ranked(frag: Frag, unit: str) -> list[tuple[str, float]]:
    """按分数排序。**目前得手写**——见 `TestGaps.test_缺少_top_k_算子`。"""
    scored = [
        (frag.nodes[sid].name, frag.evidence_for(sid).scores.get(unit, 0.0)) for sid in frag.nodes
    ]
    return sorted(scored, key=lambda x: (-x[1], x[0]))


class TestQuery1LexicalOnly:
    """`find the token manager` —— 纯实体定位，只靠词法。"""

    def test_能定位到_token_管理器(self, ctx: EvalContext) -> None:
        unit = QueryUnit(
            "token_manager",
            concept="管理令牌的组件",
            satisfiers=(LexicalSatisfier(terms=(Term("token"), Term("manager")), weight=1.0),),
        )
        top = ranked(eval_unit(unit, ctx), "token_manager")
        assert top[0][0] == "RedisTokenManager"

    def test_同时命中两个词的排在只命中一个的前面(self, ctx: EvalContext) -> None:
        unit = QueryUnit(
            "token_manager",
            satisfiers=(
                LexicalSatisfier(terms=(Term("token"),), weight=1.0),
                LexicalSatisfier(terms=(Term("manager"),), weight=1.0),
            ),
        )
        scores = dict(ranked(eval_unit(unit, ctx), "token_manager"))
        assert scores["RedisTokenManager"] > scores["generateToken"]


class TestQuery2GraphOnly:
    """`RedisTokenManager 里有哪些方法` —— 纯图约束，关键词没有区分度。

    这是设计文档举的例子：现有三段管线会先跑一遍全量词法匹配，
    而这个查询根本没有有意义的关键词。
    """

    def test_用_contains_边列出类的成员(self, ctx: EvalContext) -> None:
        cls = _by_name(ctx, "RedisTokenManager", kind="class")
        members = reach(cls, ctx, edge="contains", hops=1)
        assert {"generateToken", "parseToken", "validateToken"} <= names(members)

    def test_物化_contains_把孤点从大多数降到极少数(self, ctx: EvalContext) -> None:
        """10 章的判断：把 contains 物化出来，孤点问题基本就没了。

        实测 33% → 98%。剩下那 2%（7 个）是符号表里 container 为空的记录，
        属于上游解析的欠账，不是 contains 物化没做到位。
        """
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        total = len(payload["symbols"])
        by_kind = {
            kind: {e["source_id"] for e in payload["edges"] if e["kind"] == kind}
            | {e["target_id"] for e in payload["edges"] if e["kind"] == kind}
            for kind in ("calls", "contains")
        }
        assert len(by_kind["calls"]) / total < 0.4
        assert len(by_kind["calls"] | by_kind["contains"]) / total > 0.95

    def test_只有_calls_边时大量符号够不着(self, ctx: EvalContext) -> None:
        """反证：不物化 contains，同样的查询就废了。"""
        cls = _by_name(ctx, "RedisTokenManager", kind="class")
        assert not reach(cls, ctx, edge="calls", hops=(1, 3))


class TestQuery3MultiUnitHop:
    """`token 相关的代码调用了 redis 相关的代码` —— 两个单元靠图连起来。

    这句话表达不成「同时含有 token 和 redis 关键词的元素」——那样的元素几乎不存在。
    """

    def test_两个单元之间的调用路径(self, ctx: EvalContext) -> None:
        token = eval_unit(
            QueryUnit("token", satisfiers=(LexicalSatisfier(terms=(Term("token"),)),)), ctx
        )
        redis = eval_unit(
            QueryUnit("redis", satisfiers=(LexicalSatisfier(terms=(Term("redis"),)),)), ctx
        )
        linked = hop(token, redis, ctx, edge=["calls", "contains"], hops=(1, 2))
        assert linked.witnesses

    def test_结果保留了路径而不只是端点(self, ctx: EvalContext) -> None:
        token = eval_unit(
            QueryUnit("token", satisfiers=(LexicalSatisfier(terms=(Term("token"),)),)), ctx
        )
        redis = eval_unit(
            QueryUnit("redis", satisfiers=(LexicalSatisfier(terms=(Term("redis"),)),)), ctx
        )
        linked = hop(token, redis, ctx, edge=["calls", "contains"], hops=(1, 2))
        assert all(len(p.nodes) == len(p.edges) + 1 for p in linked.witnesses)

    def test_两端的单元证据都被带进结果(self, ctx: EvalContext) -> None:
        token = eval_unit(
            QueryUnit("token", satisfiers=(LexicalSatisfier(terms=(Term("token"),)),)), ctx
        )
        redis = eval_unit(
            QueryUnit("redis", satisfiers=(LexicalSatisfier(terms=(Term("redis"),)),)), ctx
        )
        linked = hop(token, redis, ctx, edge=["calls", "contains"], hops=(1, 2))
        units = {hit.unit for sid in linked.nodes for hit in linked.evidence_for(sid).unit_hits}
        assert {"token", "redis"} <= units


def _unit(name: str, *terms: str, weight: float = 1.0) -> QueryUnit:
    return QueryUnit(
        name, satisfiers=(LexicalSatisfier(terms=tuple(Term(t) for t in terms), weight=weight),)
    )


class TestQuery4Algebra:
    """`既和 token 有关又和 redis 有关` —— 片段代数。"""

    def test_交集(self, ctx: EvalContext) -> None:
        both = eval_unit(_unit("token", "token"), ctx) & eval_unit(_unit("redis", "redis"), ctx)
        assert "RedisTokenManager" in names(both)

    def test_差集(self, ctx: EvalContext) -> None:
        """按 symbol_id 比，不按名字——不同符号可以同名。"""
        token = eval_unit(_unit("token", "token"), ctx)
        redis = eval_unit(_unit("redis", "redis"), ctx)
        assert not set((token - redis).nodes) & set(redis.nodes)
        assert set((token - redis).nodes) < set(token.nodes)

    def test_交集合并两边证据(self, ctx: EvalContext) -> None:
        both = eval_unit(_unit("token", "token"), ctx) & eval_unit(_unit("redis", "redis"), ctx)
        sid = next(sid for sid in both.nodes if both.nodes[sid].name == "RedisTokenManager")
        assert {"token", "redis"} <= {h.unit for h in both.evidence_for(sid).unit_hits}


class TestFindingIcfIsRelativeToTheIndex:
    """发现：ICF 下限对索引组成敏感，临界词会翻转。

    `user` 在全项目是 125/1718 → icf_ratio 0.352（刚过 0.34 的线），
    在这个 375 符号的样本里是 65/375 → 0.296（刚不过）。

    **后果**：不能在子集索引上沿用全量索引标定的阈值，
    也不能假设「常见领域词一定能查到」。这条要写进 09 章。
    """

    def test_领域高频词被挡掉(self, ctx: EvalContext) -> None:
        assert not eval_unit(_unit("user", "user"), ctx)

    def test_同一个词降低阈值后就能查到(self, ctx: EvalContext) -> None:
        from dataclasses import replace

        loose = replace(ctx, icf_floor=0.2)
        assert eval_unit(_unit("user", "user"), loose)


class TestGaps:
    """写不出来的地方。**每条都是一个待补的能力。**"""

    def test_缺口1_注解没有被索引(self, ctx: EvalContext) -> None:
        """`所有 @Transactional 的方法` 写不出来。

        解析器不抽注解（10 章记录的欠账），所以 annotation 域是空的。
        实测项目里有 77 种注解，这是信噪比最高的一类信号却完全用不上。
        """
        unit = QueryUnit(
            "transactional", satisfiers=(AnnotationSatisfier(names=("@Transactional",)),)
        )
        assert not eval_unit(unit, ctx), "注解已经能索引了，请删掉这条缺口断言"

    def test_缺口2_没有字段读写边(self, ctx: EvalContext) -> None:
        """`谁修改了这个字段` 写不出来——没有 reads / writes 边。"""
        field = _any_of_kind(ctx, "variable")
        assert not reach(field, ctx, edge="writes", direction="backward"), (
            "writes 边已经有了，请删掉这条缺口断言"
        )

    def test_缺口3_没有修饰符(self, ctx: EvalContext) -> None:
        """`静态的工具方法` 写不出来——`Element.modifiers` 恒为空。

        修饰符是语言级事实，比任何关键词都准，但解析器没抽。
        """
        assert all(not e.modifiers for e in ctx.symbols.get_many(range(1, 200)).values()), (
            "修饰符已经有了，请删掉这条缺口断言"
        )

    def test_缺口4_按元素类型过滤要手写(self, ctx: EvalContext) -> None:
        """`只要方法，不要字段和类` 目前得在脚本里手写列表推导。

        缺一个 `only(frag, kind=...)` 算子——这是最常用的收窄方式之一。
        """
        found = eval_unit(_unit("token", "token"), ctx)
        manual = found.induced(sid for sid in found.nodes if found.nodes[sid].kind == "method")
        assert manual and len(manual) < len(found)

    def test_缺口5_取前_K_名要手写(self, ctx: EvalContext) -> None:
        """`最相关的 5 个` 目前得手写排序——见本文件的 `ranked()`。

        缺一个 `top(frag, n, by=unit)` 算子。排序需要知道按哪个单元的分数排，
        这个信息在证据里，所以算子放在 QL 层而不是脚本层才对。
        """
        assert len(ranked(eval_unit(_unit("token", "token"), ctx), "token")) > 5

    def test_缺口6_没有_intent_算子(self) -> None:
        """`真正在做鉴权的那个入口` 写不出来——`intent` 还没实现。

        它是唯一需要 LLM 的算子，刻意放在最后、候选最少时。
        """
        with pytest.raises(ImportError):
            from codesense.ql.operators import intent  # noqa: F401


def _any_of_kind(ctx: EvalContext, kind: str) -> Frag:
    found = {
        sid: element
        for sid, element in ctx.symbols.get_many(range(1, 2000)).items()
        if element.kind == kind
    }
    assert found, f"fixture 里没有 {kind}"
    return Frag(nodes=dict(sorted(found.items())[:1]))


def _by_name(ctx: EvalContext, name: str, *, kind: str) -> Frag:
    found = {
        sid: element
        for sid, element in ctx.symbols.get_many(range(1, 2000)).items()
        if element.name == name and element.kind == kind
    }
    assert found, f"fixture 里没有 {kind} {name}"
    return Frag(nodes=found)
