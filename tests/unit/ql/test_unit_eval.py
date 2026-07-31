"""registry / combine / satisfiers / `eval_unit` 的单元测试。"""

from __future__ import annotations

import json
from dataclasses import asdict

import pytest

from codesense.ql import Element, Evidence, IndexField, UnitHit, Verdict
from codesense.ql.combine import COMBINERS, combine
from codesense.ql.context import EvalContext
from codesense.ql.operators import eval_unit
from codesense.ql.registry import Registry
from codesense.ql.satisfiers import AnnotationSatisfier, LexicalSatisfier, ModifierSatisfier
from codesense.ql.store import (
    Expansion,
    InMemoryEdgeStore,
    InMemoryExpansionTable,
    InMemoryPostingIndex,
    InMemorySymbolStore,
    Posting,
)
from codesense.ql.unit import QueryUnit, Term

TOTAL = 1718


def make_element(symbol_id: int, name: str) -> Element:
    return Element(symbol_id=symbol_id, name=name, kind="method", file="A.java", span=(1, 2))


def make_context(
    *,
    postings: dict[str, list[Posting]] | None = None,
    expansion: dict[str, list[Expansion]] | None = None,
    elements: list[Element] | None = None,
    **kwargs: object,
) -> EvalContext:
    return EvalContext(
        symbols=InMemorySymbolStore(elements or [make_element(1, "flushBuf")]),
        postings=InMemoryPostingIndex(postings or {}, total_symbols=TOTAL),
        expansion=InMemoryExpansionTable(expansion or {}),
        edges=InMemoryEdgeStore([]),
        **kwargs,  # type: ignore[arg-type]
    )


class TestRegistry:
    def test_重复注册直接报错而不是静默覆盖(self) -> None:
        reg: Registry[int] = Registry("测试项")
        reg.register("a", 1)
        with pytest.raises(ValueError, match="已注册过同名实现"):
            reg.register("a", 2)

    def test_未知名字的报错里列出已注册的(self) -> None:
        reg: Registry[int] = Registry("测试项")
        reg.register("alpha", 1)
        with pytest.raises(KeyError, match="alpha"):
            reg.get("beta")

    def test_装饰器形式(self) -> None:
        reg: Registry[object] = Registry("测试项")

        @reg.decorator("thing")
        class Thing:
            pass

        assert reg.get("thing") is Thing


class TestCombine:
    def test_三种策略都已注册(self) -> None:
        assert {"max", "sum", "noisy_or"} <= set(COMBINERS)

    def test_max(self) -> None:
        assert combine("max", [0.2, 0.9, 0.5]) == 0.9

    def test_sum(self) -> None:
        assert combine("sum", [0.2, 0.3]) == pytest.approx(0.5)

    def test_noisy_or_多个弱信号可累积(self) -> None:
        assert combine("noisy_or", [0.5, 0.5]) == pytest.approx(0.75)

    def test_noisy_or_有上界1(self) -> None:
        assert combine("noisy_or", [0.9] * 20) <= 1.0

    def test_noisy_or_夹紧越界分量(self) -> None:
        """分量超出 [0,1] 会让结果失去意义，所以夹紧而不是放任。"""
        assert combine("noisy_or", [1.5]) == pytest.approx(1.0)
        assert combine("noisy_or", [-3.0]) == pytest.approx(0.0)

    def test_空输入(self) -> None:
        assert combine("max", []) == 0.0
        assert combine("noisy_or", []) == 0.0

    def test_未知策略立刻报错(self) -> None:
        with pytest.raises(KeyError, match="平均"):
            combine("平均", [0.5])


class TestTermAndUnit:
    def test_空词报错(self) -> None:
        with pytest.raises(ValueError, match="不能为空"):
            Term("")

    def test_空单元名报错(self) -> None:
        with pytest.raises(ValueError, match="不能为空"):
            QueryUnit("")

    def test_默认合成策略是_noisy_or(self) -> None:
        assert QueryUnit("io").combine == "noisy_or"


class TestLexicalSatisfier:
    def test_精确命中(self) -> None:
        ctx = make_context(postings={"buf": [Posting(1, IndexField.NAME)]})
        assert 1 in LexicalSatisfier(terms=(Term("buf"),)).hits("io", ctx)

    def test_经扩展表命中项目里的写法(self) -> None:
        ctx = make_context(
            postings={"buf": [Posting(1, IndexField.NAME)]},
            expansion={"buffer": [Expansion("buf", 0.91, "prefix")]},
        )
        hits = LexicalSatisfier(terms=(Term("buffer"),)).hits("perf", ctx)
        assert "buf←buffer(prefix)" in hits[1][0].detail

    def test_泛词被_icf_下限挡掉(self) -> None:
        """`get` 在 1718 个符号里占 207 个，什么都"相似"。"""
        ctx = make_context(postings={"get": [Posting(i, IndexField.NAME) for i in range(207)]})
        assert LexicalSatisfier(terms=(Term("get"),)).hits("io", ctx) == {}

    def test_稀有词不被挡掉(self) -> None:
        ctx = make_context(
            postings={"login": [Posting(1, IndexField.NAME)]},
        )
        assert LexicalSatisfier(terms=(Term("login"),)).hits("auth", ctx) != {}

    def test_可以限定只查某些域(self) -> None:
        ctx = make_context(
            postings={"buf": [Posting(1, IndexField.NAME), Posting(2, IndexField.DOC)]},
            elements=[make_element(1, "a"), make_element(2, "b")],
        )
        hits = LexicalSatisfier(terms=(Term("buf"),), fields=(IndexField.NAME,)).hits("io", ctx)
        assert set(hits) == {1}

    def test_命中名字比命中文档得分高(self) -> None:
        ctx = make_context(
            postings={"buf": [Posting(1, IndexField.NAME), Posting(2, IndexField.DOC)]},
            elements=[make_element(1, "a"), make_element(2, "b")],
        )
        hits = LexicalSatisfier(terms=(Term("buf"),)).hits("io", ctx)
        assert hits[1][0].score > hits[2][0].score

    def test_打分是连乘_每跳都打折(self) -> None:
        ctx = make_context(
            postings={"buf": [Posting(1, IndexField.DOC)]},
            expansion={"buffer": [Expansion("buf", 0.5, "prefix")]},
        )
        info = ctx.postings.term_info("buf")
        assert info is not None
        hit = LexicalSatisfier(terms=(Term("buffer", weight=0.5),), weight=0.5).hits("io", ctx)[1][
            0
        ]
        expected = 0.5 * 0.5 * 0.5 * ctx.field_weights.weight(IndexField.DOC) * info.icf_ratio
        assert hit.score == pytest.approx(expected)

    def test_查不到的词不产生证据(self) -> None:
        assert LexicalSatisfier(terms=(Term("nope"),)).hits("io", make_context()) == {}


class TestAnnotationSatisfier:
    def test_只查注解相关的域(self) -> None:
        ctx = make_context(
            postings={"cache": [Posting(1, IndexField.NAME), Posting(2, IndexField.ANNOTATION)]},
            elements=[make_element(1, "a"), make_element(2, "b")],
        )
        assert set(AnnotationSatisfier(units=(Term("cache"),)).hits("perf", ctx)) == {2}

    def test_也查注解参数(self) -> None:
        """`@PreAuthorize` 里的权限串、`@Schema` 里的描述都在参数里。"""
        ctx = make_context(postings={"query": [Posting(1, IndexField.ANNOTATION_ARG)]})
        assert set(AnnotationSatisfier(units=(Term("query"),)).hits("q", ctx)) == {1}

    def test_元注解展开_可信度是事实而非估计(self) -> None:
        ctx = make_context(
            postings={"@GetMapping": [Posting(1, IndexField.ANNOTATION)]},
            expansion={"@RequestMapping": [Expansion("@GetMapping", 1.0, "meta")]},
        )
        hits = AnnotationSatisfier(names=("@RequestMapping",)).hits("http", ctx)
        assert "meta" in hits[1][0].detail

    def test_默认权重高于词法(self) -> None:
        assert AnnotationSatisfier().weight > LexicalSatisfier(terms=()).weight


class TestModifierSatisfier:
    def test_只查_modifier_域(self) -> None:
        ctx = make_context(
            postings={"native": [Posting(1, IndexField.MODIFIER), Posting(2, IndexField.NAME)]},
            elements=[make_element(1, "a"), make_element(2, "nativeHelper")],
        )
        assert set(ModifierSatisfier(modifiers=("native",)).hits("perf", ctx)) == {1}

    def test_泛修饰符被_ICF_挡掉(self) -> None:
        """`public` 几乎所有符号都有，区分度趋零。"""
        ctx = make_context(
            postings={"public": [Posting(i, IndexField.MODIFIER) for i in range(1600)]},
            elements=[make_element(i, f"m{i}") for i in range(1600)],
        )
        assert ModifierSatisfier(modifiers=("public",)).hits("u", ctx) == {}

    def test_罕见修饰符保留(self) -> None:
        ctx = make_context(postings={"volatile": [Posting(1, IndexField.MODIFIER)]})
        assert ModifierSatisfier(modifiers=("volatile",)).hits("u", ctx) != {}

    def test_权重介于词法与注解之间(self) -> None:
        """修饰符是语言级事实，比词法准；但注解携带的语义更具体。"""
        assert (
            LexicalSatisfier(terms=()).weight
            < ModifierSatisfier().weight
            < AnnotationSatisfier().weight
        )


class TestEvalUnit:
    def _ctx(self) -> EvalContext:
        return make_context(
            postings={
                "buf": [Posting(1, IndexField.NAME)],
                "cache": [Posting(1, IndexField.ANNOTATION)],
            },
            expansion={"buffer": [Expansion("buf", 0.91, "prefix")]},
        )

    def test_产出的片段没有边(self) -> None:
        unit = QueryUnit("perf", satisfiers=(LexicalSatisfier(terms=(Term("buf"),)),))
        assert eval_unit(unit, self._ctx()).edges == {}

    def test_节点本体来自_symbol_store(self) -> None:
        unit = QueryUnit("perf", satisfiers=(LexicalSatisfier(terms=(Term("buf"),)),))
        assert eval_unit(unit, self._ctx()).nodes[1].name == "flushBuf"

    def test_原始证据一条不删_只追加汇总(self) -> None:
        unit = QueryUnit(
            "perf",
            satisfiers=(
                LexicalSatisfier(terms=(Term("buf"),)),
                AnnotationSatisfier(units=(Term("cache"),)),
            ),
        )
        signals = [h.signal for h in eval_unit(unit, self._ctx()).evidence_for(1).unit_hits]
        assert signals.count("lexical") == 1
        assert signals.count("annotation") == 1
        assert signals.count(Evidence.COMBINED) == 1

    def test_汇总证据不被重复计入总分(self) -> None:
        """回归：`scores` 曾把汇总证据也加进求和，分数翻倍。"""
        unit = QueryUnit("perf", satisfiers=(AnnotationSatisfier(units=(Term("cache"),)),))
        evidence = eval_unit(unit, self._ctx()).evidence_for(1)
        annotation_hit = next(h for h in evidence.unit_hits if h.signal == "annotation")
        assert evidence.scores["perf"] == pytest.approx(annotation_hit.score)

    def test_多信号合成高于单信号(self) -> None:
        lexical_only = QueryUnit("perf", satisfiers=(LexicalSatisfier(terms=(Term("buf"),)),))
        both = QueryUnit(
            "perf",
            satisfiers=(
                LexicalSatisfier(terms=(Term("buf"),)),
                AnnotationSatisfier(units=(Term("cache"),)),
            ),
        )
        ctx = self._ctx()
        one = eval_unit(lexical_only, ctx).evidence_for(1).scores["perf"]
        two = eval_unit(both, ctx).evidence_for(1).scores["perf"]
        assert two > one

    def test_合成策略可选(self) -> None:
        satisfiers = (
            LexicalSatisfier(terms=(Term("buf"),)),
            AnnotationSatisfier(units=(Term("cache"),)),
        )
        ctx = self._ctx()
        by_max = eval_unit(QueryUnit("p", satisfiers=satisfiers, combine="max"), ctx)
        by_or = eval_unit(QueryUnit("p", satisfiers=satisfiers, combine="noisy_or"), ctx)
        assert by_or.evidence_for(1).scores["p"] > by_max.evidence_for(1).scores["p"]

    def test_没有命中时返回空片段(self) -> None:
        unit = QueryUnit("perf", satisfiers=(LexicalSatisfier(terms=(Term("nope"),)),))
        assert not eval_unit(unit, self._ctx())

    def test_symbol_store_里没有的_id_不进片段(self) -> None:
        ctx = make_context(
            postings={"buf": [Posting(999, IndexField.NAME)]},
            elements=[make_element(1, "flushBuf")],
        )
        unit = QueryUnit("perf", satisfiers=(LexicalSatisfier(terms=(Term("buf"),)),))
        assert not eval_unit(unit, ctx)

    def test_satisfier_类型不对时报错(self) -> None:
        unit = QueryUnit("perf", satisfiers=("不是 satisfier",))
        with pytest.raises(TypeError, match="satisfier 类型不对"):
            eval_unit(unit, self._ctx())

    def test_产出的片段可以参与代数运算(self) -> None:
        """单元求值后直接是 Frag，不需要额外转换。"""
        left = eval_unit(
            QueryUnit("a", satisfiers=(LexicalSatisfier(terms=(Term("buf"),)),)), self._ctx()
        )
        right = eval_unit(
            QueryUnit("b", satisfiers=(AnnotationSatisfier(units=(Term("cache"),)),)), self._ctx()
        )
        merged = left & right
        units = {h.unit for h in merged.evidence_for(1).unit_hits}
        assert {"a", "b"} <= units


class TestEvidenceSerialisable:
    """设计文档要求证据「必须可序列化」——它要落盘供人事后查。"""

    def test_证据能_json_往返(self) -> None:
        hit = UnitHit(unit="io", signal="lexical", detail="buf", field="name", score=0.5)
        restored = UnitHit(**json.loads(json.dumps(asdict(hit))))
        assert restored == hit

    def test_带位置的证据也能往返(self) -> None:
        hit = UnitHit(unit="io", signal="lexical", detail="buf", span=(3, 9))
        payload = json.loads(json.dumps(asdict(hit)))
        assert UnitHit(**{**payload, "span": tuple(payload["span"])}) == hit

    def test_整份证据能序列化(self) -> None:
        ev = Evidence(
            unit_hits=(UnitHit("io", "lexical", "buf"),),
            verdicts=(Verdict("llm", "yes", "它是登录入口"),),
        )
        assert json.loads(json.dumps(asdict(ev)))["verdicts"][0]["reason"] == "它是登录入口"
