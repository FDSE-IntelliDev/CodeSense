"""Declarations into postings.

The one rule worth stating: **fields stay separate**. `buffer` in a symbol's
name and `buffer` in its javadoc are evidence of very different strength, and
collapsing them loses the only signal that distinguishes a class *about*
buffers from one that mentions them in passing.

Language-neutral apart from one hook: annotation arguments are shaped by the
language (`@PreAuthorize("@ss.hasPerm('sys:user')")` means nothing to a Go
tokeniser), so the adapter contributes those terms itself.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any

from codesense.lang.base import Declaration
from codesense.text.split import words_of

__all__ = ["DOC_TERM_LIMIT", "PostingTable", "declaration_terms"]

#: Most words kept from a doc. Javadoc can run long, and taking all of it blows
#: up the df of common words.
DOC_TERM_LIMIT = 40

Split = Callable[[str], Sequence[str]]


def declaration_terms(
    declaration: Declaration, split: Split, language: Any = None
) -> list[tuple[str, str]]:
    """Every (term, field) pair one declaration produces.

    ``split`` decides how identifiers break into words; passing a `Splitter`
    rather than `split_identifier` is what adds compound segmentation.
    """
    terms: list[tuple[str, str]] = []
    terms += [(t, "name") for t in split(declaration.name)]
    terms += [(t, "container") for t in split(declaration.container)]
    terms += [(t, "signature") for t in split(declaration.signature)]
    terms += [(w, "doc") for w in words_of(declaration.doc, limit=DOC_TERM_LIMIT)]
    terms += [(m.lower(), "modifier") for m in declaration.modifiers]
    if language is not None and hasattr(language, "annotation_terms"):
        terms += language.annotation_terms(declaration, split)
    return terms


class PostingTable:
    """Accumulates postings, then flattens them for serialisation.

    A class rather than a bare dict because the nesting (term to
    (symbol, field) to count) is easy to get subtly wrong at each call site,
    and because `refine` needs to reach inside it.
    """

    def __init__(self) -> None:
        self._by_term: dict[str, dict[tuple[int, str], int]] = defaultdict(lambda: defaultdict(int))

    def add(self, term: str, symbol_id: int, field: str, count: int = 1) -> None:
        self._by_term[term][(symbol_id, field)] += count

    def add_all(self, pairs: Iterable[tuple[str, str]], symbol_id: int) -> None:
        for term, field in pairs:
            self.add(term, symbol_id, field)

    def terms(self) -> Sequence[str]:
        return tuple(self._by_term)

    def symbols_for(self, term: str) -> Sequence[tuple[int, str]]:
        return tuple(self._by_term.get(term, ()))

    def refine(self, segment: Callable[[str], Sequence[str]]) -> int:
        """Add the pieces of any compound term, keeping the compound.

        Runs **after** the scan rather than during it, and that ordering is the
        point: segmenting `iostat` needs a lexicon built from the whole
        repository, which does not exist until the scan is done. Doing it here
        avoids a second parse -- the postings already say which symbols carry
        the compound, so its pieces inherit exactly those.

        Returns how many terms were segmented, so a build can report it.
        """
        segmented = 0
        for term in list(self._by_term):
            pieces = segment(term)
            if not pieces:
                continue
            segmented += 1
            for piece in pieces:
                for (symbol_id, field), count in self._by_term[term].items():
                    self.add(piece, symbol_id, field, count)
        return segmented

    def flatten(self) -> dict[str, list[dict[str, Any]]]:
        return {
            term: [
                {"symbol_id": sid, "field": field, "tf": tf}
                for (sid, field), tf in sorted(entries.items())
            ]
            for term, entries in sorted(self._by_term.items())
        }

    def count(self) -> int:
        return sum(len(entries) for entries in self._by_term.values())

    def vocabulary(self) -> Mapping[str, int]:
        """Term to the number of distinct symbols carrying it."""
        return {term: len({sid for sid, _ in entries}) for term, entries in self._by_term.items()}

    def __len__(self) -> int:
        return len(self._by_term)
