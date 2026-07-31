import ast
from typing import List, Tuple

from .base import SymbolItem, RangeInfo, DependencyItem


class PythonAnalyzer(ast.NodeVisitor):
    def __init__(self, file_rel: str, source: str):
        self.file_rel = file_rel
        self.source = source.splitlines()
        self.symbols: List[SymbolItem] = []
        self.calls: List[Tuple[str, str, int, str]] = []
        self.deps: List[DependencyItem] = []
        self.scope_stack: List[str] = []

    def _curr_container(self) -> str:
        if not self.scope_stack:
            return ""
        return ".".join(self.scope_stack[:-1]) if len(self.scope_stack) > 1 else ""

    def _curr_func(self) -> str:
        return ".".join(self.scope_stack) if self.scope_stack else "<module>"

    def _name_pos(self, node, name: str) -> List[int]:
        line_no = getattr(node, "lineno", 1)
        line = self.source[line_no - 1] if 1 <= line_no <= len(self.source) else ""
        start_col = getattr(node, "col_offset", 0)
        found_col = line.find(name, start_col)
        if found_col < 0:
            found_col = line.find(name)
        return [line_no, found_col if found_col >= 0 else start_col]

    def visit_ClassDef(self, node: ast.ClassDef):
        self.scope_stack.append(node.name)
        self.symbols.append(
            SymbolItem(
                name=node.name,
                type="class",
                file=self.file_rel,
                range=RangeInfo(node.lineno, getattr(node, "end_lineno", node.lineno)),
                name_pos=self._name_pos(node, node.name),
                signature=f"class {node.name}",
                language="python",
                doc=ast.get_docstring(node) or "",
                container=self._curr_container(),
            )
        )
        self.generic_visit(node)
        self.scope_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef):
        stype = "method" if self.scope_stack else "function"
        self.scope_stack.append(node.name)
        args = [a.arg for a in node.args.args]
        self.symbols.append(
            SymbolItem(
                name=node.name,
                type=stype,
                file=self.file_rel,
                range=RangeInfo(node.lineno, getattr(node, "end_lineno", node.lineno)),
                name_pos=self._name_pos(node, node.name),
                signature=f"def {node.name}({', '.join(args)})",
                language="python",
                doc=ast.get_docstring(node) or "",
                container=self._curr_container(),
            )
        )
        self.generic_visit(node)
        self.scope_stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Assign(self, node: ast.Assign):
        if self.scope_stack:
            self.generic_visit(node)
            return
        for t in node.targets:
            if isinstance(t, ast.Name):
                self.symbols.append(
                    SymbolItem(
                        name=t.id,
                        type="variable",
                        file=self.file_rel,
                        range=RangeInfo(node.lineno, getattr(node, "end_lineno", node.lineno)),
                        name_pos=[getattr(t, "lineno", node.lineno), getattr(t, "col_offset", 0)],
                        signature=t.id,
                        language="python",
                        doc="",
                        container="",
                    )
                )
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import):
        for alias in node.names:
            mod = alias.name.replace(".", "/")
            self.deps.append(DependencyItem(self.file_rel, mod, "import"))
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        mod = (node.module or "").replace(".", "/")
        if mod:
            self.deps.append(DependencyItem(self.file_rel, mod, "import"))
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call):
        callee = ""
        if isinstance(node.func, ast.Name):
            callee = node.func.id
        elif isinstance(node.func, ast.Attribute):
            callee = node.func.attr
        line = getattr(node, "lineno", 1)
        code = self.source[line - 1].strip() if 1 <= line <= len(self.source) else ""
        self.calls.append((self._curr_func(), callee, line, code))
        self.generic_visit(node)


def parse_python(file_rel: str, source: str):
    try:
        tree = ast.parse(source)
        analyzer = PythonAnalyzer(file_rel, source)
        analyzer.visit(tree)
        return analyzer.symbols, analyzer.calls, analyzer.deps
    except Exception:
        return [], [], []
