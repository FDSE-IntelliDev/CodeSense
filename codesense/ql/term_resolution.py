"""Resolve canonical semantic terms onto exact project index surfaces."""

from __future__ import annotations

from codesense.ql.context import EvalContext
from codesense.ql.store.base import Expansion, Posting

__all__ = [
    "TermResolver",
    "resolved_postings",
    "resolved_surfaces",
    "resolved_symbol_ids",
]


class TermResolver:
    """Query-local exact and expansion lookup with bounded lifetime caches."""

    def __init__(self, ctx: EvalContext) -> None:
        self._ctx = ctx
        self._surfaces: dict[str, tuple[Expansion, ...]] = {}
        self._postings: dict[str, tuple[Posting, ...]] = {}
        self._symbol_ids: dict[str, frozenset[int]] = {}

    def surfaces(self, term: str) -> tuple[Expansion, ...]:
        # Index terms use case-folded exact surfaces, so every query entry
        # point tries that normalization first. The original spelling remains
        # a fallback for legacy case-preserving surfaces such as annotations.
        cached = self._surfaces.get(term)
        if cached is not None:
            return cached

        found: dict[str, Expansion] = {}
        lookup_keys = dict.fromkeys((term.casefold(), term))
        for key in lookup_keys:
            if self._ctx.postings.term_info(key) is not None:
                found.setdefault(key, Expansion(key, 1.0, "exact"))
            for expansion in self._ctx.expansion.expand(key):
                if expansion.target in found:
                    continue
                if self._ctx.postings.term_info(expansion.target) is not None:
                    found[expansion.target] = expansion

        resolved = tuple(found.values())
        self._surfaces[term] = resolved
        return resolved

    def postings(self, term: str) -> tuple[Posting, ...]:
        cached = self._postings.get(term)
        if cached is not None:
            return cached

        found: dict[tuple[int, object], Posting] = {}
        for surface in self.surfaces(term):
            for posting in self._ctx.postings.lookup(surface.target):
                found.setdefault((posting.symbol_id, posting.field), posting)

        resolved = tuple(found.values())
        self._postings[term] = resolved
        return resolved

    def symbol_ids(self, term: str) -> frozenset[int]:
        cached = self._symbol_ids.get(term)
        if cached is not None:
            return cached

        resolved = frozenset(posting.symbol_id for posting in self.postings(term))
        self._symbol_ids[term] = resolved
        return resolved


def resolved_surfaces(
    term: str,
    ctx: EvalContext,
    *,
    resolver: TermResolver | None = None,
) -> tuple[Expansion, ...]:
    """Return exact-first project spellings that have posting statistics."""
    return (resolver or TermResolver(ctx)).surfaces(term)


def resolved_postings(
    term: str,
    ctx: EvalContext,
    *,
    resolver: TermResolver | None = None,
) -> tuple[Posting, ...]:
    """Return stable postings deduplicated across resolved surfaces."""
    return (resolver or TermResolver(ctx)).postings(term)


def resolved_symbol_ids(
    term: str,
    ctx: EvalContext,
    *,
    resolver: TermResolver | None = None,
) -> frozenset[int]:
    """Return symbols matched by one canonical semantic term."""
    return (resolver or TermResolver(ctx)).symbol_ids(term)
