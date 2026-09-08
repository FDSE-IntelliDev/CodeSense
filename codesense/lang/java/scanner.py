"""Scanning Java declarations: annotations and modifiers in one traversal.

Both hang off the same ``modifiers`` node, so a single walk of the AST is
enough -- two extractors each walking once would be wasteful and would drift
apart on the question of which nodes count as declarations.

Modifiers are **language-level facts**, more precise than any keyword: asked
for "asynchronous disk-writing functions", `async` / `synchronized` /
`volatile` are certain, whereas whether the name contains "async" is a
guess.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence

from codesense.lang.base import AnnotationUse, Declaration, Invocation, ReferenceUse, ScanResult

__all__ = [
    "Declaration",
    "Invocation",
    "JavaDeclarationScanner",
]

#: Declarations carrying a ``modifiers`` child. Annotations and modifiers
#: both hang off it.
_DECLARATIONS: dict[str, str] = {
    "class_declaration": "class",
    "interface_declaration": "interface",
    "enum_declaration": "enum",
    "record_declaration": "record",
    "annotation_type_declaration": "annotation_type",
    "method_declaration": "method",
    "constructor_declaration": "constructor",
    "field_declaration": "field",
    "formal_parameter": "parameter",
    "enum_constant": "enum_constant",
}

_ANNOTATION_NODES = frozenset({"marker_annotation", "annotation"})

#: Declarations that become containers -- things nested inside them carry
#: their name.
_TYPE_KINDS = frozenset({"class", "interface", "enum", "record", "annotation_type"})

_DOC_PREFIX = "/**"

_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

#: Declarations with a body, worth collecting calls from.
_CALLABLE_KINDS = frozenset({"method", "constructor"})

#: Java's modifier keywords. tree-sitter makes them node types in their own
#: right, so "a modifiers child that is not an annotation" is a modifier --
#: but they are still listed explicitly, so a grammar version change cannot
#: silently let something else through.
JAVA_MODIFIERS = frozenset(
    {
        "public",
        "protected",
        "private",
        "static",
        "final",
        "abstract",
        "synchronized",
        "native",
        "transient",
        "volatile",
        "strictfp",
        "default",
        "sealed",
        "non-sealed",
    }
)


class JavaDeclarationScanner:
    """A tree-sitter based scan of Java declarations.

    The parser is injected through the constructor -- loading a grammar is an
    external dependency and should not happen inside this class.
    """

    def __init__(self, parser: object) -> None:
        self._parser = parser

    @classmethod
    def for_java(cls) -> JavaDeclarationScanner:
        """Convenience constructor. The import is deliberately local: anyone
        not using this need not install tree-sitter."""
        from tree_sitter_languages import get_parser

        return cls(get_parser("java"))

    def scan(self, source: str) -> ScanResult:
        data = source.encode("utf-8")
        tree = self._parser.parse(data)  # type: ignore[attr-defined]
        root = tree.root_node
        package = _package_name(root, data)
        return ScanResult(
            declarations=tuple(self._walk(root, data, package=package)),
            references=_reference_uses(root, data),
        )

    def annotations(self, source: str) -> list[AnnotationUse]:
        """Convenience entry point when only annotations are wanted."""
        return [
            use for declaration in self.scan(source).declarations for use in declaration.annotations
        ]

    def _walk(
        self, node: object, data: bytes, container: str = "", package: str = ""
    ) -> Iterator[Declaration]:
        kind = _DECLARATIONS.get(node.type)  # type: ignore[attr-defined]
        inner = container
        if kind is not None:
            declaration = self._declaration(node, kind, data, container, package)
            yield declaration
            if kind in _TYPE_KINDS and declaration.name:
                # Nested classes carry the outer name: `Outer.Inner`, not a bare `Inner`
                inner = f"{container}.{declaration.name}" if container else declaration.name
        for child in node.children:  # type: ignore[attr-defined]
            yield from self._walk(child, data, inner, package)

    def _declaration(
        self, node: object, kind: str, data: bytes, container: str, package: str
    ) -> Declaration:
        name = _text(node.child_by_field_name("name"), data) or _declared_name(node, data)  # type: ignore[attr-defined]
        structural_name = f"{container}.{name}" if container else name
        qualified_name = f"{package}.{structural_name}" if package else structural_name
        modifiers: set[str] = set()
        uses: list[AnnotationUse] = []
        modifier_node = _child_of_type(node, "modifiers")
        if modifier_node is not None:
            for child in modifier_node.children:  # type: ignore[attr-defined]
                if child.type in _ANNOTATION_NODES:
                    use = _annotation_use(child, kind, name, data)
                    if use is not None:
                        uses.append(use)
                elif child.type in JAVA_MODIFIERS:
                    modifiers.add(child.type)
        return Declaration(
            kind=kind,
            name=name,
            line=node.start_point[0] + 1,  # type: ignore[attr-defined]
            column=node.start_point[1],  # type: ignore[attr-defined]
            end_line=node.end_point[0] + 1,  # type: ignore[attr-defined]
            end_column=node.end_point[1],  # type: ignore[attr-defined]
            container=container,
            qualified_name=qualified_name,
            signature=_signature(node, kind, data),
            doc=_javadoc(node, data),
            modifiers=frozenset(modifiers),
            annotations=tuple(uses),
            calls=_invocations(node, data) if kind in _CALLABLE_KINDS else (),
            local_types=_local_types(node, data) if kind in _CALLABLE_KINDS else (),
            supertypes=_supertypes(node, data) if kind in _TYPE_KINDS else (),
        )


def _package_name(root: object, data: bytes) -> str:
    """Read the Java package from the already parsed compilation unit."""
    package = next(
        (
            child
            for child in root.children  # type: ignore[attr-defined]
            if child.type == "package_declaration"
        ),
        None,
    )
    if package is None:
        return ""
    target = next(
        (
            child
            for child in package.children  # type: ignore[attr-defined]
            if child.type in {"identifier", "scoped_identifier"}
        ),
        None,
    )
    return _text(target, data)


def _invocations(node: object, data: bytes) -> tuple[Invocation, ...]:
    """Call sites in a method body, deduplicated in order.

    Searches only **this declaration's own body**, stopping at a nested
    method declaration; otherwise calls inside an anonymous class would be
    attributed to the enclosing method.
    """
    found: dict[Invocation, None] = {}
    for current in _own_body(node):
        if current.type != "method_invocation":
            continue
        name = _text(current.child_by_field_name("name"), data)
        if name:
            receiver = _text(current.child_by_field_name("object"), data)
            found.setdefault(
                Invocation(
                    name=name,
                    receiver=receiver,
                    line=current.start_point[0] + 1,
                ),
                None,
            )
    return tuple(found)


def _reference_uses(root: object, data: bytes) -> tuple[ReferenceUse, ...]:
    """Collect conservative type and import facts in source traversal order.

    These are deliberately unresolved syntax observations. The graph builder
    owns project-wide name resolution, where all declarations are available.
    """
    found: dict[tuple[str, int, int, str, str], ReferenceUse] = {}

    def add(use: ReferenceUse | None) -> None:
        if use is None or not use.name:
            return
        key = (use.name, use.line, use.column, use.relation, use.qualified_name)
        found.setdefault(key, use)

    def visit(node: object) -> None:
        node_type = node.type  # type: ignore[attr-defined]
        if node_type == "import_declaration":
            add(_import_use(node, data))
        elif node_type == "type_identifier":
            add(_type_use(node, data))
        elif node_type == "method_invocation":
            receiver = node.child_by_field_name("object")  # type: ignore[attr-defined]
            receiver_name = _text(receiver, data)
            if receiver_name.isidentifier() and receiver_name[:1].isupper():
                add(_type_use(receiver, data))
        for child in node.children:  # type: ignore[attr-defined]
            visit(child)

    visit(root)
    return tuple(found.values())


def _import_use(node: object, data: bytes) -> ReferenceUse | None:
    """Return the import target, excluding wildcard packages."""
    if any(child.type == "asterisk" for child in node.children):  # type: ignore[attr-defined]
        return None
    target = next(
        (
            child
            for child in node.children  # type: ignore[attr-defined]
            if child.type in {"identifier", "scoped_identifier"}
        ),
        None,
    )
    qualified_name = _text(target, data)
    name = qualified_name.rsplit(".", 1)[-1]
    if not name or name == "*":
        return None
    return ReferenceUse(
        name=name,
        line=target.start_point[0] + 1,  # type: ignore[union-attr]
        column=target.start_point[1],  # type: ignore[union-attr]
        relation="imports",
        target_kind="type",
        qualified_name=qualified_name,
    )


def _type_use(node: object | None, data: bytes) -> ReferenceUse | None:
    """Convert a type-shaped AST occurrence to a language-neutral use."""
    name = _text(node, data)
    if node is None or not name:
        return None
    return ReferenceUse(
        name=name,
        line=node.start_point[0] + 1,  # type: ignore[attr-defined]
        column=node.start_point[1],  # type: ignore[attr-defined]
        target_kind="type",
    )


def _local_types(node: object, data: bytes) -> tuple[tuple[str, str], ...]:
    """Declared types of parameters and local variables."""
    found: dict[str, str] = {}
    for current in _own_body(node):
        if current.type == "local_variable_declaration":
            declared = _text(current.child_by_field_name("type"), data)
            for child in current.children:
                if child.type == "variable_declarator":
                    name = _text(child.child_by_field_name("name"), data)
                    if name and declared:
                        found.setdefault(name, declared)
    parameters = _child_of_type(node, "formal_parameters")
    if parameters is not None:
        for child in parameters.children:  # type: ignore[attr-defined]
            if child.type == "formal_parameter":
                name = _text(child.child_by_field_name("name"), data)
                declared = _text(child.child_by_field_name("type"), data)
                if name and declared:
                    found.setdefault(name, declared)
    return tuple(found.items())


def _supertypes(node: object, data: bytes) -> tuple[str, ...]:
    """Type names listed in `extends` / `implements`."""
    found: list[str] = []
    for field_name in ("superclass", "interfaces"):
        child = node.child_by_field_name(field_name)  # type: ignore[attr-defined]
        if child is not None:
            found.extend(_WORD.findall(_text(child, data)))
    return tuple(dict.fromkeys(n for n in found if n and n[0].isupper()))


def _own_body(node: object) -> Iterator[object]:
    """Nodes inside this declaration's own body, stopping at a nested method
    declaration."""
    stack = list(node.children)  # type: ignore[attr-defined]
    while stack:
        current = stack.pop()
        if current.type in ("method_declaration", "constructor_declaration"):
            continue
        yield current
        stack.extend(current.children)


def _annotation_use(
    node: object, target_kind: str, target_name: str, data: bytes
) -> AnnotationUse | None:
    name = _text(node.child_by_field_name("name"), data)  # type: ignore[attr-defined]
    if not name:
        return None
    return AnnotationUse(
        name=name,
        args=_text(node.child_by_field_name("arguments"), data),  # type: ignore[attr-defined]
        target_kind=target_kind,
        target_name=target_name,
        line=node.start_point[0] + 1,  # type: ignore[attr-defined]
    )


def _child_of_type(node: object, wanted: str) -> object | None:
    """Direct children only.

    It must not descend: an anonymous class in a method body has its own
    ``modifiers``, and descending would attribute the inner annotations and
    modifiers to the outer declaration.
    """
    for child in node.children:  # type: ignore[attr-defined]
        if child.type == wanted:
            return child
    return None


def _declared_name(node: object, data: bytes) -> str:
    """A field declaration's name lives in ``variable_declarator``, not in a
    ``name`` field."""
    declarator = _child_of_type(node, "variable_declarator")
    if declarator is None:
        return ""
    return _text(declarator.child_by_field_name("name"), data)  # type: ignore[attr-defined]


def _signature(node: object, kind: str, data: bytes) -> str:
    """A method's ``(params) : return type``; for a field, its type.

    Signature only, never the body -- signature and doc are usually enough to
    decide what a piece of code does, and including source would put
    downstream token cost out of control.
    """
    if kind in ("method", "constructor"):
        params = _text(node.child_by_field_name("parameters"), data)  # type: ignore[attr-defined]
        returns = _text(node.child_by_field_name("type"), data)  # type: ignore[attr-defined]
        return f"{params} : {returns}" if returns else params
    if kind in ("field", "parameter"):
        return _text(node.child_by_field_name("type"), data)  # type: ignore[attr-defined]
    return ""


def _javadoc(node: object, data: bytes) -> str:
    """The javadoc immediately preceding a declaration.

    tree-sitter puts comments in a sibling node rather than inside the
    declaration, so this looks backwards. Non-javadoc block comments
    (`/* ... */`) do not count -- those are usually commented-out code.
    """
    previous = node.prev_sibling  # type: ignore[attr-defined]
    if previous is None or previous.type != "block_comment":
        return ""
    text = _text(previous, data)
    if not text.startswith(_DOC_PREFIX):
        return ""
    body = text[len(_DOC_PREFIX) :].removesuffix("*/")
    lines = [line.strip().lstrip("*").strip() for line in body.splitlines()]
    return " ".join(line for line in lines if line and not line.startswith("@"))


def _text(node: object | None, data: bytes) -> str:
    if node is None:
        return ""
    return data[node.start_byte : node.end_byte].decode("utf-8")  # type: ignore[attr-defined]


def modifier_terms(declaration: Declaration) -> Sequence[tuple[str, str]]:
    """The (term, field) pairs modifiers produce.

    Modifiers go into the inverted index rather than living only on
    `Element` so that the `modifier` satisfier takes the same lookup path as
    lexical matching and annotations -- otherwise it would have to scan the
    whole table to evaluate.

    ICF sorts out strong from weak automatically: nearly every symbol is
    `public`, so its discriminative power tends to zero and the floor blocks
    it; `native` and `volatile` are rare, score high, and are exactly the
    informative ones.
    """
    return [(modifier, "modifier") for modifier in sorted(declaration.modifiers)]
