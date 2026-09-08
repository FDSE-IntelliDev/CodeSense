"""What a language must provide, and the vocabulary every language speaks.

Adding a language means writing one `Language` adapter -- everything above
this layer (postings, graph construction, grounding, search) is written
against these scan facts and never against Java.

The types here are the negotiated middle ground. They are deliberately not
"the union of every language's AST": that would make the adapter trivial and
every consumer complicated. They are instead **what retrieval needs**:

    what is it        kind, name, container
    what does it say  signature, doc, annotations, modifiers
    what does it use  calls, local_types, supertypes

A language that has no annotations leaves the tuple empty; one whose
"modifiers" are decorators maps them onto `modifiers`. The mapping is the
adapter's job and its judgement call, which is exactly where it belongs.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

__all__ = [
    "AnnotationUse",
    "Declaration",
    "Invocation",
    "Language",
    "ReferenceUse",
    "ScanResult",
]


@dataclass(frozen=True, slots=True)
class Invocation:
    """One call site.

    ``receiver`` is the raw text left of the dot (`""` means no receiver, so
    `m()` or `this.m()`). The text is kept rather than resolved here:
    resolving needs the whole project's type table, which belongs to
    `codesense.indexing.graph`.
    """

    name: str
    receiver: str = ""
    line: int = 0


@dataclass(frozen=True, slots=True)
class AnnotationUse:
    """One use of an annotation, decorator, or attribute.

    Java annotations, Python decorators, C# attributes and Rust derives all
    land here. ``target_kind`` records what it is attached to -- the same
    annotation on a method and on a class usually mean different things.
    """

    name: str
    args: str = ""
    target_kind: str = ""
    target_name: str = ""
    line: int = 0

    @property
    def at_name(self) -> str:
        """The ``@``-prefixed spelling, used as the expansion table's key."""
        return f"@{self.name}"


@dataclass(frozen=True, slots=True)
class Declaration:
    """One indexable declaration, in language-neutral terms."""

    name: str
    kind: str
    line: int = 0
    end_line: int = 0
    container: str = ""
    signature: str = ""
    doc: str = ""
    modifiers: frozenset[str] = frozenset()
    annotations: tuple[AnnotationUse, ...] = ()

    #: Call sites within this declaration's body, carrying the **receiver
    #: expression** so downstream can narrow by type without a type checker.
    calls: tuple[Invocation, ...] = ()

    #: `variable name -> declared type` visible inside this declaration.
    #: Empty for languages without declared types; call resolution then falls
    #: back to matching on name, which the confidence score reflects.
    local_types: tuple[tuple[str, str], ...] = ()

    #: What this type extends or implements. Method lookup walks up it.
    supertypes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ReferenceUse:
    """One syntactic use of a potentially indexable target.

    The language adapter records source facts only. Resolving ``name`` or
    ``qualified_name`` against project declarations belongs to the indexing
    graph, after every file has been scanned.
    """

    name: str
    line: int
    column: int = 0
    relation: str = "references"
    target_kind: str = ""
    qualified_name: str = ""


@dataclass(frozen=True, slots=True)
class ScanResult:
    """All declaration and reference facts produced by one source parse."""

    declarations: tuple[Declaration, ...]
    references: tuple[ReferenceUse, ...] = ()


@runtime_checkable
class Language(Protocol):
    """The adapter one language needs to supply.

    Everything is a property rather than a constant so an adapter can compute
    it -- a language whose file extensions depend on a dialect is free to.
    """

    #: Stable identifier, stored on every symbol. Queries filter on it.
    name: str

    #: Globs for source files, e.g. ``("*.java",)``.
    file_globs: tuple[str, ...]

    #: Path fragments whose files are skipped: tests, generated code, vendored
    #: dependencies. These dilute ICF and are almost never a query's target.
    skip_parts: tuple[str, ...]

    #: Kinds worth indexing. Parameters and locals are usually excluded -- they
    #: are enormously numerous and rarely queried on their own.
    indexed_kinds: frozenset[str]

    #: Kinds that can contain other declarations, so `contains` edges can be
    #: derived from the container field.
    container_kinds: frozenset[str]

    def scan(self, source: str) -> ScanResult:
        """Parse one file into declarations and syntactic reference uses.

        Must not raise on malformed input if it can avoid it: one unparseable
        file should cost that file, not the build. Adapters should derive both
        result collections from the same parse tree.
        """
        ...

    def expansions(self) -> Mapping[str, Sequence[tuple[str, float, str]]]:
        """Relations the language or its frameworks declare as fact.

        Spring's `@RestController` really *is* `@Controller` plus
        `@ResponseBody`, and that is not an estimate -- it is written in the
        framework's source. Such entries enter the expansion table at 1.0,
        above anything a vector or a lexical rule can claim.

        Return an empty mapping when the language has nothing of the sort.
        """
        ...


@dataclass
class LanguageRegistry:
    """Languages by name.

    A registry rather than a dict so a duplicate registration fails loudly.
    Two adapters silently claiming `java` would make which one runs depend on
    import order.
    """

    _by_name: dict[str, Language] = field(default_factory=dict)

    def register(self, language: Language) -> Language:
        if language.name in self._by_name:
            raise ValueError(f"a language named {language.name!r} is already registered")
        self._by_name[language.name] = language
        return language

    def get(self, name: str) -> Language:
        try:
            return self._by_name[name]
        except KeyError:
            known = ", ".join(sorted(self._by_name)) or "none"
            raise KeyError(f"unknown language {name!r}; registered: {known}") from None

    def for_path(self, path: str) -> Language | None:
        """The language claiming this file, or None.

        Used to index a repository that holds more than one language.
        """
        from fnmatch import fnmatch

        for language in self._by_name.values():
            if any(fnmatch(path, glob) for glob in language.file_globs):
                return language
        return None

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._by_name))

    def __len__(self) -> int:
        return len(self._by_name)
