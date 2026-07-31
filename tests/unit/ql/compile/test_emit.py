"""脚本产物的测试。

**最要紧的一条是等价性**：脚本必须和它声称代表的 `Plan` 跑出同一个结果。
一旦分叉，脚本就成了一份看着像那么回事的假文档——比没有更糟，
因为人会照着它推断系统行为。

写这个测试之前踩过两次：`only(kind=...)` 被渲染成硬过滤而计划里是偏好加权，
`Cohere` 在脚本里当场重排而计划里只标记邻域。两次都是脚本比计划筛得更狠。
"""

from __future__ import annotations

from codesense.ql import Edge, Element, IndexField
from codesense.ql.compile import QuerySpec, plan, to_script
from codesense.ql.context import EvalContext
from codesense.ql.store import (
    InMemoryEdgeStore,
    InMemoryExpansionTable,
    InMemoryPostingIndex,
    InMemorySymbolStore,
    Posting,
)

TOTAL = 400


def make_context() -> EvalContext:
    postings = {
        "rare": [Posting(i, IndexField.NAME) for i in range(1, 30)],
        "common": [Posting(i, IndexField.NAME) for i in range(1, 160)],
        "@Cacheable": [Posting(i, IndexField.ANNOTATION) for i in range(1, 12)],
        "static": [Posting(i, IndexField.MODIFIER) for i in range(1, 8)],
    }
    return EvalContext(
        symbols=InMemorySymbolStore(
            [
                Element(
                    symbol_id=i,
                    name=f"s{i}",
                    kind="method" if i % 3 else "class",
                    file="A.java",
                    span=(1, 2),
                )
                for i in range(1, TOTAL + 1)
            ]
        ),
        postings=InMemoryPostingIndex(postings, total_symbols=TOTAL),
        expansion=InMemoryExpansionTable({}),
        edges=InMemoryEdgeStore(
            [Edge(i, i + 1, "calls") for i in range(1, 120)]
            + [Edge(i, i + 2, "contains") for i in range(1, 60)]
        ),
    )


def make_spec(**overrides: object) -> QuerySpec:
    payload: dict[str, object] = {
        "query": "找出缓存相关的方法",
        "units": [
            {"name": "narrow", "terms": ["rare"], "annotations": ["@Cacheable"]},
            {"name": "wide", "terms": ["common"], "modifiers": ["static"]},
        ],
        "kinds": ["method"],
    }
    payload.update(overrides)
    return QuerySpec.from_dict(payload)


def run_script(source: str, ctx: EvalContext) -> set[int]:
    namespace: dict[str, object] = {"ctx": ctx}
    exec(compile(source, "<generated>", "exec"), namespace)  # noqa: S102
    return set(namespace["answer"].nodes)  # type: ignore[union-attr]


class TestEquivalence:
    """脚本跑出来的必须和 `Plan` 跑出来的一样。"""

    def test_基本情形(self) -> None:
        ctx = make_context()
        spec = make_spec()
        execution = plan(spec, ctx)
        assert run_script(to_script(execution, spec), ctx) == set(execution.run(ctx).current.nodes)

    def test_带种类偏好(self) -> None:
        """踩过：`only(kind=...)` 被渲染成硬过滤，而计划里是偏好加权。"""
        ctx = make_context()
        spec = make_spec(kinds=["class"])
        execution = plan(spec, ctx)
        assert run_script(to_script(execution, spec), ctx) == set(execution.run(ctx).current.nodes)

    def test_带意图判定(self) -> None:
        ctx = make_context()
        spec = make_spec(concept="它是否在做缓存")
        execution = plan(spec, ctx)
        assert run_script(to_script(execution, spec), ctx) == set(execution.run(ctx).current.nodes)

    def test_单个单元(self) -> None:
        ctx = make_context()
        spec = QuerySpec.from_dict({"query": "q", "units": [{"name": "only", "terms": ["rare"]}]})
        execution = plan(spec, ctx)
        assert run_script(to_script(execution, spec), ctx) == set(execution.run(ctx).current.nodes)


class TestReadability:
    """脚本是给人看的，所以形态本身有要求。"""

    def test_是合法的_python(self) -> None:
        ctx = make_context()
        spec = make_spec()
        compile(to_script(plan(spec, ctx), spec), "<generated>", "exec")

    def test_头部记录查询与索引(self) -> None:
        """同一段脚本在不同索引上结果不同，不记就没法复现。"""
        ctx = make_context()
        spec = make_spec()
        source = to_script(plan(spec, ctx), spec, index="netty · 42221 符号")
        assert "找出缓存相关的方法" in source
        assert "netty · 42221 符号" in source

    def test_注释解释编排理由(self) -> None:
        """算子语义在设计文档里，脚本只说「为什么这么排」。"""
        ctx = make_context()
        spec = make_spec(concept="它是否在做缓存")
        source = to_script(plan(spec, ctx), spec)
        assert "# 先跑" in source
        assert "intent 放最后" in source

    def test_每个单元一个变量(self) -> None:
        ctx = make_context()
        spec = make_spec()
        source = to_script(plan(spec, ctx), spec)
        assert "narrow = QueryUnit(" in source

    def test_产出名为_answer(self) -> None:
        ctx = make_context()
        spec = make_spec()
        assert "\nanswer = frag" in to_script(plan(spec, ctx), spec)

    def test_相关度进了脚本(self) -> None:
        """词的权重是编译结果的一部分，看得见才改得动。"""
        ctx = make_context()
        spec = QuerySpec.from_dict({"query": "q", "units": [{"name": "u", "terms": ["rare"]}]})
        source = to_script(plan(spec, ctx), spec)
        assert 'Term("rare")' in source
