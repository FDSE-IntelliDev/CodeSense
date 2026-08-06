"""Grounding general vocabulary in the spellings a project actually uses.

The gap this closes: a query says `buffer`, netty writes `buf`; a query says
`backpressure`, netty writes `watermark` and `writability`. The index is exact
by design, so something has to map one to the other, and that something is the
expansion table.

**Three strategies, cheapest first.** Which to use is a real trade-off, not a
detail:

    lexical    abbreviation and prefix rules against the project vocabulary.
               No model, no dependency, deterministic. Catches `buf`/`buffer`
               and `msg`/`message`; catches nothing where the two spellings
               share no letters.
    vectors    the above, plus nearest neighbours in a pretrained fastText
               space restricted to the project's vocabulary. Catches
               semantic neighbours that share no orthography. Needs the model
               file and roughly 8GB of RAM.
    finetune   the above, plus a compact project Word2Vec trained from the
               repository corpus. The default lightweight profile uses a
               compact base package and a 2GB budget; full FastText loading is
               opt-in through full_force or warn_full.

**We are not asking embeddings for synonyms.** They do not deliver them --
measured, the neighbours of `pool` in cc.en.300 are morphological variants
(`pools`, `pooled`), not `arena` or `chunk`. What they do deliver is *high
semantic proximity*, and combining that with lexical rules is what makes the
result usable. Scores reflect that: a lexical rule outranks a vector
neighbour, because the rule is a near-certainty and the neighbour is an
estimate.

**This runs at build time and its output is a few hundred KB.** Nothing on
the query path ever loads a 7GB model.

Design: ``docs/design/09-grounding.md``.
"""

from __future__ import annotations

import bisect
import logging
import math

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "GroundingConfig",
    "GroundingOutcome",
    "STRATEGIES",
    "VectorSpace",
    "ground_lexically",
    "ground_vocabulary",
]

_log = logging.getLogger(__name__)

STRATEGIES = ("lexical", "vectors", "finetune")

#: Score for an abbreviation or prefix mapping. High but never 1.0 -- `buf`
#: really is `buffer` in netty, but `str` is `string` and also `stream`.
LEXICAL_SCORE = 0.75

#: Ceiling for a vector neighbour. Deliberately below `LEXICAL_SCORE`: an
#: orthographic rule is close to certain, a cosine is an estimate.
VECTOR_CEILING = 0.6

#: Below this cosine a neighbour is noise. The random word-pair baseline
#: `scripts/probe_embeddings.py` reports puts chance around 0.3, but 0.45 is
#: still far too permissive in practice: the band from 0.45 to 0.55 is almost
#: entirely morphological variants and topic-adjacent words, so the bar sits
#: where a neighbour starts being worth a lookup.
MIN_COSINE = 0.55

#: An expansion scoring below this cannot change any ranking -- the score
#: multiplies into the total, so 0.001 is a no-op that still costs a postings
#: lookup. Dropping these is what keeps the table honest about its own size.
MIN_ENTRY_SCORE = 0.05

#: Vowels dropped when testing whether one word abbreviates another.
_VOWELS = "aeiou"

#: Score for a subsequence match. Below the other rules: `ctx`/`context` is
#: real, but the same rule also links `cat` to `cast`.
SUBSEQ_SCORE = 0.5

#: How much longer than the abbreviation the full form may be. Without a
#: ceiling, `cat` reaches `concatenate`.
_SUBSEQ_RATIO = 3

#: Longest word the subsequence rule will treat as an abbreviation.
#:
#: Real ones are short -- `ctx`, `msg`, `cfg`, `impl`, `mgr`. Past five
#: characters a subsequence match is almost always coincidence: measured on
#: netty, the rule linked `allocation` to `action`, which shares six letters in
#: order and no meaning at all.
_SUBSEQ_MAX_SHORT = 5


@dataclass(frozen=True, slots=True)
class GroundingConfig:
    """Knobs for building the table. Pipeline parameters, never a config file."""

    strategy: str = "lexical"
    model_path: Path | None = None
    #: How much general English to consider as query-side keys. The pretrained
    #: vocabulary is ranked by corpus frequency, and past a few hundred
    #: thousand the vectors are noise anyway.
    general_vocab: int = 50_000
    #: Skipped from the head of the general vocabulary. The most frequent words
    #: in any English corpus are function words -- `she`, `would`, `people` --
    #: and no code query is built from them. Without this the table fills with
    #: entries like `she -> then`.
    skip_head: int = 3_000
    #: A project term must appear on at least this many symbols to be worth
    #: expanding to. Singletons are typically typos or one-off locals.
    min_df: int = 2
    #: A project term must **discriminate** to be worth expanding to.
    #: `codesense.ql.satisfiers` discards any expansion below its `icf_floor`
    #: at query time, so generating those is pure waste: the entry can only
    #: ever be dropped. Kept in step with `EvalContext.icf_floor`.
    icf_floor: float = 0.34
    #: Most project spellings one general word may expand to. Unbounded fan-out
    #: turns one query word into hundreds of postings lookups.
    max_targets: int = 4
    min_cosine: float = MIN_COSINE
    #: Training passes over the repository corpus during fine-tuning.
    epochs: int = 5
    workers: int = 8

    def __post_init__(self) -> None:
        if self.strategy not in STRATEGIES:
            raise ValueError(f"strategy must be one of {STRATEGIES}, got {self.strategy!r}")
        if self.strategy != "lexical" and self.model_path is None:
            raise ValueError(f"strategy {self.strategy!r} needs model_path")


@dataclass(frozen=True, slots=True)
class GroundingOutcome:
    table: dict[str, list[tuple[str, float, str]]]
    profile: str
    status: str = "ready"
    reason: str = ""


def ground_vocabulary(
    project_terms: Mapping[str, int],
    total_symbols: int,
    *,
    config: GroundingConfig,
    sentences: Sequence[Sequence[str]] | None = None,
    space: VectorSpace | None = None,
    finetune_config: object | None = None,
) -> dict[str, list[tuple[str, float, str]]]:
    """Build the expansion table: general word -> project spellings.

    ``project_terms`` maps term to document frequency and ``total_symbols`` is
    what df is out of -- together they give the same ICF the query path
    computes. ``sentences`` is the repository corpus, needed only when
    fine-tuning.

    Returns a table shaped for `codesense.indexing.expansion.build_expansion_table`.
    Falls back to the lexical strategy, with a warning, if the vector work
    fails -- an index without grounding is worse than one with, but far better
    than no index at all.
    """
    return ground_vocabulary_result(
        project_terms,
        total_symbols,
        config=config,
        sentences=sentences,
        space=space,
        finetune_config=finetune_config,
    ).table


def ground_vocabulary_result(
    project_terms: Mapping[str, int],
    total_symbols: int,
    *,
    config: GroundingConfig,
    sentences: Iterable[Sequence[str]] | None = None,
    space: VectorSpace | None = None,
    finetune_config: object | None = None,
) -> GroundingOutcome:
    """Build grounding and expose whether optional finetuning degraded."""
    targets = discriminating_targets(project_terms, total_symbols, config)
    if not targets:
        return GroundingOutcome({}, config.strategy)

    table = ground_lexically(targets, config=config)
    if config.strategy == "lexical":
        return GroundingOutcome(table, "lexical")

    if config.strategy == "finetune" and finetune_config is not None:
        from codesense.indexing.finetune import run_finetune

        try:
            result = run_finetune(
                project_terms=project_terms,
                targets=targets,
                lexical=table,
                corpus=sentences or (),
                config=finetune_config,
                min_cosine=config.min_cosine,
                max_targets=config.max_targets,
            )
        except Exception as exc:  # noqa: BLE001 -- optional grounding may degrade
            if finetune_config.strict_profile:
                raise
            _log.exception("finetune grounding failed; falling back to lexical rules only")
            return GroundingOutcome(table, finetune_config.profile, "degraded", str(exc))
        return GroundingOutcome(
            _merge(table, result.expansion, config.max_targets), finetune_config.profile
        )

    try:
        space = space or VectorSpace.load(config.model_path, config=config)
        if config.strategy == "finetune":
            legacy_sentences = list(sentences or ())
            space.finetune(legacy_sentences, config=config)
        vectors = space.ground(targets, config=config)
    except Exception:  # noqa: BLE001 -- grounding is an enhancement, not a gate
        _log.exception("vector grounding failed; falling back to lexical rules only")
        return GroundingOutcome(table, config.strategy, "degraded", "vector grounding failed")
    return GroundingOutcome(_merge(table, vectors, config.max_targets), config.strategy)


def discriminating_targets(
    project_terms: Mapping[str, int], total_symbols: int, config: GroundingConfig
) -> dict[str, int]:
    """The project terms worth being an expansion target.

    Two filters, and the second is the one that matters. A term appearing on
    nearly every symbol -- `the` and `if` reach the index through javadoc, and
    `get` through method names -- carries no information about *which* symbol
    is wanted. `codesense.ql.satisfiers` already refuses to follow an expansion
    onto such a term, so an entry pointing at one is guaranteed dead weight.

    Filtering here rather than at query time is what keeps the table small
    enough to read.

    ``total_symbols`` is the same denominator `TermInfo.icf_ratio` uses. It has
    to be: a build-time gate computed against a different total would keep
    entries the query-time gate then throws away, which is the bug this
    function exists to fix.
    """
    if total_symbols <= 1:
        return {}
    scale = math.log(total_symbols)
    found: dict[str, int] = {}
    for term, df in project_terms.items():
        if df < config.min_df or len(term) < 2 or df > total_symbols:
            continue
        if math.log(total_symbols / df) / scale >= config.icf_floor:
            found[term] = df
    return found


def ground_lexically(
    project_terms: Mapping[str, int], *, config: GroundingConfig
) -> dict[str, list[tuple[str, float, str]]]:
    """Abbreviation and prefix rules, using nothing but the project's own terms.

    Both directions matter and they are not symmetric:

        buf   -> buffer     the project abbreviates; a query spells it out
        cache -> caching    the project inflects; a query gives the stem

    The keys are what a *query* might say, so the long form is the key and the
    project's spelling is the target.
    """
    found: dict[str, list[tuple[str, float, str]]] = {}
    ordered = sorted(project_terms)
    by_skeleton: dict[str, list[str]] = {}
    for term in ordered:
        by_skeleton.setdefault(_skeleton(term), []).append(term)

    for short in ordered:
        for long, reason in _longer_forms(short, ordered, by_skeleton):
            score = SUBSEQ_SCORE if reason == "subseq" else LEXICAL_SCORE
            found.setdefault(long, []).append((short, score, reason))
    for key, entries in found.items():
        entries.sort(key=lambda item: (-item[1], item[0]))
        found[key] = entries[: config.max_targets]
    return found


def _longer_forms(
    short: str, ordered: Sequence[str], by_skeleton: Mapping[str, Sequence[str]]
) -> Iterable[tuple[str, str]]:
    """Project terms that ``short`` plausibly abbreviates, with the rule used.

    Only project terms are considered, not all of English. A mapping to a word
    the project never uses expands to a term the index cannot contain, which
    costs a lookup and returns nothing.

    Prefixes are found by bisecting the sorted vocabulary; skeletons by a
    precomputed index, since `msg` and `message` share no prefix beyond `m`.
    """
    if len(short) < 3:
        return ()  # two letters abbreviate far too much to be evidence
    found: list[tuple[str, str]] = []
    start = bisect.bisect_left(ordered, short)
    for candidate in ordered[start:]:
        if not candidate.startswith(short):
            break
        if len(candidate) > len(short):
            found.append((candidate, "prefix"))
    seen = {c for c, _ in found}
    for candidate in by_skeleton.get(_skeleton(short), ()):
        if candidate not in seen and abbreviates(short, candidate):
            found.append((candidate, "abbrev"))
            seen.add(candidate)
    for candidate in _subsequence_matches(short, ordered, seen):
        found.append((candidate, "subseq"))
    return found


def _subsequence_matches(short: str, ordered: Sequence[str], seen: set[str]) -> Iterable[str]:
    """Terms that ``short`` spells out in order, as `ctx` does in `context`.

    The loosest of the three rules and the only one that catches vowel-dropping
    that is not systematic -- `ctx` loses the `n`, so no skeleton reaches it.

    Two guards keep it from firing on coincidence: the long form may be at most
    `_SUBSEQ_RATIO` times the short one (without which `cat` matches
    `concatenate`), and the first letters must agree. It still scores below the
    other rules, because it is the one most likely to be wrong.
    """
    if len(short) > _SUBSEQ_MAX_SHORT:
        return
    ceiling = len(short) * _SUBSEQ_RATIO
    start = bisect.bisect_left(ordered, short[0])
    for candidate in ordered[start:]:
        if not candidate.startswith(short[0]):
            break
        if candidate in seen or len(candidate) <= len(short) or len(candidate) > ceiling:
            continue
        if _is_subsequence(short, candidate):
            yield candidate


def _is_subsequence(short: str, long: str) -> bool:
    letters = iter(long)
    return all(c in letters for c in short)


def _skeleton(word: str) -> str:
    """The consonant skeleton: `message` -> `msg`, `buffer` -> `bfr`.

    Keeps the first letter whatever it is (a leading vowel carries the word,
    as in `alloc`), drops the rest of the vowels, and collapses immediate
    repeats. That last step is what makes it work: without it `message`
    reduces to `mssg` and never meets `msg`, which is the single most common
    abbreviation in any Java codebase.
    """
    consonants = [c for c in word[1:] if c not in _VOWELS]
    collapsed = [c for i, c in enumerate(consonants) if i == 0 or c != consonants[i - 1]]
    return word[:1] + "".join(collapsed)


class VectorSpace:
    """A pretrained fastText space, optionally adapted to one repository.

    Held as a class rather than a function because loading costs minutes and
    several gigabytes: one instance can ground many projects.
    """

    def __init__(self, model: object, vectors: object) -> None:
        self._model = model
        self._vectors = vectors

    @property
    def vectors(self):  # type: ignore[no-untyped-def]
        return self._vectors

    def save_full(self, path: Path | str) -> None:
        if self._model is None:
            raise RuntimeError("no full model is loaded")
        self._model.save(str(path))  # type: ignore[attr-defined]

    @classmethod
    def load(cls, path: Path | str | None, *, config: GroundingConfig) -> VectorSpace:
        """Load the pretrained model.

        Fine-tuning needs the full model (input *and* output matrices, hence
        the memory); the other strategies need only the vectors, which is
        substantially lighter.
        """
        if path is None:
            raise ValueError("no model_path given")
        source = Path(path)
        if not source.is_file():
            raise FileNotFoundError(f"no fastText model at {source}")
        _log.info("loading %s (this takes minutes)", source)
        if config.strategy == "finetune":
            from gensim.models.fasttext import load_facebook_model

            model = load_facebook_model(str(source))
            return cls(model, model.wv)
        from gensim.models.fasttext import load_facebook_vectors

        return cls(None, load_facebook_vectors(str(source)))

    def finetune(self, sentences: Sequence[Sequence[str]], *, config: GroundingConfig) -> None:
        """Continue training on the repository's own corpus.

        Two things happen. Words the pretrained model never saw -- `netlink`,
        `writability`, most of any project's coinages -- get real vectors
        instead of a subword guess. And words it saw in a different sense get
        pulled toward this project's usage.

        Words already well aligned are frozen through ``vectors_vocab_lockf``
        so training cannot degrade what already works. **The freeze is
        partial**: fastText composes a word from its subwords, and the subword
        matrix stays trainable, so a frozen word still drifts a little through
        its n-grams. Measured, only about 22% of a frozen word's
        representation is genuinely held fixed. It is a brake, not a lock.
        """
        if self._model is None:
            raise RuntimeError("finetune needs the full model; load with strategy='finetune'")
        if not sentences:
            _log.warning("no corpus to fine-tune on; leaving the pretrained space unchanged")
            return
        import numpy as np

        model = self._model
        before = len(model.wv)
        model.build_vocab(sentences, update=True)
        added = len(model.wv) - before
        lockf = np.ones(len(model.wv), dtype=np.float32)
        for word in model.wv.index_to_key[:before]:
            lockf[model.wv.get_index(word)] = 0.0  # pretrained: brake it
        model.wv.vectors_vocab_lockf = lockf
        model.workers = config.workers
        _log.info(
            "fine-tuning on %d sentences; %d words new to the model, %d braked",
            len(sentences),
            added,
            before,
        )
        model.train(
            sentences,
            total_examples=len(sentences),
            epochs=config.epochs,
        )
        self._vectors = model.wv

    def ground(
        self, project_terms: Mapping[str, int], *, config: GroundingConfig
    ) -> dict[str, list[tuple[str, float, str]]]:
        """General word -> the project spellings nearest it in vector space.

        The general side is a **band**, not a prefix, of the pretrained
        vocabulary: the head is function words that no code query is built
        from, and the tail is words whose vectors are too poorly trained to
        trust. Any general word already in the project's vocabulary is skipped
        too -- the index matches those directly, so an entry would be a wasted
        lookup.

        Similarity is computed as one matrix product rather than per-word
        lookups: tens of thousands of general words against tens of thousands
        of project terms is minutes as linear algebra and hours as a loop.
        """
        import numpy as np

        vectors = self._vectors
        targets = [t for t in sorted(project_terms) if _has(vectors, t)]
        if not targets:
            _log.warning("no project term has a vector; skipping vector grounding")
            return {}
        keys = [
            w
            for w in vectors.index_to_key[config.skip_head : config.general_vocab]
            if w.isalpha() and w.islower() and len(w) >= 3 and w not in project_terms
        ]
        if not keys:
            return {}

        target_matrix = _unit_rows(np.array([vectors[t] for t in targets], dtype=np.float32))
        found: dict[str, list[tuple[str, float, str]]] = {}
        for start in range(0, len(keys), 2048):
            chunk = keys[start : start + 2048]
            key_matrix = _unit_rows(np.array([vectors[k] for k in chunk], dtype=np.float32))
            similarity = key_matrix @ target_matrix.T
            for row, key in enumerate(chunk):
                scores = similarity[row]
                best = np.argpartition(-scores, min(config.max_targets, len(targets) - 1))[
                    : config.max_targets
                ]
                entries = [
                    (targets[i], score, "vector")
                    for i in best
                    if scores[i] >= config.min_cosine
                    and (score := _vector_score(float(scores[i]))) >= MIN_ENTRY_SCORE
                ]
                if entries:
                    entries.sort(key=lambda item: (-item[1], item[0]))
                    found[key] = entries
        _log.info("vector grounding produced %d keys from %d general words", len(found), len(keys))
        return found


def _has(vectors: object, word: str) -> bool:
    """Whether a vector can be produced at all.

    For fastText this is nearly always true -- an unknown word is composed
    from its character n-grams -- which is exactly why `netlink` gets a
    plausible vector without ever appearing in the training corpus. The guard
    is only for a word so short it has no n-grams.
    """
    try:
        return vectors[word] is not None  # type: ignore[index]
    except (KeyError, IndexError, ValueError):
        return False


def _unit_rows(matrix):  # type: ignore[no-untyped-def]
    """L2-normalise rows so a dot product is a cosine."""
    import numpy as np

    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    return matrix / np.maximum(norms, 1e-9)


def _vector_score(cosine: float) -> float:
    """Map a cosine onto an expansion score.

    Rescaled from ``[MIN_COSINE, 1]`` rather than used raw: a raw 0.6 cosine
    would enter the table at a weight it does not deserve beside a lexical
    rule at 0.75.

    The rescale is linear. An earlier version squared it, which sounded
    conservative and was in fact useless -- it pushed every neighbour below
    cosine 0.8 down to a score under 0.01, small enough that multiplying it
    into a ranking changed nothing at all.
    """
    span = max(1.0 - MIN_COSINE, 1e-6)
    normalised = max(0.0, min(1.0, (cosine - MIN_COSINE) / span))
    return round(VECTOR_CEILING * normalised, 4)


def _merge(
    lexical: dict[str, list[tuple[str, float, str]]],
    vectors: dict[str, list[tuple[str, float, str]]],
    cap: int,
) -> dict[str, list[tuple[str, float, str]]]:
    """Lexical rules win ties; both survive when they disagree.

    A word reached by both a rule and a neighbour keeps both entries only if
    they point at different spellings. Where they agree, the rule's higher
    score stands -- taking the max rather than summing, because two derivations
    of the same mapping are not twice the evidence.
    """
    found = {key: list(entries) for key, entries in lexical.items()}
    for key, entries in vectors.items():
        merged = {target: (score, reason) for target, score, reason in found.get(key, ())}
        for target, score, reason in entries:
            existing = merged.get(target)
            if existing is None or score > existing[0]:
                merged[target] = (score, reason if existing is None else existing[1])
        ranked = sorted(
            ((t, s, r) for t, (s, r) in merged.items()), key=lambda item: (-item[1], item[0])
        )
        found[key] = ranked[:cap]
    return found


def abbreviates(short: str, long: str) -> bool:
    """Whether ``short`` is a plausible abbreviation of ``long``.

    Kept public because it is the one rule worth testing directly: prefix, or
    the same word with its vowels dropped.
    """
    if len(short) < 3 or len(short) >= len(long):
        return False
    if long.startswith(short):
        return True
    return _skeleton(long) == _skeleton(short)


def coverage(project_terms: Mapping[str, int], table: Mapping[str, Sequence[object]]) -> float:
    """Share of the project's terms that some general word reaches.

    Reported by the CLI so a build says how much grounding it actually got,
    rather than leaving the number invisible.
    """
    if not project_terms:
        return 0.0
    reached = {
        entry[0]  # type: ignore[index]
        for entries in table.values()
        for entry in entries
    }
    return len(reached & set(project_terms)) / len(project_terms)
