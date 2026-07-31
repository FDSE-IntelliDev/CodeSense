"""注解类查询的端到端测试。

样例项目的 Java 源码不在本机（`dependency_graph.json` 里是另一台机器的路径），
所以这里用合成的索引，而不是 `mini_index.json`。抽取本身的正确性由
`tests/unit/indexing/test_annotations.py` 对着内联 Java 源码保证。
"""

from __future__ import annotations

import pytest

from codesense.indexing import build_expansion_table
from codesense.ql import Element, IndexField
from codesense.ql.context import EvalContext
from codesense.ql.operators import eval_unit, only, top
from codesense.ql.satisfiers import AnnotationSatisfier, LexicalSatisfier
from codesense.ql.store import (
    InMemoryEdgeStore,
    InMemoryPostingIndex,
    InMemorySymbolStore,
    Posting,
)
from codesense.ql.unit import QueryUnit, Term

#: symbol_id → (名字, 该符号上的 annotation 域 term, annotation_arg 域 term)
FIXTURE = {
    1: ("getUser", ["@GetMapping", "get", "mapping"], ["api", "v1", "users"]),
    2: ("createUser", ["@PostMapping", "post", "mapping"], ["api", "v1", "users"]),
    3: ("evictUserCache", ["@CacheEvict", "cache", "evict"], ["usercache"]),
    4: ("appCached", ["@AppCache", "app", "cache"], []),
    5: ("plainHelper", [], []),
}


@pytest.fixture(scope="module")
def ctx() -> EvalContext:
    postings: dict[str, list[Posting]] = {}
    for symbol_id, (_, annotation_terms, arg_terms) in FIXTURE.items():
        for term in annotation_terms:
            postings.setdefault(term, []).append(Posting(symbol_id, IndexField.ANNOTATION))
        for term in arg_terms:
            postings.setdefault(term, []).append(Posting(symbol_id, IndexField.ANNOTATION_ARG))
    return EvalContext(
        symbols=InMemorySymbolStore(
            [
                Element(symbol_id=sid, name=name, kind="method", file="A.java", span=(sid, sid))
                for sid, (name, _, _) in FIXTURE.items()
            ]
        ),
        # 200 是个"大项目"的规模，让 ICF 不至于把这些词全挡掉
        postings=InMemoryPostingIndex(postings, total_symbols=200),
        expansion=build_expansion_table(),
        edges=InMemoryEdgeStore([]),
    )


def unit(name: str, satisfier: object) -> QueryUnit:
    return QueryUnit(name, satisfiers=(satisfier,))


class TestMetaExpansion:
    """`所有 HTTP 入口` —— 查询只说 `@RequestMapping`，要命中各种 Mapping。"""

    def test_一般注解展开到具体注解(self, ctx: EvalContext) -> None:
        frag = eval_unit(unit("http", AnnotationSatisfier(names=("@RequestMapping",))), ctx)
        assert {e.name for e in frag} == {"getUser", "createUser"}

    def test_证据说清是元注解关系(self, ctx: EvalContext) -> None:
        frag = eval_unit(unit("http", AnnotationSatisfier(names=("@RequestMapping",))), ctx)
        details = [h.detail for sid in frag.nodes for h in frag.evidence_for(sid).unit_hits]
        assert any("meta" in d for d in details)

    def test_没有元注解关系的不会被带出来(self, ctx: EvalContext) -> None:
        frag = eval_unit(unit("http", AnnotationSatisfier(names=("@RequestMapping",))), ctx)
        assert "evictUserCache" not in {e.name for e in frag}


class TestUnitMatching:
    """`缓存相关的方法` —— 按切分后的单元匹配，不是字面正则。"""

    def test_项目自定义注解和框架注解一起命中(self, ctx: EvalContext) -> None:
        """`@AppCache` 是项目自己定义的，`@CacheEvict` 是 Spring 的。"""
        frag = eval_unit(unit("cache", AnnotationSatisfier(units=(Term("cache"),))), ctx)
        assert {e.name for e in frag} == {"evictUserCache", "appCached"}

    def test_没有注解的方法不命中(self, ctx: EvalContext) -> None:
        frag = eval_unit(unit("cache", AnnotationSatisfier(units=(Term("cache"),))), ctx)
        assert "plainHelper" not in {e.name for e in frag}


class TestAnnotationArguments:
    """参数里的信息——只索引名字就全丢了。"""

    def test_能按_url_路径段查(self, ctx: EvalContext) -> None:
        frag = eval_unit(unit("users", AnnotationSatisfier(units=(Term("users"),))), ctx)
        assert {e.name for e in frag} == {"getUser", "createUser"}

    def test_命中的是_annotation_arg_域(self, ctx: EvalContext) -> None:
        frag = eval_unit(unit("users", AnnotationSatisfier(units=(Term("users"),))), ctx)
        fields = {
            h.field for sid in frag.nodes for h in frag.evidence_for(sid).unit_hits if h.field
        }
        assert fields == {"annotation_arg"}

    def test_参数域权重低于名字域(self, ctx: EvalContext) -> None:
        by_name = eval_unit(unit("u", AnnotationSatisfier(units=(Term("cache"),))), ctx)
        by_arg = eval_unit(unit("u", AnnotationSatisfier(units=(Term("users"),))), ctx)
        best_name = max(by_name.evidence_for(s).scores["u"] for s in by_name.nodes)
        best_arg = max(by_arg.evidence_for(s).scores["u"] for s in by_arg.nodes)
        assert best_name > best_arg


class TestAnnotationBeatsLexical:
    """注解是比词法强得多的信号，权重应当体现这一点。"""

    def test_同一个词_注解命中得分高于词法命中(self, ctx: EvalContext) -> None:
        annotation = eval_unit(unit("c", AnnotationSatisfier(units=(Term("cache"),))), ctx)
        lexical = eval_unit(unit("c", LexicalSatisfier(terms=(Term("cache"),), weight=0.5)), ctx)
        best_annotation = max(annotation.evidence_for(s).scores["c"] for s in annotation.nodes)
        best_lexical = max(lexical.evidence_for(s).scores["c"] for s in lexical.nodes)
        assert best_annotation > best_lexical

    def test_可以和收窄算子串起来(self, ctx: EvalContext) -> None:
        frag = eval_unit(unit("cache", AnnotationSatisfier(units=(Term("cache"),))), ctx)
        assert len(top(only(frag, kind="method"), 1, by="cache")) == 1
