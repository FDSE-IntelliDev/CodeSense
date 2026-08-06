"""An index on disk, and how it becomes something queries can run against.

The layout is a directory rather than one file, because the artifacts have
genuinely different lifecycles:

    meta.json        what was indexed, when, and with what
    index.json       symbols + postings + edges -- rebuilt when source changes
    expansion.json   lexical grounding -- rebuilt when the embedding changes

Keeping them apart means re-running the embedding does not rewrite a 58MB
symbol table, and re-scanning the source does not discard grounding that took
minutes of vector work to produce.

`meta.json` is what makes a result reproducible. The same script over the same
query gives different answers on a different index, so the index has to be able
to say what it is.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from codesense.ql.context import EvalContext
from codesense.ql.fields import IndexField
from codesense.ql.frag import Edge, Element
from codesense.ql.store import (
    Expansion,
    InMemoryEdgeStore,
    InMemoryPostingIndex,
    InMemorySymbolStore,
    Posting,
)

__all__ = ["Index", "IndexMeta"]

#: Bumped when the on-disk shape changes incompatibly. Loading an index from a
#: different version fails loudly rather than half-working.
FORMAT_VERSION = 1

_INDEX_FILE = "index.json"
_EXPANSION_FILE = "expansion.json"
_META_FILE = "meta.json"


@dataclass(frozen=True, slots=True)
class IndexMeta:
    """What this index is. Written beside the data so results can be traced."""

    project: str
    root: str
    built_at: str
    symbols: int = 0
    postings: int = 0
    edges: int = 0
    grounded_terms: int = 0
    grounding_profile: str = "lexical"
    grounding_status: str = "ready"
    grounding_reason: str = ""
    #: Languages this index was built from. Recorded because framework
    #: relations enter the expansion table at query time and depend on it.
    language: str = "java"
    format_version: int = FORMAT_VERSION

    def describe(self) -> str:
        """One line, for the header of a generated script."""
        return (
            f"{self.project} - {self.symbols:,} symbols, {self.edges:,} edges, "
            f"{self.grounded_terms:,} grounded terms, built {self.built_at}"
        )


@dataclass
class Index:
    """A loaded index. Holds the raw payload; `to_context` makes it queryable."""

    meta: IndexMeta
    payload: dict[str, Any]
    expansion: dict[str, list[tuple[str, float, str]]] = field(default_factory=dict)

    @classmethod
    def load(cls, directory: Path | str) -> Index:
        """Read an index directory."""
        path = Path(directory)
        meta_file = path / _META_FILE
        if not meta_file.is_file():
            raise FileNotFoundError(
                f"{path} is not an index directory (no {_META_FILE}); "
                "build one with Project.build()"
            )
        raw = json.loads(meta_file.read_text(encoding="utf-8"))
        version = raw.get("format_version", 0)
        if version != FORMAT_VERSION:
            raise ValueError(
                f"index at {path} is format v{version}, this build reads v{FORMAT_VERSION}; "
                "rebuild it"
            )
        expansion_file = path / _EXPANSION_FILE
        expansion = _read_expansion(expansion_file) if expansion_file.is_file() else {}
        return cls(
            meta=IndexMeta(**raw),
            payload=json.loads((path / _INDEX_FILE).read_text(encoding="utf-8")),
            expansion=expansion,
        )

    def save(self, directory: Path | str) -> Path:
        """Write the index directory, creating it if needed."""
        path = Path(directory)
        path.mkdir(parents=True, exist_ok=True)
        (path / _INDEX_FILE).write_text(
            json.dumps(self.payload, ensure_ascii=False), encoding="utf-8"
        )
        if self.expansion:
            (path / _EXPANSION_FILE).write_text(
                json.dumps(self.expansion, ensure_ascii=False, indent=1), encoding="utf-8"
            )
        # Written last: its presence is what marks the directory complete, so a
        # crash mid-write leaves something that fails to load rather than
        # something that loads and lies.
        (path / _META_FILE).write_text(
            json.dumps(asdict(self.meta), ensure_ascii=False, indent=1), encoding="utf-8"
        )
        return path

    def elements(self) -> list[Element]:
        return [
            Element(
                symbol_id=s["symbol_id"],
                name=s["name"],
                kind=s["kind"],
                file=s["file"],
                span=tuple(s["span"]),
                signature=s.get("signature", ""),
                container=s.get("container", ""),
                doc=s.get("doc", ""),
                language=s.get("language", "java"),
                modifiers=frozenset(s.get("modifiers", ())),
            )
            for s in self.payload["symbols"]
        ]

    def to_context(self, **overrides: Any) -> EvalContext:
        """Build the context operators run against.

        ``overrides`` lets a caller inject a judge or swap a store without this
        module knowing anything about LLMs -- the QL layer stays free of them.
        """
        from codesense.indexing.expansion import build_expansion_table

        elements = self.elements()
        language = _language_of(self.meta.language)
        postings = {
            term: [Posting(p["symbol_id"], IndexField(p["field"]), p["tf"]) for p in entries]
            for term, entries in self.payload["postings"].items()
        }
        base: dict[str, Any] = {
            "symbols": InMemorySymbolStore(elements),
            "postings": InMemoryPostingIndex(postings, total_symbols=len(elements)),
            "expansion": build_expansion_table(lexical=self.expansion or None, language=language),
            "edges": InMemoryEdgeStore(
                Edge(
                    source_id=e["source_id"],
                    target_id=e["target_id"],
                    kind=e["kind"],
                    confidence=e["confidence"],
                    provenance=e["provenance"],
                )
                for e in self.payload.get("edges", ())
            ),
        }
        base.update(overrides)
        return EvalContext(**base)

    def vocabulary(self, limit: int = 0) -> list[tuple[str, int]]:
        """The project's terms with their document frequency, commonest first.

        **``df`` is the point.** Without it a model has no way to tell that
        `buffer` matches 2365 symbols while `watermark` matches 11, and no
        basis for ordering anything.

        ``limit <= 0`` returns everything. Narrowing is deliberately by raw
        frequency and not by an ICF band: a fixed band excluded `buf`,
        `allocator` and `chunk` on netty precisely *because* they are common
        there -- and the query was about buffer allocation.
        """
        counted = [
            (term, len({p["symbol_id"] for p in entries}))
            for term, entries in self.payload["postings"].items()
        ]
        counted.sort(key=lambda item: (-item[1], item[0]))
        return counted[:limit] if limit > 0 else counted

    def names(self) -> dict[str, list[int]]:
        """Symbol name to ids. Several symbols may share a name."""
        found: dict[str, list[int]] = {}
        for s in self.payload["symbols"]:
            found.setdefault(s["name"], []).append(s["symbol_id"])
        return found

    def __len__(self) -> int:
        return len(self.payload.get("symbols", ()))


def _language_of(name: str):  # type: ignore[no-untyped-def]
    """The adapter for this index, or None.

    None rather than an error: an index built elsewhere may name a language
    this installation has no adapter for, and it should still load and answer
    lexical queries. Only the framework expansions are lost.
    """
    from codesense.lang import LANGUAGES

    try:
        return LANGUAGES.get(name)
    except KeyError:
        return None


def _read_expansion(path: Path) -> dict[str, list[tuple[str, float, str]]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {
        key: [(str(t), float(s), str(r)) for t, s, r in entries] for key, entries in raw.items()
    }


def expansion_entries(table: dict[str, list[tuple[str, float, str]]]) -> list[Expansion]:
    """Flatten a grounding table for inspection. Used by the CLI, not by query
    execution."""
    return [Expansion(t, s, r) for entries in table.values() for t, s, r in entries]
