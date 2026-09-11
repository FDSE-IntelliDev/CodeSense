"""Satisfiers: the ways a query unit can be satisfied.

Deciding whether code is "about performance" is only weakly served by
lexical matching. Annotations, structural position, modifiers and semantic
similarity can satisfy the same unit, so units are decoupled from lexical
matching and `Satisfier` is where that decoupling lands.

Design: ``docs/design/04-query-unit.md``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import ClassVar

from codesense.ql.context import EvalContext
from codesense.ql.fields import IndexField
from codesense.ql.frag import UnitHit
from codesense.ql.registry import Registry
from codesense.ql.store.base import Expansion, TermInfo
from codesense.ql.term_resolution import TermResolver
from codesense.ql.unit import Term

__all__ = ["SATISFIERS", "Satisfier", "collect_term_hits"]

HitsBySymbol = Mapping[int, tuple[UnitHit, ...]]

#: Marks a surface form that the caller supplied rather than one expanded to.
_EXACT = "exact"

#: Satisfier types by name, so compiled output can construct them.
SATISFIERS: Registry[type[Satisfier]] = Registry("satisfier")


class Satisfier(ABC):
    """Evaluate a unit's condition into which symbols hit, and how strongly.

    Implementations must declare ``signal``; it goes into the evidence so a
    result can say whether an element was matched lexically or by annotation.
    """

    signal: ClassVar[str]

    @abstractmethod
    def hits(self, unit: str, ctx: EvalContext) -> HitsBySymbol:
        """Evaluate. Returns symbol_id to all evidence this satisfier produced."""


def collect_term_hits(
    *,
    unit: str,
    signal: str,
    terms: Sequence[Term],
    ctx: EvalContext,
    weight: float,
    fields: Sequence[IndexField] | None,
) -> HitsBySymbol:
    """Term to expansion to postings to evidence. Shared by lexical-style
    satisfiers.

    Only the **second hop** happens here (canonical term to the project's
    actual spelling). The first hop (`performance` to `cache`/`buffer`) is
    produced at compile time by the same LLM call that splits the query, so
    the `terms` arriving at runtime are already canonical. See
    ``docs/design/09-grounding.md``, section 6.

    Scoring **multiplies**, so each hop attenuates. After an association, an
    abbreviation mapping, and landing in doc rather than name, a piece of
    evidence should rank far below a direct hit on the name.
    """
    allowed = None if fields is None else frozenset(fields)
    found: dict[int, list[UnitHit]] = defaultdict(list)
    resolver = TermResolver(ctx)

    for term in terms:
        for surface in resolver.surfaces(term.value):
            info = ctx.postings.term_info(surface.target)
            if info is None or _too_generic(surface, info, ctx):
                continue
            for posting in ctx.postings.lookup(surface.target):
                if allowed is not None and posting.field not in allowed:
                    continue
                score = (
                    weight
                    * term.weight
                    * surface.score
                    * ctx.field_weights.weight(posting.field)
                    * info.icf_ratio
                )
                if score < ctx.min_hit_score:
                    continue
                found[posting.symbol_id].append(
                    UnitHit(
                        unit=unit,
                        signal=signal,
                        detail=_detail(term, surface),
                        field=str(posting.field),
                        score=score,
                    )
                )
    return {sid: tuple(hits) for sid, hits in found.items()}


def _too_generic(surface: Expansion, info: TermInfo, ctx: EvalContext) -> bool:
    """The ICF floor governs **expanded terms only, never the query's own**.

    A correction the benchmark forced. Applying the floor uniformly backfired
    badly at scale: netty has 42221 symbols, `buf` appears in 15.4% of them
    and `allocator` in 3.6%, so both fell below the floor and were discarded
    -- on a query about buffer allocation. `PooledByteBufAllocator` became
    unreachable as a result.

    The floor exists to stop noise from expansion (``docs/design/
    09-grounding.md``, section 7: low-ICF terms do not enter the expansion
    table). It was **never** meant to discard terms the caller asked for.
    A low-ICF query term should be **downweighted** -- `icf_ratio` already
    multiplies into the score -- not **excluded**.
    """
    if surface.reason == _EXACT:
        return False
    return info.icf_ratio < ctx.icf_floor


def _detail(term: Term, surface: Expansion) -> str:
    """Readable evidence: what was hit, and why it counts as a hit."""
    if surface.reason == _EXACT:
        return term.value
    return f"{surface.target}←{term.value}({surface.reason})"
