import re
from typing import List, Tuple

from .base import SymbolItem, RangeInfo, DependencyItem

try:
    from tree_sitter_languages import get_parser
except Exception:
    get_parser = None


def parse_c_cpp(file_rel: str, source: str, language: str):
    symbols: List[SymbolItem] = []
    calls: List[Tuple[str, str, int, str]] = []
    deps: List[DependencyItem] = []
    lines = source.splitlines()

    include_pattern = re.compile(r'^\s*#\s*include\s*[<"](.*?)[>"]')
    for ln in lines:
        m = include_pattern.search(ln)
        if m:
            deps.append(DependencyItem(file_rel, m.group(1), "#include"))

    if get_parser is None:
        return symbols, calls, deps

    try:
        parser = get_parser("cpp" if language == "cpp" else "c")
        tree = parser.parse(source.encode("utf-8"))
        root = tree.root_node
    except Exception as e:
        print(f"Parse {language} failed: {e}")
        return symbols, calls, deps

    src_bytes = source.encode("utf-8")

    def node_text(n):
        return src_bytes[n.start_byte:n.end_byte].decode("utf-8", errors="ignore")

    def get_identifier(n):
        if n.type in ("identifier", "field_identifier", "type_identifier"):
            return n
        for child in n.children:
            res = get_identifier(child)
            if res:
                return res
        return None

    def walk(node, container=""):
        ntype = node.type

        if ntype in ("function_definition", "declaration"):
            decl_node = node.child_by_field_name("declarator")
            name = ""
            if decl_node:
                id_node = get_identifier(decl_node)
                if id_node:
                    name = node_text(id_node)

            if name:
                is_decl_only = (ntype == "declaration")
                # Exclude simple variable declarations if we just want functions
                # but if it has a function_declarator inside, it's a function declaration.
                has_func_decl = False
                def check_func(n):
                    nonlocal has_func_decl
                    if n.type == "function_declarator":
                        has_func_decl = True
                    for c in n.children:
                        check_func(c)
                check_func(node)

                if has_func_decl or not is_decl_only:
                    stype = "function" if not is_decl_only else "function_decl"
                    symbols.append(
                        SymbolItem(
                            name=name,
                            type=stype,
                            file=file_rel,
                            range=RangeInfo(node.start_point[0] + 1, node.end_point[0] + 1),
                            signature=node_text(decl_node)[:100] if decl_node else name,
                            language=language,
                            doc="",
                            container=container,
                        )
                    )
                container2 = name if (ntype == "function_definition" and not container) else (f"{container}.{name}" if ntype == "function_definition" else container)
            else:
                container2 = container

            if ntype == "function_definition":
                for ch in node.children:
                    walk(ch, container2)
                return

        if ntype in ("struct_specifier", "class_specifier", "enum_specifier"):
            name_node = node.child_by_field_name("name")
            if name_node:
                cname = node_text(name_node)
            else:
                cname = "Anonymous" + ntype.split("_")[0].capitalize()

            symbols.append(
                SymbolItem(
                    name=cname,
                    type=ntype.split("_")[0],
                    file=file_rel,
                    range=RangeInfo(node.start_point[0] + 1, node.end_point[0] + 1),
                    signature=f"{ntype.split('_')[0]} {cname}",
                    language=language,
                    doc="",
                    container=container,
                )
            )
            for ch in node.children:
                walk(ch, cname)
            return

        if ntype == "call_expression":
            fn = node.child_by_field_name("function")
            if fn:
                id_node = get_identifier(fn)
                callee = node_text(id_node) if id_node else ""
            else:
                callee = ""

            line = node.start_point[0] + 1
            code = lines[line - 1].strip() if 1 <= line <= len(lines) else ""
            if callee:
                calls.append((container or "<module>", callee, line, code))

        for ch in node.children:
            walk(ch, container)

    walk(root, "")
    return symbols, calls, deps
