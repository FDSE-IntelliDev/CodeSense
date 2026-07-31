"""`JavaDeclarationScanner` 与修饰符的测试。

一次遍历同时取注解与修饰符——两者挂在同一个 `modifiers` 节点上。
"""

from __future__ import annotations

import pytest

from codesense.indexing import JAVA_MODIFIERS, Declaration, modifier_terms

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
    def test_每个修饰符一条_posting(self) -> None:
        declaration = Declaration(
            kind="method", name="f", line=1, modifiers=frozenset({"static", "public"})
        )
        assert set(modifier_terms(declaration)) == {("public", "modifier"), ("static", "modifier")}

    def test_没有修饰符时为空(self) -> None:
        assert modifier_terms(Declaration(kind="method", name="f", line=1)) == []

    def test_输出有序_结果可复现(self) -> None:
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
        from codesense.indexing import JavaDeclarationScanner

        scanned = JavaDeclarationScanner.for_java().scan(SOURCE)
        return {d.name: d for d in scanned if d.name}

    def test_类的修饰符(self, declarations: dict[str, Declaration]) -> None:
        assert declarations["Base"].modifiers == {"public", "abstract"}

    def test_字段的修饰符(self, declarations: dict[str, Declaration]) -> None:
        assert declarations["MAX"].modifiers == {"private", "static", "final"}

    def test_方法的修饰符(self, declarations: dict[str, Declaration]) -> None:
        assert declarations["fast"].modifiers == {"public", "static", "native"}
        assert declarations["run"].modifiers == {"public", "synchronized"}

    def test_抽象方法(self, declarations: dict[str, Declaration]) -> None:
        assert "abstract" in declarations["hook"].modifiers

    def test_没有修饰符的声明(self, declarations: dict[str, Declaration]) -> None:
        assert declarations["x"].modifiers == frozenset()

    def test_同一次扫描也带出注解(self, declarations: dict[str, Declaration]) -> None:
        assert [a.name for a in declarations["fast"].annotations] == ["Async"]

    def test_匿名类的修饰符不算到外层方法头上(self, declarations: dict[str, Declaration]) -> None:
        """`run` 里的匿名类有自己的 `modifiers`，往下钻就会张冠李戴。"""
        assert declarations["run"].modifiers == {"public", "synchronized"}
        assert declarations["go"].modifiers == {"public"}
        assert [a.name for a in declarations["run"].annotations] == []

    def test_抽出来的修饰符都在已知集合里(self, declarations: dict[str, Declaration]) -> None:
        for declaration in declarations.values():
            assert declaration.modifiers <= JAVA_MODIFIERS

    def test_记录种类与行号(self, declarations: dict[str, Declaration]) -> None:
        assert declarations["Base"].kind == "class"
        assert declarations["fast"].kind == "method"
        assert all(d.line > 0 for d in declarations.values())
