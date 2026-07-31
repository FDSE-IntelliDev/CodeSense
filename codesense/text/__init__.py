"""Language-neutral lexical work: splitting identifiers, building a corpus.

Kept apart from `codesense.lang` because none of it depends on a language, and
apart from `codesense.indexing` because both indexing and grounding need it.
Putting `split_identifier` inside the Java adapter would have meant a Go
adapter reimplementing camel-case.
"""

from codesense.text.corpus import corpus_sentences, source_files
from codesense.text.split import Splitter, split_identifier, words_of

__all__ = [
    "Splitter",
    "corpus_sentences",
    "source_files",
    "split_identifier",
    "words_of",
]
