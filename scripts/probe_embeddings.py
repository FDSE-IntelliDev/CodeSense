"""Probing how usable a pretrained word-vector model is on a project's
vocabulary.

Several of chapter 09's conclusions were measured with this script. Swap the
model, or finish fine-tuning, and rerunning it says whether they still hold.

    python scripts/probe_embeddings.py coverage   --model M --index D
    python scripts/probe_embeddings.py senses     --model M
    python scripts/probe_embeddings.py abbrev     --model M
    python scripts/probe_embeddings.py mismatch   --model M --index D
    python scripts/probe_embeddings.py gates      --model M --index D

The subcommands answer one question each:

    coverage   how much of the project vocabulary is in the model, and how
               well ranked
    senses     are a systems word's neighbours software senses or natural
               language ones
    abbrev     is an abbreviation-to-expansion correspondence real semantics
               or just spelling (**with an orthographic control**)
    mismatch   which words' generic vectors disagree with their actual use in
               the project (fine-tuning priority)
    gates      which words each of the two fine-tuning gates covers, and
               whether they really conflict

Requires ``gensim`` and a fastText ``.bin`` (``cc.en.300.bin``, say).
This is a **research script**: read, compute, print; it is not on the query
path.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import numpy as np

#: abbreviation -> (the correct expansion, a same-prefix but semantically
#: unrelated distractor). The distractor is the point: fastText has subwords,
#: `dept` and `department` share n-grams, and the cosine is lifted by the
#: orthographic overlap alone -- without a control there is no telling
#: semantics from spelling.
ABBREVIATIONS: dict[str, tuple[str, tuple[str, ...]]] = {
    "dept": ("department", ("depth", "deposit", "depict", "depot")),
    "cfg": ("configuration", ("cog", "cliff", "coffee")),
    "config": ("configuration", ("confidence", "confetti", "conflict")),
    "mgr": ("manager", ("merge", "mugger", "meager")),
    "buf": ("buffer", ("buffalo", "buffet", "bufo")),
    "impl": ("implementation", ("imply", "impala", "impale")),
    "msg": ("message", ("mosaic", "musing", "massage")),
    "auth": ("authentication", ("author", "authority", "autism")),
    "pwd": ("password", ("powder", "pawed", "pwn")),
    "dict": ("dictionary", ("dictate", "diction", "dictator")),
    "addr": ("address", ("adder", "adorn", "addax")),
    "perms": ("permission", ("perm", "perky", "persimmon")),
}

#: Systems vocabulary. Their dominant natural-language sense differs sharply
#: from their sense in code, which is where vectors trained on general text
#: are most likely to be wrong.
SYSTEMS_TERMS = (
    "pool",
    "thread",
    "stream",
    "flush",
    "swap",
    "sector",
    "block",
    "buffer",
    "cache",
    "async",
    "disk",
    "queue",
)

#: The line between "already aligned" and "mismatched". Words below it should
#: be unfrozen during fine-tuning, words above it frozen.
MISMATCH_FLOOR = 0.15


def load_vectors(model_path: Path) -> Any:
    from gensim.models.fasttext import load_facebook_vectors

    return load_facebook_vectors(str(model_path))


def load_corpus(index_dir: Path) -> list[list[str]]:
    raw = json.loads((index_dir / "enhanced_call_chain_corpus.json").read_text(encoding="utf-8"))
    return [entry if isinstance(entry, list) else [entry] for entry in raw]


def split_identifier(name: str) -> list[str]:
    text = re.sub(r"[^A-Za-z]+", " ", name)
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", text)
    return [token.lower() for token in text.split() if token]


class Space:
    """A few common measurements over one word-vector space."""

    def __init__(self, vectors: Any) -> None:
        self._kv = vectors

    def __contains__(self, word: object) -> bool:
        return word in self._kv.key_to_index

    def rank(self, word: str) -> int:
        return self._kv.key_to_index.get(word, -1)

    def unit(self, word: str) -> np.ndarray:
        vector = self._kv[word]
        return vector / (float(np.linalg.norm(vector)) + 1e-9)

    def cosine(self, left: str, right: str) -> float:
        return float(self.unit(left) @ self.unit(right))

    def neighbours(self, word: str, count: int = 8) -> list[str]:
        return [name for name, _ in self._kv.most_similar(word, topn=count)]

    def size(self) -> int:
        return len(self._kv.key_to_index)


def context_profile(corpus: list[list[str]]) -> tuple[Counter[str], dict[str, Counter[str]]]:
    """Word frequencies and co-occurrence."""
    freq: Counter[str] = Counter(term for entry in corpus for term in entry)
    co: dict[str, Counter[str]] = defaultdict(Counter)
    for entry in corpus:
        unique = set(entry)
        for term in unique:
            co[term].update(unique - {term})
    return freq, co


def inverse_chain_frequency(corpus: list[list[str]]) -> dict[str, float]:
    total = len(corpus)
    document_frequency: Counter[str] = Counter()
    for entry in corpus:
        document_frequency.update(set(entry))
    return {term: math.log(total / df) for term, df in document_frequency.items()}


def mismatch_score(
    space: Space, term: str, partners: list[tuple[str, int]], icf: dict[str, float]
) -> float | None:
    """How well a generic vector's position agrees with actual use in the
    project.

    Weighted by ICF, or `get` -- 120,000 occurrences -- would dominate every
    context vector.
    """
    usable = [(word, count) for word, count in partners if word in space]
    if len(usable) < 3:
        return None
    weights = [count * icf.get(word, 0.0) for word, count in usable]
    if sum(weights) <= 0:
        return None
    similarities = [space.cosine(term, word) for word, _ in usable]
    return float(np.average(similarities, weights=weights))


def cmd_coverage(space: Space, corpus: list[list[str]], index_dir: Path) -> None:
    freq, _ = context_profile(corpus)
    symbols = json.loads((index_dir / "symbols_index.json").read_text(encoding="utf-8"))
    units: Counter[str] = Counter()
    for record in symbols:
        units.update(split_identifier(record.get("name") or ""))

    print(f"model vocabulary {space.size():,}\n")
    for label, vocabulary in (("call-chain corpus", freq), ("symbol split units", units)):
        missing = sorted((t for t in vocabulary if t not in space), key=lambda t: -vocabulary[t])
        inside = len(vocabulary) - len(missing)
        pct = 100 * inside / len(vocabulary)
        print(f"{label}: {len(vocabulary)} words, {inside} in the model ({pct:.0f}%)")
        print(f"  OOV: {missing[:8]}")

    print("\n'in the vocabulary' is not 'the vector is usable' -- by rank band:")
    buckets = (
        (0, 50_000, "top 50k    well trained"),
        (50_000, 200_000, "50k-200k   adequate"),
        (200_000, 800_000, "200k-800k  weak"),
        (800_000, 10**9, ">800k      essentially noise"),
    )
    for low, high, label in buckets:
        hit = [t for t in freq if low <= space.rank(t) < high]
        print(f"  {label:<24}{len(hit):>4}")
        if low >= 200_000 and hit:
            print(f"      {sorted(hit, key=lambda t: -freq[t])[:10]}")


def cmd_senses(space: Space) -> None:
    print("neighbours of systems vocabulary -- software sense or natural language?\n")
    for term in SYSTEMS_TERMS:
        if term in space:
            print(f"  {term:<10}{', '.join(space.neighbours(term))}")


def cmd_abbrev(space: Space) -> None:
    print("abbreviation vs expansion: real correspondence, or just spelling?")
    print(f"  {'abbrev':<10}{'expansion':<16}{'cos':>7}   {'distractor':>14}{'cos':>7}   verdict")
    print("  " + "-" * 66)
    wins = tested = 0
    for short, (full, distractors) in ABBREVIATIONS.items():
        if short not in space or full not in space:
            print(f"  {short:<10}{full:<16}  -- not in the vocabulary")
            continue
        available = [(d, space.cosine(short, d)) for d in distractors if d in space]
        if not available:
            continue
        worst, worst_score = max(available, key=lambda pair: pair[1])
        true_score = space.cosine(short, full)
        tested += 1
        won = true_score > worst_score
        wins += won
        print(
            f"  {short:<8}{full:<16}{true_score:>7.3f}   {worst:>12}{worst_score:>7.3f}   "
            f"{'semantics wins' if won else 'spelling wins'}"
        )
    print(f"\n  the expansion beat the same-prefix distractor in {wins}/{tested} pairs")
    print(f"  {_baseline(space)}")


def _baseline(space: Space) -> str:
    generator = np.random.default_rng(0)
    words = [w for w in list(space._kv.key_to_index)[:20_000] if w.isalpha() and len(w) > 3]
    pairs = generator.choice(len(words), 4_000).reshape(2_000, 2)
    scores = [space.cosine(words[i], words[j]) for i, j in pairs if words[i] != words[j]]
    return (
        f"random word-pair baseline: mean {np.mean(scores):.3f}  "
        f"95th pct {np.percentile(scores, 95):.3f}"
    )


def cmd_mismatch(space: Space, corpus: list[list[str]]) -> None:
    freq, co = context_profile(corpus)
    icf = inverse_chain_frequency(corpus)
    rows = []
    for term, partners in co.items():
        if freq[term] < 50 or term not in space:
            continue
        score = mismatch_score(space, term, partners.most_common(25), icf)
        if score is not None:
            rows.append((score, term, freq[term], [w for w, _ in partners.most_common(5)]))
    rows.sort()

    print("the 20 words most worth fine-tuning (generic vector least like project use)")
    print(f"  {'word':<14}{'project freq':>13}{'agreement':>11}   co-occurring in project")
    for score, term, count, context in rows[:20]:
        print(f"  {term:<14}{count:>9}{score:>9.3f}   {', '.join(context)}")

    bad = [row for row in rows if row[0] < MISMATCH_FLOOR]
    mass = sum(row[2] for row in bad) / sum(freq.values())
    share = f"{100 * mass:.1f}%"
    print(f"\n  {len(bad)}/{len(rows)} words score below {MISMATCH_FLOOR}, {share} of all tokens")
    print("  note: this metric conflates genuine mismatch with 'every co-occurring")
    print("  word is an uninformative verb'. Trust the head of the list; read the")
    print("  proportion as an upper bound.")


def cmd_gates(space: Space, corpus: list[list[str]]) -> None:
    """Which words each fine-tuning gate covers, and whether they really
    conflict."""
    freq, co = context_profile(corpus)
    icf = inverse_chain_frequency(corpus)
    print("words under gate 1 (abbreviation mappings must not regress), with scores:")
    print(
        f"  {'abbrev':<10}{'expansion':<16}{'cos':>7}{'freq':>8}{'agreement':>11}"
        "   what fine-tuning does"
    )
    print("  " + "-" * 74)
    for short, (full, _) in ABBREVIATIONS.items():
        if short not in space or full not in space:
            continue
        score = (
            mismatch_score(space, short, co[short].most_common(25), icf) if short in co else None
        )
        if score is None:
            note = "absent or too rare -- no gradient, safe by construction"
        elif score >= MISMATCH_FLOOR:
            note = "aligned -- frozen, so gate 1 holds automatically"
        else:
            note = "mismatched -- unfrozen; fine-tuning should *improve* cos"
        shown = f"{score:.3f}" if score is not None else "—"
        head = f"  {short:<8}{full:<16}{space.cosine(short, full):>7.3f}"
        print(f"{head}{freq.get(short, 0):>9}{shown:>9}   {note}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("coverage", "senses", "abbrev", "mismatch", "gates"))
    parser.add_argument("--model", type=Path, required=True, help="fastText .bin")
    parser.add_argument(
        "--index", type=Path, help="index artifact directory; needed by coverage/mismatch/gates"
    )
    args = parser.parse_args(argv)

    if args.command in ("coverage", "mismatch", "gates") and args.index is None:
        parser.error(f"{args.command} needs --index")

    print(f"loading {args.model} ...", flush=True)
    space = Space(load_vectors(args.model))

    if args.command == "senses":
        cmd_senses(space)
        return 0

    corpus = load_corpus(args.index)
    if args.command == "coverage":
        cmd_coverage(space, corpus, args.index)
    elif args.command == "abbrev":
        cmd_abbrev(space)
    elif args.command == "mismatch":
        cmd_mismatch(space, corpus)
    else:
        cmd_gates(space, corpus)
    return 0


if __name__ == "__main__":
    sys.exit(main())
