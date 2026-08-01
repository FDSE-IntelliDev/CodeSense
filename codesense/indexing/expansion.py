"""Assembling index artifacts into an expansion table QL understands.

The bridge between indexing and ql. The dependency is one-way: indexing
knows ql's data types, ql does not know indexing -- which is why this
function lives here rather than there.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from codesense.ql.store import Expansion, InMemoryExpansionTable

__all__ = ["build_expansion_table"]


def build_expansion_table(
    *,
    lexical: Mapping[str, Sequence[tuple[str, float, str]]] | None = None,
    language: object | None = None,
) -> InMemoryExpansionTable:
    """Build the expansion table.

    ``lexical`` is what the lexical/vector chain produced (canonical word to
    the project's actual spelling); ``language`` contributes whatever
    relations its frameworks declare as fact.

    Their key spaces do not overlap -- meta-annotation keys all carry ``@``
    -- so merging needs no conflict handling. Should a key collide anyway,
    ``lexical`` goes first and both entries are kept: meta-annotations are
    facts and lexical entries are estimates, so let scoring separate them
    rather than discarding either here.
    """
    table: dict[str, list[Expansion]] = {}
    if language is not None:
        for key, entries in language.expansions().items():
            table[key] = [Expansion(target, score, reason) for target, score, reason in entries]
    for key, entries in (lexical or {}).items():
        table.setdefault(key, []).extend(
            Expansion(target, score, reason) for target, score, reason in entries
        )
    return InMemoryExpansionTable(table)
