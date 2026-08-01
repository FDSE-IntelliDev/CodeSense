"""Tests for `JavaDeclarationScanner` and modifiers.

One traversal collects both annotations and modifiers -- they hang off the
same `modifiers` node.
"""

from __future__ import annotations

import pytest

from codesense.lang import Declaration
from codesense.lang.java import JAVA_MODIFIERS, modifier_terms

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
    def declarations(self) -> dict[str, Declaration]:
        pytest.importorskip("tree_sitter_languages")
        from codesense.lang.java import JavaDeclarationScanner

        scanned = JavaDeclarationScanner.for_java().scan(SOURCE)
        return {d.name: d for d in scanned if d.name}

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
