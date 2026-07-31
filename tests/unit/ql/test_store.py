"""`codesense.ql.store` 与 `codesense.ql.fields` 的单元测试。"""

from __future__ import annotations

import math

import pytest

from codesense.ql import DEFAULT_FIELD_WEIGHTS, Edge, Element, FieldWeights, IndexField
from codesense.ql.store import (
    Expansion,
    InMemoryEdgeStore,
    InMemoryExpansionTable,
    InMemoryPostingIndex,
    InMemorySymbolStore,
    Posting,
)


def make_element(symbol_id: int) -> Element:
    return Element(
        symbol_id=symbol_id,
        name=f"sym{symbol_id}",
        kind="method",
        file="A.java",
        span=(symbol_id, symbol_id + 1),
    )


class TestIndexField:
    def test_可以直接当字符串用(self) -> None:
        assert IndexField.NAME == "name"
        assert f"{IndexField.DOC}" == "doc"

    def test_取值受约束(self) -> None:
        with pytest.raises(ValueError, match="body"):
            IndexField("body")


class TestFieldWeights:
    def test_名字域权重最高(self) -> None:
        w = DEFAULT_FIELD_WEIGHTS
        assert w.weight(IndexField.NAME) > w.weight(IndexField.ANNOTATION)
        assert w.weight(IndexField.ANNOTATION) > w.weight(IndexField.DOC)

    def test_未知域记0而不是静默当成1(self) -> None:
        assert DEFAULT_FIELD_WEIGHTS.weight("body") == 0.0

    def test_接受字符串也接受枚举(self) -> None:
        w = DEFAULT_FIELD_WEIGHTS
        assert w.weight("name") == w.weight(IndexField.NAME)

    def test_可以注入自定义权重(self) -> None:
        assert FieldWeights(name=0.5).weight(IndexField.NAME) == 0.5

    def test_权重表是只读的(self) -> None:
        with pytest.raises(TypeError):
            DEFAULT_FIELD_WEIGHTS.as_mapping()["name"] = 9.0  # type: ignore[index]


class TestSymbolStore:
    def test_取单个(self) -> None:
        store = InMemorySymbolStore([make_element(1)])
        assert store.get(1) is not None
        assert store.get(999) is None

    def test_批量取_缺失的直接不出现(self) -> None:
        store = InMemorySymbolStore([make_element(1), make_element(2)])
        assert set(store.get_many([1, 2, 999])) == {1, 2}

    def test_count_是符号总数(self) -> None:
        assert InMemorySymbolStore([make_element(1), make_element(2)]).count() == 2


class TestPostingIndex:
    def test_精确查找(self) -> None:
        idx = InMemoryPostingIndex({"buf": [Posting(1, IndexField.NAME)]}, total_symbols=10)
        assert len(idx.lookup("buf")) == 1

    def test_查不到返回空序列而不是_None(self) -> None:
        idx = InMemoryPostingIndex({}, total_symbols=10)
        assert idx.lookup("buffer") == ()

    def test_不做任何模糊匹配(self) -> None:
        """索引保持精确，模糊性全在扩展表。"""
        idx = InMemoryPostingIndex({"buf": [Posting(1, IndexField.NAME)]}, total_symbols=10)
        assert idx.lookup("buffer") == ()

    def test_df_按不同符号数而不是_posting_数(self) -> None:
        """同一个符号在 name 和 doc 都命中，只算一次。"""
        idx = InMemoryPostingIndex(
            {"buf": [Posting(1, IndexField.NAME), Posting(1, IndexField.DOC)]},
            total_symbols=10,
        )
        info = idx.term_info("buf")
        assert info is not None
        assert info.df == 1

    def test_icf_是符号级的(self) -> None:
        idx = InMemoryPostingIndex(
            {"get": [Posting(i, IndexField.NAME) for i in range(207)]},
            total_symbols=1718,
        )
        info = idx.term_info("get")
        assert info is not None
        assert info.icf == pytest.approx(math.log(1718 / 207))

    def test_泛词的_icf_低于稀有词(self) -> None:
        idx = InMemoryPostingIndex(
            {
                "get": [Posting(i, IndexField.NAME) for i in range(207)],
                "login": [Posting(i, IndexField.NAME) for i in range(20)],
            },
            total_symbols=1718,
        )
        assert idx.term_info("get").icf < idx.term_info("login").icf  # type: ignore[union-attr]

    def test_未知_term_的统计量是_None(self) -> None:
        assert InMemoryPostingIndex({}, total_symbols=10).term_info("x") is None

    def test_df为0时_icf记0不抛(self) -> None:
        idx = InMemoryPostingIndex({"x": []}, total_symbols=10)
        assert idx.term_info("x").icf == 0.0  # type: ignore[union-attr]

    def test_可以遍历全部_term(self) -> None:
        idx = InMemoryPostingIndex({"a": [], "b": []}, total_symbols=10)
        assert set(idx.terms()) == {"a", "b"}


class TestExpansionTable:
    def test_按分数从高到低(self) -> None:
        table = InMemoryExpansionTable(
            {"buffer": [Expansion("bfr", 0.72, "subseq"), Expansion("buf", 0.91, "prefix")]}
        )
        assert [e.target for e in table.expand("buffer")] == ["buf", "bfr"]

    def test_查不到返回空序列(self) -> None:
        assert InMemoryExpansionTable({}).expand("buffer") == ()

    def test_理由随扩展一起带出(self) -> None:
        table = InMemoryExpansionTable({"@RequestMapping": [Expansion("@GetMapping", 1.0, "meta")]})
        assert table.expand("@RequestMapping")[0].reason == "meta"


class TestEdgeStore:
    def _store(self) -> InMemoryEdgeStore:
        return InMemoryEdgeStore(
            [
                Edge(1, 2, "calls", confidence=0.95),
                Edge(1, 3, "calls_virtual", confidence=0.6),
                Edge(2, 3, "contains", confidence=1.0),
            ]
        )

    def test_出边(self) -> None:
        assert {e.target_id for e in self._store().out_edges(1)} == {2, 3}

    def test_入边(self) -> None:
        assert {e.source_id for e in self._store().in_edges(3)} == {1, 2}

    def test_按类型过滤(self) -> None:
        assert {e.target_id for e in self._store().out_edges(1, kinds=["calls"])} == {2}

    def test_按置信度过滤_挡掉虚分派(self) -> None:
        edges = self._store().out_edges(1, min_confidence=0.9)
        assert {e.kind for e in edges} == {"calls"}

    def test_孤点返回空序列(self) -> None:
        assert self._store().out_edges(999) == ()

    def test_度数含出入两侧(self) -> None:
        assert self._store().degree(3) == 2

    def test_度数可以按类型算(self) -> None:
        assert self._store().degree(1, kinds=["calls"]) == 1
