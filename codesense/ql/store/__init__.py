"""索引访问层。

ABC 在 `base`，内存实现在 `memory`。算子只依赖 ABC——
换存储不改算子，测试也不需要数据库。
"""

from codesense.ql.store.base import (
    EdgeStore,
    Expansion,
    ExpansionTable,
    Posting,
    PostingIndex,
    SymbolStore,
    TermInfo,
)
from codesense.ql.store.memory import (
    InMemoryEdgeStore,
    InMemoryExpansionTable,
    InMemoryPostingIndex,
    InMemorySymbolStore,
)

__all__ = [
    "EdgeStore",
    "Expansion",
    "ExpansionTable",
    "InMemoryEdgeStore",
    "InMemoryExpansionTable",
    "InMemoryPostingIndex",
    "InMemorySymbolStore",
    "Posting",
    "PostingIndex",
    "SymbolStore",
    "TermInfo",
]
