"""Turning declarations into sentences for training word vectors.

Language-neutral on purpose: it reads `Declaration` attributes and nothing
else, so a Go or Python adapter gets a corpus for free.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence

from codesense.text.split import split_identifier, words_of

__all__ = ["corpus_sentences", "source_files"]

#: Most doc words kept in a sentence. A long javadoc would otherwise swamp the
#: identifier words it sits beside, which are the ones being learned.
DOC_WORDS = 60


def corpus_sentences(declarations: Iterable[object]) -> Iterator[list[str]]:
    """One sentence per declaration.

    **A sentence is one declaration, not one identifier.** That is the whole
    point: word vectors learn from co-occurrence, and what teaches a model
    that netty's ``buf`` means a buffer is seeing it beside ``alloc``,
    ``capacity`` and ``readable`` in the same declaration. Splitting each
    identifier into its own sentence would destroy exactly the signal being
    trained on.

    Order follows the declaration's own structure -- name, container,
    signature, doc -- so a context window sees a name beside the type it
    belongs to.
    """
    for declaration in declarations:
        sentence = _sentence(declaration)
        if len(sentence) >= 2:  # a one-word sentence teaches nothing
            yield sentence


def source_files(root, language) -> Iterator:  # type: ignore[no-untyped-def]
    """The files one language claims under a root, in a stable order.

    Sorted so symbol ids are reproducible across runs -- an index whose ids
    shuffle between builds cannot be diffed or cached against.
    """
    found = []
    for glob in language.file_globs:
        found.extend(root.rglob(glob))
    for path in sorted(found):
        text = str(path).replace("\\", "/")
        if not any(part in text for part in language.skip_parts):
            yield path


def _sentence(declaration: object) -> list[str]:
    words: list[str] = []
    for attribute in ("name", "container", "signature"):
        value = getattr(declaration, attribute, "")
        if isinstance(value, str) and value:
            words += split_identifier(value)
    doc = getattr(declaration, "doc", "")
    if isinstance(doc, str) and doc:
        words += words_of(doc, limit=DOC_WORDS)
    for use in getattr(declaration, "annotations", ()) or ():
        name = getattr(use, "name", "")
        if isinstance(name, str) and name:
            words += split_identifier(name)
    return _dedupe(words)


def _dedupe(words: Sequence[str]) -> list[str]:
    """Each word once, in first-seen order.

    A getter contributes ``get user get user`` across its name and signature,
    and that repetition is an artifact of the language's shape rather than
    evidence the words belong together. Collapsing only *adjacent* repeats
    would miss it -- the echo comes back interleaved, not doubled.
    """
    seen: dict[str, None] = {}
    for word in words:
        seen.setdefault(word, None)
    return list(seen)
