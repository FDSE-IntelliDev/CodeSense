"""Scanning a repository into an index.

Orchestration only -- the work is in `postings`, `graph`, and the language
adapter. What this module owns is the **order**, and one ordering decision is
load-bearing:

    scan  ->  lexicon  ->  segment  ->  edges

Compound segmentation cannot happen during the scan, because the lexicon it
needs is built from the whole repository's vocabulary and does not exist until
the scan finishes. Running it afterwards over the postings costs no second
parse: the postings already record which symbols carry `iostat`, so its pieces
inherit exactly those.

One parse also produces the training corpus. Scanning netty takes twenty
seconds, and making the grounding stage re-read every file would double that
for nothing.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from codesense.indexing.graph import GraphBuilder
from codesense.indexing.postings import PostingTable, declaration_terms
from codesense.lang.base import Declaration, Language
from codesense.text.corpus import corpus_sentences, source_files
from codesense.text.split import Splitter, split_identifier

__all__ = ["BuildResult", "Stats", "build_index"]

_log = logging.getLogger(__name__)

#: Most doc characters kept on the symbol itself. This is what `intent` shows
#: the model, and whole javadocs would put token cost out of control.
DOC_KEEP_CHARS = 400

#: Kinds whose bodies hold calls. Named generically so an adapter using
#: `function` rather than `method` needs no change here.
_CALLABLE_KINDS = frozenset({"method", "constructor", "function"})


@dataclass
class Stats:
    """What the build saw. Printed by the CLI and asserted on by tests."""

    files: int = 0
    failed: int = 0
    symbols: int = 0
    annotations: int = 0
    postings: int = 0
    edges: int = 0
    contains: int = 0
    calls: int = 0
    typed: int = 0
    ambiguous: int = 0
    unresolved: int = 0
    segmented: int = 0
    languages: tuple[str, ...] = ()


@dataclass
class BuildResult:
    """The index payload plus the corpus that fell out of the same parse."""

    payload: dict[str, Any]
    sentences: list[list[str]] = field(default_factory=list)
    stats: Stats = field(default_factory=Stats)


def build_index(
    root: Path,
    *,
    languages: Sequence[Language] | None = None,
    segment: bool = True,
    progress: Callable[[int, int], None] | None = None,
) -> BuildResult:
    """Scan a repository into an index payload and a training corpus.

    ``languages`` defaults to every registered adapter, so a mixed repository
    indexes in one pass. ``segment`` turns off compound splitting, which is
    what an A/B against an index built without it needs.
    """
    if languages is None:
        from codesense.lang import LANGUAGES

        languages = [LANGUAGES.get(name) for name in LANGUAGES.names()]
    if not languages:
        raise ValueError("no language adapters available; cannot index anything")

    stats = Stats(languages=tuple(language.name for language in languages))
    symbols: list[dict[str, Any]] = []
    sentences: list[list[str]] = []
    postings = PostingTable()
    graphs = {
        language.name: GraphBuilder(language.container_kinds, _CALLABLE_KINDS)
        for language in languages
    }

    for language in languages:
        _scan_language(
            root, language, symbols, sentences, postings, graphs[language.name], stats, progress
        )

    stats.symbols = len(symbols)
    if segment:
        stats.segmented = _segment_compounds(postings)

    edges: list[dict[str, Any]] = []
    for language in languages:
        builder = graphs[language.name]
        edges += builder.build()
        stats.contains += builder.stats.contains
        stats.calls += builder.stats.calls
        stats.typed += builder.stats.typed
        stats.ambiguous += builder.stats.ambiguous
        stats.unresolved += builder.stats.unresolved
    stats.edges = len(edges)

    flat = postings.flatten()
    stats.postings = postings.count()
    return BuildResult(
        payload={"symbols": symbols, "postings": flat, "edges": edges},
        sentences=sentences,
        stats=stats,
    )


def _scan_language(
    root: Path,
    language: Language,
    symbols: list[dict[str, Any]],
    sentences: list[list[str]],
    postings: PostingTable,
    graph: GraphBuilder,
    stats: Stats,
    progress: Callable[[int, int], None] | None,
) -> None:
    for path in source_files(root, language):
        stats.files += 1
        try:
            declarations = language.scan(path.read_text(encoding="utf-8", errors="replace"))
        except Exception as exc:  # noqa: BLE001 -- one bad file must not halt the build
            stats.failed += 1
            _log.warning("skipped %s: %s", path, exc)
            continue

        kept = [d for d in declarations if d.kind in language.indexed_kinds and d.name]
        sentences.extend(corpus_sentences(kept))
        for declaration in kept:
            symbol_id = len(symbols) + 1
            symbols.append(_symbol_row(symbol_id, declaration, path, root, language.name))
            stats.annotations += len(declaration.annotations)
            postings.add_all(declaration_terms(declaration, split_identifier, language), symbol_id)
            graph.observe(declaration, symbol_id)
        if progress is not None and stats.files % 200 == 0:
            progress(stats.files, len(symbols))


def _segment_compounds(postings: PostingTable) -> int:
    """Split compound terms the delimiter left whole.

    The lexicon comes from the postings themselves -- every word the delimiter
    already produced, weighted by how many symbols carry it. A repository that
    writes `io` and `stat` throughout thereby says `iostat` is two words; one
    that never writes `stat` alone leaves it whole, which is the right answer
    *for that repository*.
    """
    splitter = Splitter(postings.vocabulary())
    return postings.refine(splitter.segment)


def _symbol_row(
    symbol_id: int, declaration: Declaration, path: Path, root: Path, language: str
) -> dict[str, Any]:
    return {
        "symbol_id": symbol_id,
        "name": declaration.name,
        "kind": declaration.kind,
        "file": str(path.relative_to(root)),
        "span": [declaration.line, declaration.end_line],
        "signature": declaration.signature,
        "container": declaration.container,
        "doc": declaration.doc[:DOC_KEEP_CHARS],
        "language": language,
        "modifiers": sorted(declaration.modifiers),
    }
