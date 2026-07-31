"""Lexical, annotation and modifier satisfiers.

They share one term-to-expansion-to-postings path and differ only in which
fields they probe, and in annotations first passing through meta-annotation
expansion.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import ClassVar

from codesense.ql.context import EvalContext
from codesense.ql.fields import IndexField
from codesense.ql.satisfiers.base import SATISFIERS, HitsBySymbol, Satisfier, collect_term_hits
from codesense.ql.unit import Term

__all__ = ["AnnotationSatisfier", "LexicalSatisfier", "ModifierSatisfier"]

#: Annotation-related fields. Both the name and the arguments matter --
#: the permission string in `@PreAuthorize("@ss.hasPerm('sys:user:query')")`
#: and the prose in `@Schema(description=...)` live in the arguments, and
#: indexing only names would discard them.
_ANNOTATION_FIELDS = (IndexField.ANNOTATION, IndexField.ANNOTATION_ARG)


@SATISFIERS.decorator("lexical")
@dataclass(frozen=True, slots=True)
class LexicalSatisfier(Satisfier):
    """Lexical matching in identifiers, signatures and documentation.

    The default weight is low (0.5) because lexical is the **weakest** kind
    of evidence. A field named `cacheKey` matches `cache` while having
    nothing to do with performance, and only combining with other signals
    pushes it back down.
    """

    signal: ClassVar[str] = "lexical"

    terms: tuple[Term, ...]
    weight: float = 0.5
    fields: tuple[IndexField, ...] | None = None

    def hits(self, unit: str, ctx: EvalContext) -> HitsBySymbol:
        return collect_term_hits(
            unit=unit,
            signal=self.signal,
            terms=self.terms,
            ctx=ctx,
            weight=self.weight,
            fields=self.fields,
        )


@SATISFIERS.decorator("annotation")
@dataclass(frozen=True, slots=True)
class AnnotationSatisfier(Satisfier):
    """Annotation matching.

    Matches the **segmented units of an annotation name**, not a literal
    regex, so a project's own `@AppCache` and `@CacheAside` match a `cache`
    unit alongside `@Cacheable` without an LLM generating regexes.

    ``names`` may name annotations directly (``"@Transactional"``); they
    first pass through the expansion table for **meta-annotation expansion**,
    turning `@RequestMapping` into `@GetMapping` and `@PostMapping`. That
    relation is a fact the framework declares in source, not an estimate, so
    it scores 1.0.

    The default weight is high (0.9): in Java and Spring projects annotations
    are close to the strongest semantic signal there is -- `@RestController`
    says outright that something is an HTTP entry point, more reliably than
    any keyword.
    """

    signal: ClassVar[str] = "annotation"

    units: tuple[Term, ...] = ()
    names: tuple[str, ...] = ()
    weight: float = 0.9

    def hits(self, unit: str, ctx: EvalContext) -> HitsBySymbol:
        return collect_term_hits(
            unit=unit,
            signal=self.signal,
            terms=(*self.units, *_as_terms(self.names)),
            ctx=ctx,
            weight=self.weight,
            fields=_ANNOTATION_FIELDS,
        )


def _as_terms(names: Sequence[str]) -> tuple[Term, ...]:
    """Named annotations are treated as literal terms; the expansion table
    handles meta-annotations."""
    return tuple(Term(value=name, source="literal") for name in names)


@SATISFIERS.decorator("modifier")
@dataclass(frozen=True, slots=True)
class ModifierSatisfier(Satisfier):
    """Language-level modifiers: `static`, `abstract`, `synchronized`,
    `native` and so on.

    Modifiers are **facts**, not guesses -- asking for asynchronous write
    paths, `synchronized` and `volatile` are certain while whether a name
    contains "async" is inference. Hence a default weight of 0.6, above
    lexical at 0.5 and below annotation at 0.9, since an annotation carries
    more specific meaning than a modifier.

    No expansion is needed: `static` is `static`, with none of the surface
    variation of `buf` against `buffer`. The expansion table has no such keys
    anyway, so reusing the same path is safe.

    ICF separates strong from weak by itself: `public` is on nearly every
    symbol and falls below the floor, while `native` and `volatile` are rare
    and are exactly the informative ones.
    """

    signal: ClassVar[str] = "modifier"

    modifiers: tuple[str, ...] = ()
    weight: float = 0.6

    def hits(self, unit: str, ctx: EvalContext) -> HitsBySymbol:
        return collect_term_hits(
            unit=unit,
            signal=self.signal,
            terms=tuple(Term(value=m, source="literal") for m in self.modifiers),
            ctx=ctx,
            weight=self.weight,
            fields=(IndexField.MODIFIER,),
        )
