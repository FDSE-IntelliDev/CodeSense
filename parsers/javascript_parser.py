import re
from typing import List, Tuple

from .base import SymbolItem, RangeInfo, DependencyItem

try:
    from tree_sitter_languages import get_parser
except Exception:
    get_parser = None


def parse_javascript_typescript(file_rel: str, source: str, language: str):
    symbols: List[SymbolItem] = []
    calls: List[Tuple[str, str, int, str]] = []
    deps: List[DependencyItem] = []
    lines = source.splitlines()

    import_patterns = [
        r'^\s*import\s+.*?\s+from\s+[\'\"](.+?)[\'\"]',
        r'^\s*import\s+[\'\"](.+?)[\'\"]',
        r'^\s*const\s+.*?=\s*require\([\'\"](.+?)[\'\"]\)',
        r'^\s*require\([\'\"](.+?)[\'\"]\)',
    ]
    for ln in lines:
        for pat in import_patterns:
            m = re.search(pat, ln)
            if m:
                deps.append(DependencyItem(file_rel, m.group(1), "import" if "import" in ln else "require"))

    if get_parser is None:
        return symbols, calls, deps

    try:
        parser = get_parser("typescript" if language == "typescript" else "javascript")
        tree = parser.parse(source.encode("utf-8"))
        root = tree.root_node
    except Exception:
        return symbols, calls, deps

    src_bytes = source.encode("utf-8")

    def node_text(n):
        return src_bytes[n.start_byte:n.end_byte].decode("utf-8", errors="ignore")

    def node_name_pos(n):
        return [n.start_point[0] + 1, n.start_point[1]]

    def walk(node, container=""):
        ntype = node.type

        if ntype in ("function_declaration", "method_definition"):
            name_node = node.child_by_field_name("name")
            params_node = node.child_by_field_name("parameters")
            if name_node:
                name = node_text(name_node)
                is_method = ntype == "method_definition" or bool(container)
                symbols.append(
                    SymbolItem(
                        name=name,
                        type="method" if is_method else "function",
                        file=file_rel,
                        range=RangeInfo(node.start_point[0] + 1, node.end_point[0] + 1),
                        name_pos=node_name_pos(name_node),
                        signature=f"{name}{node_text(params_node) if params_node else '()'}",
                        language=language,
                        doc="",
                        container=container,
                    )
                )
                container2 = name if not container else f"{container}.{name}"
            else:
                container2 = container
            for ch in node.children:
                walk(ch, container2)
            return

        if ntype == "class_declaration":
            name_node = node.child_by_field_name("name")
            cname = node_text(name_node) if name_node else "AnonymousClass"
            symbols.append(
                SymbolItem(
                    name=cname,
                    type="class",
                    file=file_rel,
                    range=RangeInfo(node.start_point[0] + 1, node.end_point[0] + 1),
                    name_pos=node_name_pos(name_node) if name_node else [node.start_point[0] + 1, node.start_point[1]],
                    signature=f"class {cname}",
                    language=language,
                    doc="",
                    container=container,
                )
            )
            for ch in node.children:
                walk(ch, cname)
            return

        if ntype in ("lexical_declaration", "variable_declaration"):
            text = node_text(node)
            names = re.findall(r"(?:const|let|var)\s+([A-Za-z_]\w*)", text)
            for vn in names:
                symbols.append(
                    SymbolItem(
                        name=vn,
                        type="variable",
                        file=file_rel,
                        range=RangeInfo(node.start_point[0] + 1, node.end_point[0] + 1),
                        name_pos=[
                            node.start_point[0] + 1,
                            lines[node.start_point[0]].find(vn) if node.start_point[0] < len(lines) else node.start_point[1],
                        ],
                        signature=vn,
                        language=language,
                        doc="",
                        container=container,
                    )
                )

        if ntype == "call_expression":
            fn = node.child_by_field_name("function")
            callee = node_text(fn).split(".")[-1] if fn else ""
            line = node.start_point[0] + 1
            code = lines[line - 1].strip() if 1 <= line <= len(lines) else ""
            calls.append((container or "<module>", callee, line, code))

        for ch in node.children:
            walk(ch, container)

    walk(root, "")
    return symbols, calls, deps
