"""Breaking identifiers into words.

Two layers, because one is not enough:

    delimiter   case and separator boundaries, via Ronin (``srctoolkit``).
                Gets `parseHTTPResponse` and, from its frequency table,
                `netlink` -> `net link` and `strlen` -> `str len`.
    viterbi     dynamic-programming segmentation for what is left. Ronin
                still returns `iostat`, `nfsd`, `printk` and `kmalloc`
                whole -- measured, not assumed -- and a query for "io
                statistics" cannot reach a symbol indexed only as `iostat`.

**The second layer needs no training.** Its unigram costs come from the
project's own vocabulary: the words the delimiter *did* split out, counted.
A repository that writes `io` and `stat` all over its identifiers thereby
teaches the splitter that `iostat` is two words, and one that never uses
`stat` alone leaves it whole -- which is the right answer for that repository.
No external corpus, no model file, nothing to fit.

The whole word is always kept alongside its parts. `iostat` is a real thing to
search for, and dropping it in favour of `io`+`stat` would trade one kind of
miss for another.

Design: ``docs/design/09-grounding.md``.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping, Sequence

__all__ = [
    "Splitter",
    "split_identifier",
    "words_of",
]

_WORD = re.compile(r"[A-Za-z]{2,}")

#: Splits on any non-letter, then on camel-case boundaries. Only a fallback:
#: it cannot split `netlink`, which is what Ronin's frequency table is for.
_NON_ALPHA = re.compile(r"[^A-Za-z]+")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")

#: Shortest token worth attempting to segment. Below this there is nothing to
#: gain and plenty to get wrong -- `api` and `str` are words, not compounds.
MIN_COMPOUND = 6

#: Shortest piece a segmentation may produce.
#:
#: Two, not three: `io`, `db`, `ui` and `id` are real words in code, and a
#: floor of three keeps `iostat` whole -- the very case this layer exists for.
#: Single letters are still excluded, which is what stops `nfsd` becoming
#: `nfs` + `d`. Two-letter pieces are safe because every piece must already be
#: in the lexicon, so one survives only where the repository really uses it as
#: a word.
MIN_PIECE = 2

#: How much cheaper a segmentation must be than leaving the token whole. The
#: comparison is between total costs, so this is a log-probability margin: the
#: split has to be substantially more likely, not merely more likely.
SPLIT_MARGIN = 1.0

#: Cost charged to a piece the lexicon has never seen. High enough that any
#: segmentation containing one loses to leaving the token whole.
UNKNOWN_COST = 30.0


def split_identifier(name: str) -> list[str]:
    """Split an identifier into lowercase words, using boundaries alone.

    ``parseHTTPResponse`` becomes ``parse http response``. Non-alphabetic
    tokens are dropped -- ``utf8`` yields ``utf``, since a bare ``8`` matches
    nothing useful and only inflates the vocabulary.

    This is the layer everything can use, including code with no corpus to
    hand. For compound splitting, build a `Splitter`.
    """
    try:
        from srctoolkit.delimiter import Delimiter

        return [t for t in Delimiter.split_camel(name).split() if t.isalpha()]
    except ImportError:
        text = _CAMEL.sub(" ", _NON_ALPHA.sub(" ", name))
        return [t.lower() for t in text.split() if t]


def words_of(text: str, limit: int | None = None) -> list[str]:
    """Lowercase words from free text (javadoc, annotation arguments).

    Single letters are dropped: they are loop variables and article noise, and
    they would collide with real one-letter type parameters.
    """
    found = [w.lower() for w in _WORD.findall(text)]
    return found[:limit] if limit is not None else found


class Splitter:
    """Boundary splitting, plus Viterbi segmentation of what survives it.

    Built from a lexicon rather than fitted to one:

        splitter = Splitter.from_tokens(every_token_the_delimiter_produced)
        splitter.segment("iostat")      # -> ("io", "stat") if the repo says so

    The lexicon is a word-to-count mapping. `from_tokens` builds it by
    counting, which is the whole of the "training".
    """

    def __init__(self, lexicon: Mapping[str, int]) -> None:
        total = sum(lexicon.values()) or 1
        scale = math.log(total)
        #: Cost is ``-log p(word)``, so a frequent word is cheap and Viterbi's
        #: cheapest path is the most likely segmentation.
        self._cost = {word: scale - math.log(count) for word, count in lexicon.items() if count > 0}
        self._longest = max((len(w) for w in self._cost), default=0)

    @classmethod
    def from_tokens(cls, tokens: Iterable[str]) -> Splitter:
        """Count tokens into a lexicon.

        Tokens are what the delimiter produced across the repository, so the
        lexicon holds exactly the words this project uses -- which is why the
        result is project-specific and needs no external list.
        """
        counts: dict[str, int] = {}
        for token in tokens:
            if len(token) >= MIN_PIECE and token.isalpha():
                counts[token] = counts.get(token, 0) + 1
        return cls(counts)

    def split(self, name: str) -> list[str]:
        """Split an identifier, segmenting any compound the delimiter left.

        The whole token is kept **in addition to** its pieces: `iostat` yields
        ``iostat io stat``, so both a query saying `iostat` and one saying
        "io statistics" can reach the symbol.
        """
        found: list[str] = []
        for token in split_identifier(name):
            found.append(token)
            found.extend(self.segment(token))
        return _dedupe(found)

    def segment(self, token: str) -> tuple[str, ...]:
        """The pieces of a compound, or empty if it should stay whole.

        Returns empty rather than ``(token,)`` so callers can tell "this was
        segmented" from "this was not", which is the difference between adding
        postings and adding nothing.
        """
        if len(token) < MIN_COMPOUND or not token.isalpha() or not self._cost:
            return ()
        pieces = self._viterbi(token)
        if len(pieces) < 2:
            return ()
        # A token that is itself common is not a compound: `interface` splits
        # into `inter` + `face` quite happily, and doing so is wrong.
        whole = self._cost.get(token, UNKNOWN_COST)
        if sum(self._cost.get(p, UNKNOWN_COST) for p in pieces) + SPLIT_MARGIN > whole:
            return ()
        return pieces

    def _viterbi(self, token: str) -> tuple[str, ...]:
        """Cheapest segmentation, by dynamic programming over prefixes.

        ``best[i]`` is the cost of the cheapest segmentation of the first *i*
        characters; each position tries every piece ending there. That is
        O(len x longest word), which for identifiers is nothing.
        """
        n = len(token)
        best = [0.0] + [math.inf] * n
        back = [0] * (n + 1)
        for end in range(MIN_PIECE, n + 1):
            lowest = min(end, self._longest)
            for size in range(MIN_PIECE, lowest + 1):
                start = end - size
                if best[start] == math.inf:
                    continue
                cost = self._cost.get(token[start:end])
                if cost is None:
                    continue
                total = best[start] + cost
                if total < best[end]:
                    best[end] = total
                    back[end] = start
        if best[n] == math.inf:
            return ()
        pieces: list[str] = []
        end = n
        while end > 0:
            start = back[end]
            pieces.append(token[start:end])
            end = start
        return tuple(reversed(pieces))

    def known(self, word: str) -> bool:
        return word in self._cost

    def __len__(self) -> int:
        return len(self._cost)


def _dedupe(words: Sequence[str]) -> list[str]:
    """Each word once, in first-seen order."""
    seen: dict[str, None] = {}
    for word in words:
        seen.setdefault(word, None)
    return list(seen)
