"""Tests for `JavaDeclarationScanner` and modifiers.

One traversal collects both annotations and modifiers -- they hang off the
same `modifiers` node.
"""

from __future__ import annotations

import pytest

from codesense.lang import Declaration
from codesense.lang.java import JAVA_MODIFIERS, JavaDeclarationScanner, modifier_terms

SOURCE = """
public abstract class Base {
    private static final int MAX = 10;

    @Async
    public static native void fast() {}

    public synchronized void run() {
        Runnable r = new Runnable() {
            @Override public void go() {}
        };
    }

    protected abstract void hook();
}

interface Marker { void x(); }
"""

REFERENCE_SOURCE = """import org.springframework.data.domain.PageRequest;

class Controller {
    PageRequest list() {
        return PageRequest.ofSize(20);
    }
}
"""


class TestModifierTerms:
    def test_one_posting_per_modifier(self) -> None:
        declaration = Declaration(
            kind="method", name="f", line=1, modifiers=frozenset({"static", "public"})
        )
        assert set(modifier_terms(declaration)) == {("public", "modifier"), ("static", "modifier")}

    def test_empty_when_there_are_no_modifiers(self) -> None:
        assert modifier_terms(Declaration(kind="method", name="f", line=1)) == []

    def test_output_is_ordered_so_results_reproduce(self) -> None:
        declaration = Declaration(
            kind="method", name="f", line=1, modifiers=frozenset({"static", "abstract", "public"})
        )
        assert [term for term, _ in modifier_terms(declaration)] == [
            "abstract",
            "public",
            "static",
        ]


@pytest.mark.slow
class TestJavaDeclarationScanner:
    @pytest.fixture(scope="class")
    @classmethod
    def scanner(cls) -> JavaDeclarationScanner:
        pytest.importorskip("tree_sitter_languages")
        return JavaDeclarationScanner.for_java()

    @pytest.fixture(scope="class")
    @classmethod
    def declarations(cls, scanner: JavaDeclarationScanner) -> dict[str, Declaration]:
        result = scanner.scan(SOURCE)
        return {d.name: d for d in result.declarations if d.name}

    def test_modifiers_on_a_class(self, declarations: dict[str, Declaration]) -> None:
        assert declarations["Base"].modifiers == {"public", "abstract"}

    def test_modifiers_on_a_field(self, declarations: dict[str, Declaration]) -> None:
        assert declarations["MAX"].modifiers == {"private", "static", "final"}

    def test_modifiers_on_a_method(self, declarations: dict[str, Declaration]) -> None:
        assert declarations["fast"].modifiers == {"public", "static", "native"}
        assert declarations["run"].modifiers == {"public", "synchronized"}

    def test_abstract_method(self, declarations: dict[str, Declaration]) -> None:
        assert "abstract" in declarations["hook"].modifiers

    def test_declaration_without_modifiers(self, declarations: dict[str, Declaration]) -> None:
        assert declarations["x"].modifiers == frozenset()

    def test_the_same_scan_also_yields_annotations(
        self, declarations: dict[str, Declaration]
    ) -> None:
        assert [a.name for a in declarations["fast"].annotations] == ["Async"]

    def test_anonymous_class_modifiers_not_attributed_to_the_outer_method(
        self, declarations: dict[str, Declaration]
    ) -> None:
        """The anonymous class in `run` has its own `modifiers`; descending
        into it would attribute them to the wrong declaration."""
        assert declarations["run"].modifiers == {"public", "synchronized"}
        assert declarations["go"].modifiers == {"public"}
        assert [a.name for a in declarations["run"].annotations] == []

    def test_every_extracted_modifier_is_a_known_one(
        self, declarations: dict[str, Declaration]
    ) -> None:
        for declaration in declarations.values():
            assert declaration.modifiers <= JAVA_MODIFIERS

    def test_kind_and_line_are_recorded(self, declarations: dict[str, Declaration]) -> None:
        assert declarations["Base"].kind == "class"
        assert declarations["fast"].kind == "method"
        assert all(d.line > 0 for d in declarations.values())

    def test_scan_result_keeps_declarations_and_references(
        self, scanner: JavaDeclarationScanner
    ) -> None:
        result = scanner.scan(REFERENCE_SOURCE)
        assert {declaration.name for declaration in result.declarations} >= {
            "Controller",
            "list",
        }
        page_request = [use for use in result.references if use.name == "PageRequest"]
        assert {use.relation for use in page_request} >= {"imports", "references"}
        assert all(use.line > 0 and use.column >= 0 for use in page_request)

    def test_import_keeps_the_qualified_target(self, scanner: JavaDeclarationScanner) -> None:
        imported = next(
            use for use in scanner.scan(REFERENCE_SOURCE).references if use.relation == "imports"
        )
        assert imported.qualified_name == "org.springframework.data.domain.PageRequest"
        assert imported.target_kind == "type"

    def test_type_and_static_receiver_references_keep_source_order_and_sites(
        self, scanner: JavaDeclarationScanner
    ) -> None:
        references = [
            use
            for use in scanner.scan(REFERENCE_SOURCE).references
            if use.name == "PageRequest" and use.relation == "references"
        ]
        assert [(use.line, use.column) for use in references] == [(4, 4), (5, 15)]

    def test_invocation_keeps_its_source_line(self, scanner: JavaDeclarationScanner) -> None:
        declarations = scanner.scan(REFERENCE_SOURCE).declarations
        method = next(declaration for declaration in declarations if declaration.name == "list")
        call = next(call for call in method.calls if call.name == "ofSize")
        assert call.receiver == "PageRequest"
        assert call.line == 5

    def test_scan_parses_the_source_once(self) -> None:
        tree_sitter_languages = pytest.importorskip("tree_sitter_languages")

        class CountingParser:
            def __init__(self) -> None:
                self.calls = 0
                self.parser = tree_sitter_languages.get_parser("java")

            def parse(self, data: bytes) -> object:
                self.calls += 1
                return self.parser.parse(data)

        parser = CountingParser()
        JavaDeclarationScanner(parser).scan(REFERENCE_SOURCE)
        assert parser.calls == 1

    def test_wildcard_import_does_not_claim_a_type_target(
        self, scanner: JavaDeclarationScanner
    ) -> None:
        result = scanner.scan("import org.example.*;\nclass Example {}\n")
        assert [use for use in result.references if use.relation == "imports"] == []
