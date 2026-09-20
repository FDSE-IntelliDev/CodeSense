"""The Java adapter.

Everything Java-specific lives under this package: the tree-sitter walk, the
shape of an annotation, and the framework relations Spring and JPA declare.
Nothing above `codesense.lang` imports any of it -- the rest of the system
sees only `Language` and `Declaration`.

This is also the worked example for adding a language. A new adapter needs a
scanner producing `Declaration`s and the four descriptive attributes below;
`expansions` may return nothing at all.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from codesense.lang.base import Declaration, RelationBatch, RelationContext, ScanResult
from codesense.lang.java.annotations import arg_tokens, posting_terms
from codesense.lang.java.frameworks import META_ANNOTATIONS, expansions_for, meta_expansion_table
from codesense.lang.java.relations import derive_java_relations
from codesense.lang.java.scanner import JAVA_MODIFIERS, JavaDeclarationScanner, modifier_terms

__all__ = [
    "JAVA_MODIFIERS",
    "META_ANNOTATIONS",
    "JavaDeclarationScanner",
    "JavaLanguage",
    "arg_tokens",
    "derive_java_relations",
    "expansions_for",
    "meta_expansion_table",
    "modifier_terms",
    "posting_terms",
]


class JavaLanguage:
    """Java, via tree-sitter.

    The scanner is built lazily on first use: importing this module must not
    load a grammar, or the package stops importing wherever tree-sitter is
    absent and pure-logic tests go down with it.
    """

    name = "java"
    file_globs = ("*.java",)

    #: Tests and generated sources dilute ICF and are almost never a query's
    #: target. `example` is here for the same reason -- sample code answers
    #: "how do I use this", which is not what a code search is asking.
    skip_parts = ("/test/", "/tests/", "/generated/", "/target/", "/build/", "/example/")

    #: Parameters are excluded deliberately: they are enormously numerous and
    #: almost never queried on their own, and indexing them would roughly
    #: double the postings for nothing.
    indexed_kinds = frozenset(
        {
            "class",
            "interface",
            "enum",
            "record",
            "annotation_type",
            "method",
            "constructor",
            "field",
        }
    )

    container_kinds = frozenset({"class", "interface", "enum", "record", "annotation_type"})

    def __init__(self, scanner: JavaDeclarationScanner | None = None) -> None:
        self._scanner = scanner

    @property
    def scanner(self) -> JavaDeclarationScanner:
        if self._scanner is None:
            self._scanner = JavaDeclarationScanner.for_java()
        return self._scanner

    def scan(self, source: str) -> ScanResult:
        return self.scanner.scan(source)

    def modifier_terms(self, declaration: Declaration) -> Sequence[tuple[str, str]]:
        return modifier_terms(declaration)

    def annotation_terms(self, declaration: Declaration, split: object) -> list[tuple[str, str]]:
        """The (term, field) pairs this declaration's annotations produce.

        Split out from the generic posting extraction because an annotation's
        *arguments* are Java-shaped: `@PreAuthorize("@ss.hasPerm('sys:user')")`
        carries a permission string that means nothing to another language's
        tokeniser.
        """
        found: list[tuple[str, str]] = []
        for use in declaration.annotations:
            found += posting_terms(use, split)  # type: ignore[arg-type]
        return found

    def expansions(self) -> Mapping[str, Sequence[tuple[str, float, str]]]:
        """Spring, JPA and JUnit relations, as facts rather than estimates."""
        return meta_expansion_table()

    def derive_relations(self, context: RelationContext) -> RelationBatch:
        """Resolve Java-specific project relations after scanning."""
        return derive_java_relations(context)
