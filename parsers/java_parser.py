from typing import List, Tuple

from .base import SymbolItem, RangeInfo, DependencyItem

try:
    from tree_sitter_languages import get_parser
except Exception:
    get_parser = None


def parse_java(file_rel: str, source: str):
    symbols: List[SymbolItem] = []
    calls: List[Tuple[str, str, int, str]] = []
    deps: List[DependencyItem] = []
    lines = source.splitlines()

    if get_parser is None:
        return symbols, calls, deps

    try:
        parser = get_parser("java")
        tree = parser.parse(source.encode("utf-8"))
        root = tree.root_node
    except Exception:
        return symbols, calls, deps

    src_bytes = source.encode("utf-8")

    def node_text(node) -> str:
        return src_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="ignore")

    def node_range(node) -> RangeInfo:
        return RangeInfo(node.start_point[0] + 1, node.end_point[0] + 1)

    def node_name_pos(node):
        n = node.child_by_field_name("name")
        if n is None:
            return [node.start_point[0] + 1, node.start_point[1]]
        return [n.start_point[0] + 1, n.start_point[1]]

    def child_name(node) -> str:
        n = node.child_by_field_name("name")
        return node_text(n) if n else ""

    def package_name_from_root() -> str:
        for ch in root.children:
            if ch.type != "package_declaration":
                continue
            for c in ch.children:
                if c.type in ("scoped_identifier", "identifier"):
                    return node_text(c)
        return ""

    package_name = package_name_from_root()

    # import deps
    for ch in root.children:
        if ch.type != "import_declaration":
            continue
        imp_text = node_text(ch)
        imp_text = imp_text.replace("import", "").replace("static", "").replace(";", "").strip()
        if imp_text:
            deps.append(DependencyItem(file_rel, imp_text.replace(".", "/"), "import"))

    def walk(node, container: str = ""):
        ntype = node.type

        if ntype == "class_declaration":
            cname = child_name(node) or "AnonymousClass"
            symbols.append(
                SymbolItem(
                    name=cname,
                    type="class",
                    file=file_rel,
                    range=node_range(node),
                    name_pos=node_name_pos(node),
                    signature=f"class {cname}",
                    language="java",
                    doc="",
                    container=container or package_name,
                )
            )
            class_container = cname
            for ch in node.children:
                walk(ch, class_container)
            return

        if ntype == "field_declaration":
            for ch in node.children:
                if ch.type != "variable_declarator":
                    continue
                vname = child_name(ch)
                if not vname:
                    continue
                symbols.append(
                    SymbolItem(
                        name=vname,
                        type="variable",
                        file=file_rel,
                        range=node_range(ch),
                        name_pos=node_name_pos(ch),
                        signature=vname,
                        language="java",
                        doc="",
                        container=container,
                    )
                )

        if ntype == "method_declaration":
            mname = child_name(node)
            params_node = node.child_by_field_name("parameters")
            signature = f"{mname}{node_text(params_node) if params_node else '()'}"

            symbols.append(
                SymbolItem(
                    name=mname,
                    type="method",
                    file=file_rel,
                    range=node_range(node),
                    name_pos=node_name_pos(node),
                    signature=signature,
                    language="java",
                    doc="",
                    container=container,
                )
            )

            caller = f"{container}.{mname}" if container else mname

            def collect_method_invocations(n, out):
                if n.type == "method_invocation":
                    out.append(n)
                for c in n.children:
                    collect_method_invocations(c, out)

            invocations = []
            collect_method_invocations(node, invocations)
            for inv in invocations:
                callee = child_name(inv) or ""
                i_line = inv.start_point[0] + 1
                code = lines[i_line - 1].strip() if 1 <= i_line <= len(lines) else ""
                calls.append((caller, callee, i_line, code))

        if ntype == "constructor_declaration":
            kname = child_name(node) or container
            params_node = node.child_by_field_name("parameters")
            signature = f"{kname}{node_text(params_node) if params_node else '()'}"

            symbols.append(
                SymbolItem(
                    name=kname,
                    type="method",
                    file=file_rel,
                    range=node_range(node),
                    name_pos=node_name_pos(node),
                    signature=signature,
                    language="java",
                    doc="",
                    container=container,
                )
            )

            caller = f"{container}.{kname}" if container else kname

            def collect_ctor_invocations(n, out):
                if n.type == "method_invocation":
                    out.append(n)
                for c in n.children:
                    collect_ctor_invocations(c, out)

            invocations = []
            collect_ctor_invocations(node, invocations)
            for inv in invocations:
                callee = child_name(inv) or ""
                i_line = inv.start_point[0] + 1
                code = lines[i_line - 1].strip() if 1 <= i_line <= len(lines) else ""
                calls.append((caller, callee, i_line, code))

        for ch in node.children:
            walk(ch, container)

    walk(root, "")
    return symbols, calls, deps
