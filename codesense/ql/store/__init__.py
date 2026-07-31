"""The index access layer.

ABCs in `base`, in-memory implementations in `memory`. Operators depend on
the ABCs only, so storage can change without touching them and tests need no
database.
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
