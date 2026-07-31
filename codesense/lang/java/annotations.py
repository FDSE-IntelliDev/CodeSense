"""Extracting annotations from Java source.

Annotations are the highest signal-to-noise signal in a Java project --
`@RestController` states outright that this is an HTTP entry point, more
precisely than any keyword can -- and extracting them is nearly free, since
the parser is already running tree-sitter and the nodes are right there in
the AST.

**One annotation is three things** (``docs/design/09-grounding.md``,
section 8):

    the name       -> a posting in the `annotation` field
    the attachment -> an `annotated_by` edge
    the arguments  -> postings in the `annotation_arg` field

Indexing only the name loses the other two. The permission string in
`@PreAuthorize("@ss.hasPerm('sys:user:query')")` and the natural-language
text in `@Schema(description=...)` both live in the arguments.

This module holds only data types and pure functions -- the AST walk is in
`codesense.lang.java.scanner`, because annotations and modifiers hang off the
same node and one traversal collects both.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence

from codesense.lang.base import AnnotationUse

__all__ = ["AnnotationUse", "arg_tokens", "posting_terms"]

#: Separators for cutting searchable fragments out of arguments: quotes,
#: brackets, dots, colons, commas, equals signs and so on. Alphanumerics and
#: underscores are kept; everything else is a separator.
_ARG_SPLIT = re.compile(r"[^A-Za-z0-9_]+")


def arg_tokens(args: str) -> tuple[str, ...]:
    """Cut annotation arguments into searchable fragments.

    `"@ss.hasPerm('sys:user:query')"` becomes ``ss hasPerm sys user query``.
    Deduplicated in order, because the order itself carries information
    (`sys:user:query` is a hierarchy).
    """
    seen: dict[str, None] = {}
    for token in _ARG_SPLIT.split(args):
        if token and not token.isdigit():
            seen.setdefault(token.lower(), None)
    return tuple(seen)


def posting_terms(
    use: AnnotationUse, split: Callable[[str], Sequence[str]]
) -> list[tuple[str, str]]:
    """The (term, field) pairs one annotation use produces.

    Three kinds of term, covering two of the "an annotation is three things"
    from the design doc (the third is the `annotated_by` edge, which edge
    construction handles):

        @Cacheable      whole name  -> annotation      exact target for meta expansion
        cache / able    split units -> annotation      lets @AppCache match a cache unit
        user / query    arg pieces  -> annotation_arg  permissions, URLs, descriptions

    ``split`` is injected rather than imported here -- the splitter is a
    third-party dependency (srctoolkit/Ronin) while this function is pure and
    testable.
    """
    terms: list[tuple[str, str]] = [(use.at_name, "annotation")]
    terms.extend((unit, "annotation") for unit in split(use.name))
    terms.extend((token, "annotation_arg") for token in arg_tokens(use.args))
    seen: dict[tuple[str, str], None] = {}
    for item in terms:
        seen.setdefault(item, None)
    return list(seen)
