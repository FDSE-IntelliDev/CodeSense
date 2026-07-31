"""内存实现。

用途有两个：单元测试的替身，以及小规模项目直接跑。
**不做任何 IO**——从磁盘加载由 ``codesense.ql.store.sqlite`` 或
``scripts/`` 下的加载器负责，构造好之后注入进来。
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence

from codesense.ql.frag import Edge, Element
from codesense.ql.store.base import (
    EdgeStore,
    Expansion,
    ExpansionTable,
    Posting,
    PostingIndex,
    SymbolStore,
    TermInfo,
)

__all__ = [
    "InMemoryEdgeStore",
    "InMemoryExpansionTable",
    "InMemoryPostingIndex",
    "InMemorySymbolStore",
]


class InMemorySymbolStore(SymbolStore):
    def __init__(self, elements: Iterable[Element]) -> None:
        self._by_id: dict[int, Element] = {e.symbol_id: e for e in elements}

    def get(self, symbol_id: int) -> Element | None:
        return self._by_id.get(symbol_id)

    def get_many(self, symbol_ids: Iterable[int]) -> dict[int, Element]:
        return {sid: e for sid in symbol_ids if (e := self._by_id.get(sid)) is not None}

    def count(self) -> int:
        return len(self._by_id)


class InMemoryPostingIndex(PostingIndex):
    """``df`` 由 postings 直接数出来，不单独存——存两份就会不一致。"""

    def __init__(
        self,
        postings: Mapping[str, Sequence[Posting]],
        *,
        total_symbols: int,
        sources: Mapping[str, str] | None = None,
    ) -> None:
        self._postings = {term: tuple(items) for term, items in postings.items()}
        self._total_symbols = total_symbols
        self._sources = dict(sources or {})

    def lookup(self, term: str) -> Sequence[Posting]:
        return self._postings.get(term, ())

    def term_info(self, term: str) -> TermInfo | None:
        items = self._postings.get(term)
        if items is None:
            return None
        return TermInfo(
            term=term,
            df=len({p.symbol_id for p in items}),
            total_symbols=self._total_symbols,
            source=self._sources.get(term, ""),
        )

    def terms(self) -> Iterable[str]:
        return self._postings.keys()


class InMemoryExpansionTable(ExpansionTable):
    def __init__(self, table: Mapping[str, Sequence[Expansion]]) -> None:
        self._table = {
            key: tuple(sorted(items, key=lambda e: -e.score)) for key, items in table.items()
        }

    def expand(self, key: str) -> Sequence[Expansion]:
        return self._table.get(key, ())


class InMemoryEdgeStore(EdgeStore):
    def __init__(self, edges: Iterable[Edge]) -> None:
        self._out: dict[int, list[Edge]] = defaultdict(list)
        self._in: dict[int, list[Edge]] = defaultdict(list)
        for edge in edges:
            self._out[edge.source_id].append(edge)
            self._in[edge.target_id].append(edge)

    def out_edges(
        self,
        symbol_id: int,
        *,
        kinds: Sequence[str] | None = None,
        min_confidence: float = 0.0,
    ) -> Sequence[Edge]:
        return _select(self._out.get(symbol_id, ()), kinds, min_confidence)

    def in_edges(
        self,
        symbol_id: int,
        *,
        kinds: Sequence[str] | None = None,
        min_confidence: float = 0.0,
    ) -> Sequence[Edge]:
        return _select(self._in.get(symbol_id, ()), kinds, min_confidence)

    def degree(self, symbol_id: int, *, kinds: Sequence[str] | None = None) -> int:
        return len(_select(self._out.get(symbol_id, ()), kinds, 0.0)) + len(
            _select(self._in.get(symbol_id, ()), kinds, 0.0)
        )


def _select(
    edges: Sequence[Edge], kinds: Sequence[str] | None, min_confidence: float
) -> tuple[Edge, ...]:
    allowed = None if kinds is None else frozenset(kinds)
    return tuple(
        e
        for e in edges
        if (allowed is None or e.kind in allowed) and e.confidence >= min_confidence
    )
