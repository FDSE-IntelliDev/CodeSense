"""Abstract interfaces for reading the index.

Operators depend only on the ABCs here, never on concrete storage. There are
two implementations: ``memory`` for tests and small projects, and ``sqlite``
as the real IO boundary.

The index splits into four artifacts (``docs/design/09-grounding.md``,
section 5) because their rebuild triggers differ:

    symbols     rebuilt when code changes
    postings    rebuilt when symbols, the splitter or the lexicon change
    terms       recomputed when postings change
    expansion   rebuilt when the prompt, embedding or thresholds change,
                **independently of the code**

Bundled together, everything rebuilds at once; kept apart, retuning a
threshold or swapping an embedding never touches the index.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from codesense.ql.fields import IndexField
from codesense.ql.frag import Edge, Element

__all__ = [
    "EdgeStore",
    "Expansion",
    "ExpansionTable",
    "Posting",
    "PostingIndex",
    "SymbolStore",
    "TermInfo",
]


@dataclass(frozen=True, slots=True)
class Posting:
    """One posting.

    **Stores a symbol id, not a symbol object.** The previous implementation
    inlined whole symbol objects, taking 2.2 MB for 1718 symbols -- about
    1 GB against 11 MB at kernel scale.
    """

    symbol_id: int
    field: IndexField
    tf: int = 1


@dataclass(frozen=True, slots=True)
class TermInfo:
    """Statistics for one term.

    ``icf`` is computed over **symbols**, not call chains -- the two are
    different quantities and must not be mixed.
    """

    term: str
    df: int
    total_symbols: int
    source: str = ""

    @property
    def icf(self) -> float:
        """``log(total symbols / df)``. Zero when df is 0, rather than raising."""
        if self.df <= 0 or self.total_symbols <= 0:
            return 0.0
        return math.log(self.total_symbols / self.df)

    @property
    def icf_ratio(self) -> float:
        """ICF normalised to [0, 1], that is ``icf / log(total symbols)``.

        Scoring uses this rather than raw ICF for two reasons: ``noisy_or``
        needs components in [0, 1], and raw ICF is bounded by ``log(N)``, so
        any threshold on it would drift with project size.
        """
        if self.total_symbols <= 1:
            return 0.0
        return self.icf / math.log(self.total_symbols)


@dataclass(frozen=True, slots=True)
class Expansion:
    """One expansion: from a key to a target, with a score and a reason.

    ``reason`` is not decoration -- it says how much to trust the expansion.
    ``"meta"``, a meta-annotation relation the framework itself declares, is
    a fact and scores 1.0; ``"prefix"`` and ``"ctx"`` are estimates.
    """

    target: str
    score: float
    reason: str = ""


class SymbolStore(ABC):
    """symbol_id to Element. The only source of node objects in a fragment."""

    @abstractmethod
    def get(self, symbol_id: int) -> Element | None:
        """One element, or None if it does not exist."""

    @abstractmethod
    def get_many(self, symbol_ids: Iterable[int]) -> dict[int, Element]:
        """Batch lookup. Missing ids are simply absent from the result."""

    @abstractmethod
    def count(self) -> int:
        """Total symbols. The denominator of `TermInfo.icf`."""


class PostingIndex(ABC):
    """Term to postings, plus per-term statistics.

    This layer stays **exact** and does no fuzzy matching at all. All
    fuzziness lives in `ExpansionTable`, which keeps the index free of new
    data structures and approximation error, and lets thresholds change
    without a rebuild.
    """

    @abstractmethod
    def lookup(self, term: str) -> Sequence[Posting]:
        """Exact lookup of one term; an empty sequence if absent."""

    @abstractmethod
    def term_info(self, term: str) -> TermInfo | None:
        """Statistics for one term, or None if absent."""

    @abstractmethod
    def terms(self) -> Iterable[str]:
        """Every term. Needed when building the expansion table."""


class ExpansionTable(ABC):
    """The precomputed expansion table: canonical term to project spelling.

    Pure lookup at query time -- **no vector maths, no LLM**.
    """

    @abstractmethod
    def expand(self, key: str) -> Sequence[Expansion]:
        """Expand one key; an empty sequence if absent."""


class EdgeStore(ABC):
    """Adjacency access to the graph.

    `hop` needs **paths**, not a reachable set, so this returns edges by
    direction rather than neighbour ids -- callers reconstruct paths from them.
    """

    @abstractmethod
    def out_edges(
        self,
        symbol_id: int,
        *,
        kinds: Sequence[str] | None = None,
        min_confidence: float = 0.0,
    ) -> Sequence[Edge]:
        """Edges leaving this node. ``kinds=None`` means any kind."""

    @abstractmethod
    def in_edges(
        self,
        symbol_id: int,
        *,
        kinds: Sequence[str] | None = None,
        min_confidence: float = 0.0,
    ) -> Sequence[Edge]:
        """Edges arriving at this node, used by backward traversal."""

    @abstractmethod
    def degree(self, symbol_id: int, *, kinds: Sequence[str] | None = None) -> int:
        """Total degree, used to throttle hubs -- utility methods are called
        by everything, and paths through them carry almost no information."""
