import re
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
        return parse_java_regex(file_rel, source)

    try:
        parser = get_parser("java")
        tree = parser.parse(source.encode("utf-8"))
        root = tree.root_node
    except Exception:
        return parse_java_regex(file_rel, source)

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


def parse_java_regex(file_rel: str, source: str):
    """Dependency-free Java fallback used when tree-sitter is unavailable."""
    symbols: List[SymbolItem] = []
    calls: List[Tuple[str, str, int, str]] = []
    deps: List[DependencyItem] = []
    lines = source.splitlines()

    package_name = ""
    java_keywords = {
        "if",
        "for",
        "while",
        "switch",
        "catch",
        "return",
        "throw",
        "new",
        "super",
        "this",
        "try",
        "synchronized",
    }

    def line_col(line_no: int, name: str) -> List[int]:
        line = lines[line_no - 1] if 1 <= line_no <= len(lines) else ""
        col = line.find(name)
        return [line_no, col if col >= 0 else 0]

    def find_block_end(start_idx: int) -> int:
        depth = 0
        seen_open = False
        for idx in range(start_idx, len(lines)):
            line = re.sub(r"//.*", "", lines[idx])
            depth += line.count("{")
            if "{" in line:
                seen_open = True
            depth -= line.count("}")
            if seen_open and depth <= 0:
                return idx + 1
        return start_idx + 1

    for idx, line in enumerate(lines, start=1):
        pkg = re.match(r"\s*package\s+([\w.]+)\s*;", line)
        if pkg:
            package_name = pkg.group(1)
            continue

        imp = re.match(r"\s*import\s+(?:static\s+)?([\w.*]+)\s*;", line)
        if imp:
            deps.append(DependencyItem(file_rel, imp.group(1).replace(".", "/"), "import"))

    class_ranges: List[Tuple[str, int, int]] = []
    class_pattern = re.compile(r"\b(class|interface|enum|record)\s+([A-Za-z_]\w*)")
    for idx, line in enumerate(lines, start=1):
        match = class_pattern.search(line)
        if not match:
            continue
        kind, name = match.groups()
        end_line = find_block_end(idx - 1)
        class_ranges.append((name, idx, end_line))
        symbols.append(
            SymbolItem(
                name=name,
                type="class" if kind == "record" else kind,
                file=file_rel,
                range=RangeInfo(idx, end_line),
                name_pos=line_col(idx, name),
                signature=f"{kind} {name}",
                language="java",
                doc="",
                container=package_name,
            )
        )

    def container_for(line_no: int) -> str:
        best = ""
        best_span = 10**9
        for class_name, start, end in class_ranges:
            if start <= line_no <= end and end - start < best_span:
                best = class_name
                best_span = end - start
        return best

    method_pattern = re.compile(
        r"^\s*(?:@\w+(?:\([^)]*\))?\s*)*"
        r"(?:(?:public|protected|private|static|final|native|synchronized|abstract|default|strictfp)\s+)*"
        r"[\w<>\[\].?,\s]+\s+([A-Za-z_]\w*)\s*\(([^;{}]*)\)\s*(?:throws\s+[^{]+)?\{"
    )
    field_pattern = re.compile(
        r"^\s*(?:(?:public|protected|private|static|final|transient|volatile)\s+)+"
        r"[\w<>\[\].?,]+\s+([A-Za-z_]\w*)\s*(?:=|;)"
    )
    call_pattern = re.compile(r"\b([A-Za-z_]\w*)\s*\(")

    method_ranges: List[Tuple[int, int]] = []
    for idx, line in enumerate(lines, start=1):
        method_match = method_pattern.match(line)
        container = container_for(idx)
        ctor_match = None
        if container:
            ctor_match = re.match(
                r"^\s*(?:(?:public|protected|private)\s+)?%s\s*\(([^;{}]*)\)\s*(?:throws\s+[^{]+)?\{"
                % re.escape(container),
                line,
            )

        if not method_match and not ctor_match:
            continue

        name = container if ctor_match else method_match.group(1)
        params = ctor_match.group(1) if ctor_match else method_match.group(2)
        if name in java_keywords:
            continue

        end_line = find_block_end(idx - 1)
        method_ranges.append((idx, end_line))
        symbols.append(
            SymbolItem(
                name=name,
                type="method",
                file=file_rel,
                range=RangeInfo(idx, end_line),
                name_pos=line_col(idx, name),
                signature=f"{name}({params.strip()})",
                language="java",
                doc="",
                container=container,
            )
        )

        caller = f"{container}.{name}" if container else name
        for call_line_no in range(idx, end_line + 1):
            code = lines[call_line_no - 1].strip() if 1 <= call_line_no <= len(lines) else ""
            for call_match in call_pattern.finditer(code):
                callee = call_match.group(1)
                if callee in java_keywords or callee == name:
                    continue
                calls.append((caller, callee, call_line_no, code))

    def inside_method(line_no: int) -> bool:
        return any(start <= line_no <= end for start, end in method_ranges)

    for idx, line in enumerate(lines, start=1):
        if inside_method(idx) or "(" in line:
            continue
        field_match = field_pattern.match(line)
        if not field_match:
            continue
        name = field_match.group(1)
        container = container_for(idx)
        symbols.append(
            SymbolItem(
                name=name,
                type="variable",
                file=file_rel,
                range=RangeInfo(idx, idx),
                name_pos=line_col(idx, name),
                signature=name,
                language="java",
                doc="",
                container=container,
            )
        )

    return symbols, calls, deps
