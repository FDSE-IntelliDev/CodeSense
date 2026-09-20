"""Language adapters.

**The extension point.** Supporting a new language means writing one adapter
satisfying `Language` and registering it here; nothing above this package
mentions a specific language.

    from codesense.lang import LANGUAGES, Language

    class GoLanguage:
        name = "go"
        file_globs = ("*.go",)
        ...
        def scan(self, source): ...
        def expansions(self): return {}

    LANGUAGES.register(GoLanguage())

Registration happens on import of this package, so a language that is not
importable in the current environment does not break the others: Java needs
tree-sitter, and a machine without it should still be able to load an index
and run lexical queries.
"""

from __future__ import annotations

import logging

from codesense.lang.base import (
    AnnotationUse,
    Declaration,
    IndexedDeclaration,
    Invocation,
    Language,
    LanguageRegistry,
    ReferenceUse,
    RelationBatch,
    RelationContext,
    RelationDiagnostics,
    RelationFact,
    ScanResult,
)

__all__ = [
    "AnnotationUse",
    "Declaration",
    "IndexedDeclaration",
    "Invocation",
    "LANGUAGES",
    "Language",
    "LanguageRegistry",
    "ReferenceUse",
    "RelationBatch",
    "RelationContext",
    "RelationDiagnostics",
    "RelationFact",
    "ScanResult",
]

_log = logging.getLogger(__name__)

#: Every language the build knows about.
LANGUAGES = LanguageRegistry()


def _register_builtins() -> None:
    """Register the adapters that ship with the package.

    A failure to import one is logged and skipped rather than raised. The
    alternative -- one missing grammar taking down every language -- has no
    upside: an adapter that cannot load simply cannot index its files, and
    everything else still works.
    """
    from codesense.lang.java import JavaLanguage

    for factory in (JavaLanguage,):
        try:
            LANGUAGES.register(factory())
        except Exception:  # noqa: BLE001 -- one broken adapter must not sink the rest
            _log.exception("could not register the %s adapter", factory.__name__)


_register_builtins()
