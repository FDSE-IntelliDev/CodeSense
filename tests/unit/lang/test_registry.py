"""Tests for the language extension point.

What matters here is that **adding a language means writing one adapter**. So
these tests define a fake language from scratch and check the machinery
accepts it -- if that ever needs a change to `codesense.indexing`, the
extension point has failed at its job.
"""

from __future__ import annotations

import pytest

from codesense.lang import (
    LANGUAGES,
    Declaration,
    IndexedDeclaration,
    Language,
    LanguageRegistry,
    ReferenceUse,
    RelationBatch,
    RelationContext,
    ScanResult,
)


class ToyLanguage:
    """The whole of an adapter, for a language with one-line declarations."""

    name = "toy"
    file_globs = ("*.toy",)
    skip_parts = ("/vendor/",)
    indexed_kinds = frozenset({"function"})
    container_kinds = frozenset({"module"})

    def scan(self, source: str) -> ScanResult:
        return ScanResult(
            declarations=tuple(
                Declaration(name=line.strip(), kind="function", line=n)
                for n, line in enumerate(source.splitlines(), 1)
                if line.strip()
            )
        )

    def expansions(self) -> dict:
        return {}

    def derive_relations(self, context: RelationContext) -> RelationBatch:
        assert all(item.declaration.kind == "function" for item in context.declarations)
        return RelationBatch()


class TestProtocol:
    def test_scan_fact_types_are_language_neutral(self) -> None:
        use = ReferenceUse(name="Widget", line=3, target_kind="type")
        assert ScanResult(declarations=(), references=(use,)).references == (use,)

    def test_relation_fact_types_are_language_neutral(self) -> None:
        declaration = Declaration("Child", "class", supertypes=("Base",))
        indexed = IndexedDeclaration(2, "Child.toy", declaration)
        context = RelationContext((indexed,))

        assert context.declarations == (indexed,)
        assert RelationBatch().facts == ()

    def test_a_minimal_adapter_satisfies_the_protocol(self) -> None:
        assert isinstance(ToyLanguage(), Language)

    def test_the_java_adapter_satisfies_it_too(self) -> None:
        from codesense.lang.java import JavaLanguage

        assert isinstance(JavaLanguage(), Language)

    def test_an_adapter_may_declare_no_expansions(self) -> None:
        """Framework relations are a Java/Spring luxury, not a requirement."""
        assert ToyLanguage().expansions() == {}


class TestRegistry:
    def test_registers_and_returns(self) -> None:
        registry = LanguageRegistry()
        registry.register(ToyLanguage())
        assert registry.get("toy").name == "toy"

    def test_a_duplicate_fails_loudly(self) -> None:
        """Two adapters silently claiming one name would make which runs
        depend on import order."""
        registry = LanguageRegistry()
        registry.register(ToyLanguage())
        with pytest.raises(ValueError, match="already registered"):
            registry.register(ToyLanguage())

    def test_an_unknown_name_lists_what_is_registered(self) -> None:
        registry = LanguageRegistry()
        registry.register(ToyLanguage())
        with pytest.raises(KeyError, match="toy"):
            registry.get("cobol")

    def test_matches_a_file_to_its_language(self) -> None:
        registry = LanguageRegistry()
        registry.register(ToyLanguage())
        assert registry.for_path("src/a.toy").name == "toy"

    def test_an_unclaimed_file_matches_nothing(self) -> None:
        registry = LanguageRegistry()
        registry.register(ToyLanguage())
        assert registry.for_path("src/a.rs") is None

    def test_names_are_sorted_so_builds_reproduce(self) -> None:
        registry = LanguageRegistry()
        registry.register(ToyLanguage())

        class Other(ToyLanguage):
            name = "abc"

        registry.register(Other())
        assert registry.names() == ("abc", "toy")


class TestBuiltins:
    def test_java_is_registered(self) -> None:
        assert "java" in LANGUAGES.names()

    def test_java_claims_java_files(self) -> None:
        assert LANGUAGES.for_path("src/main/java/A.java").name == "java"

    def test_java_excludes_parameters_from_indexing(self) -> None:
        """They are enormously numerous and almost never queried alone."""
        assert "parameter" not in LANGUAGES.get("java").indexed_kinds


class TestIndexingIsLanguageAgnostic:
    def test_builds_an_index_from_a_language_it_has_never_heard_of(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """The point of the whole layer: no change to `codesense.indexing` was
        needed to index a language invented in this test file."""
        from codesense.indexing import build_index

        (tmp_path / "a.toy").write_text("readBuffer\nwriteBuffer\n")
        result = build_index(tmp_path, languages=[ToyLanguage()])
        assert result.stats.symbols == 3
        assert result.stats.declarations == 2
        assert result.payload["declaration_count"] == 2
        assert "buffer" in result.payload["postings"]

    def test_records_which_language_each_symbol_came_from(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        from codesense.indexing import build_index

        (tmp_path / "a.toy").write_text("readBuffer\n")
        assert (
            build_index(tmp_path, languages=[ToyLanguage()]).payload["symbols"][0]["language"]
            == "toy"
        )

    def test_honours_the_skip_list(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        from codesense.indexing import build_index

        vendor = tmp_path / "vendor"
        vendor.mkdir()
        (vendor / "b.toy").write_text("skipMe\n")
        (tmp_path / "a.toy").write_text("keepMe\n")
        result = build_index(tmp_path, languages=[ToyLanguage()])
        assert [s["name"] for s in result.payload["symbols"]] == ["keepMe", "a.toy"]

    def test_refuses_to_build_with_no_adapter(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        from codesense.indexing import build_index

        with pytest.raises(ValueError, match="no language adapters"):
            build_index(tmp_path, languages=[])
