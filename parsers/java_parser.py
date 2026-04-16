from typing import List, Tuple

from .base import SymbolItem, RangeInfo, DependencyItem

try:
    import javalang
except Exception:
    javalang = None


def parse_java(file_rel: str, source: str):
    symbols: List[SymbolItem] = []
    calls: List[Tuple[str, str, int, str]] = []
    deps: List[DependencyItem] = []
    lines = source.splitlines()

    if javalang is None:
        return symbols, calls, deps

    try:
        tree = javalang.parse.parse(source)
    except Exception:
        return symbols, calls, deps

    package_name = tree.package.name if tree.package else ""

    for imp in getattr(tree, "imports", []) or []:
        target = imp.path.replace(".", "/") if getattr(imp, "path", None) else ""
        if target:
            deps.append(DependencyItem(file_rel, target, "import"))

    for t in tree.types or []:
        if isinstance(t, javalang.tree.ClassDeclaration):
            cname = t.name
            c_line = getattr(t, "position", None).line if getattr(t, "position", None) else 1
            symbols.append(
                SymbolItem(
                    name=cname,
                    type="class",
                    file=file_rel,
                    range=RangeInfo(c_line, len(lines)),
                    signature=f"class {cname}",
                    language="java",
                    doc="",
                    container=package_name,
                )
            )

            for field in t.fields or []:
                f_line = getattr(field, "position", None).line if getattr(field, "position", None) else c_line
                for decl in field.declarators or []:
                    symbols.append(
                        SymbolItem(
                            name=decl.name,
                            type="variable",
                            file=file_rel,
                            range=RangeInfo(f_line, f_line),
                            signature=decl.name,
                            language="java",
                            doc="",
                            container=cname,
                        )
                    )

            for method in t.methods or []:
                m_line = getattr(method, "position", None).line if getattr(method, "position", None) else c_line
                params = []
                for p in method.parameters or []:
                    ptype = getattr(getattr(p, "type", None), "name", "Object")
                    params.append(f"{ptype} {p.name}")
                symbols.append(
                    SymbolItem(
                        name=method.name,
                        type="method",
                        file=file_rel,
                        range=RangeInfo(m_line, len(lines)),
                        signature=f"{method.name}({', '.join(params)})",
                        language="java",
                        doc="",
                        container=cname,
                    )
                )

                caller = f"{cname}.{method.name}"
                for _, inv in method.filter(javalang.tree.MethodInvocation):
                    callee = inv.member or ""
                    i_line = getattr(inv, "position", None).line if getattr(inv, "position", None) else m_line
                    code = lines[i_line - 1].strip() if 1 <= i_line <= len(lines) else ""
                    calls.append((caller, callee, i_line, code))

            for ctor in t.constructors or []:
                k_line = getattr(ctor, "position", None).line if getattr(ctor, "position", None) else c_line
                symbols.append(
                    SymbolItem(
                        name=ctor.name,
                        type="method",
                        file=file_rel,
                        range=RangeInfo(k_line, len(lines)),
                        signature=f"{ctor.name}(...)",
                        language="java",
                        doc="",
                        container=cname,
                    )
                )

                caller = f"{cname}.{ctor.name}"
                for _, inv in ctor.filter(javalang.tree.MethodInvocation):
                    callee = inv.member or ""
                    i_line = getattr(inv, "position", None).line if getattr(inv, "position", None) else k_line
                    code = lines[i_line - 1].strip() if 1 <= i_line <= len(lines) else ""
                    calls.append((caller, callee, i_line, code))

    return symbols, calls, deps
