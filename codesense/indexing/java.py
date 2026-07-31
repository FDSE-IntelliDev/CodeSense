"""Java 声明扫描：一次遍历同时取出注解与修饰符。

两者挂在同一个 ``modifiers`` 节点上，所以走一遍 AST 就够——
分成两个抽取器各扫一遍是浪费，而且容易在「哪些节点算声明」上走样。

修饰符是**语言级事实**，比任何关键词都准：查「异步的写盘函数」时，
`async` / `synchronized` / `volatile` 是确定的，而名字里有没有 "async" 是猜的。
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass

from codesense.indexing.annotations import AnnotationUse

__all__ = ["Declaration", "Invocation", "JavaDeclarationScanner"]

#: 带 ``modifiers`` 子节点的声明。注解与修饰符都挂在那上面。
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

#: 会成为容器的声明——嵌套在它们里面的东西要带上它们的名字。
_TYPE_KINDS = frozenset({"class", "interface", "enum", "record", "annotation_type"})

_DOC_PREFIX = "/**"

_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

#: 有方法体、值得收集调用的声明。
_CALLABLE_KINDS = frozenset({"method", "constructor"})

#: Java 的修饰符关键字。tree-sitter 把它们做成节点类型本身，
#: 所以「不是注解的 modifiers 子节点」就是修饰符——但仍然显式列出来，
#: 免得语法树版本变化时静默混进别的东西。
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


@dataclass(frozen=True, slots=True)
class Invocation:
    """一个调用点。

    ``receiver`` 是点号左边那段原文（`""` 表示没有接收者，即 `m()` 或 `this.m()`）。
    保留原文而不是当场解析：解析需要整个项目的类型表，那是索引构建阶段的事。
    """

    name: str
    receiver: str = ""


@dataclass(frozen=True, slots=True)
class Declaration:
    """一处声明的全部可索引信息。"""

    kind: str
    name: str
    line: int
    end_line: int = 0
    container: str = ""
    signature: str = ""
    doc: str = ""
    modifiers: frozenset[str] = frozenset()
    annotations: tuple[AnnotationUse, ...] = ()

    #: 这个声明体内的调用点。带**接收者表达式**，让下游能按类型收窄——
    #: Java 里绝大多数项目内调用是 `this.m()` / `field.m()` / `local.m()`，
    #: 而字段和局部变量的声明类型就在 AST 里，不需要类型检查器。
    calls: tuple[Invocation, ...] = ()

    #: 本声明内可见的 `变量名 → 声明类型`：字段、参数、局部变量。
    #: 用来把 `receiver.m()` 里的 receiver 解析成类型。
    local_types: tuple[tuple[str, str], ...] = ()

    #: 这个类型继承/实现了谁。查方法时要沿着它往上找。
    supertypes: tuple[str, ...] = ()


class JavaDeclarationScanner:
    """基于 tree-sitter 的 Java 声明扫描。

    parser 从构造函数注入——加载语法是外部依赖，不该在这个类里发生。
    """

    def __init__(self, parser: object) -> None:
        self._parser = parser

    @classmethod
    def for_java(cls) -> JavaDeclarationScanner:
        """便利构造。在这里 import 是刻意的：不用它的人不必装 tree-sitter。"""
        from tree_sitter_languages import get_parser

        return cls(get_parser("java"))

    def scan(self, source: str) -> list[Declaration]:
        data = source.encode("utf-8")
        tree = self._parser.parse(data)  # type: ignore[attr-defined]
        return list(self._walk(tree.root_node, data))

    def annotations(self, source: str) -> list[AnnotationUse]:
        """只要注解时的便利入口。"""
        return [use for declaration in self.scan(source) for use in declaration.annotations]

    def _walk(self, node: object, data: bytes, container: str = "") -> Iterator[Declaration]:
        kind = _DECLARATIONS.get(node.type)  # type: ignore[attr-defined]
        inner = container
        if kind is not None:
            declaration = self._declaration(node, kind, data, container)
            yield declaration
            if kind in _TYPE_KINDS and declaration.name:
                # 嵌套类要带上外层：`Outer.Inner` 而不是光秃秃的 `Inner`
                inner = f"{container}.{declaration.name}" if container else declaration.name
        for child in node.children:  # type: ignore[attr-defined]
            yield from self._walk(child, data, inner)

    def _declaration(self, node: object, kind: str, data: bytes, container: str) -> Declaration:
        name = _text(node.child_by_field_name("name"), data) or _declared_name(node, data)  # type: ignore[attr-defined]
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
            end_line=node.end_point[0] + 1,  # type: ignore[attr-defined]
            container=container,
            signature=_signature(node, kind, data),
            doc=_javadoc(node, data),
            modifiers=frozenset(modifiers),
            annotations=tuple(uses),
            calls=_invocations(node, data) if kind in _CALLABLE_KINDS else (),
            local_types=_local_types(node, data) if kind in _CALLABLE_KINDS else (),
            supertypes=_supertypes(node, data) if kind in _TYPE_KINDS else (),
        )


def _invocations(node: object, data: bytes) -> tuple[Invocation, ...]:
    """方法体里的调用点，保序去重。

    只在**本声明自己的体**里找：碰到嵌套的方法声明就停，
    否则匿名类里的调用会被算到外层方法头上。
    """
    found: dict[Invocation, None] = {}
    for current in _own_body(node):
        if current.type != "method_invocation":
            continue
        name = _text(current.child_by_field_name("name"), data)
        if name:
            receiver = _text(current.child_by_field_name("object"), data)
            found.setdefault(Invocation(name=name, receiver=receiver), None)
    return tuple(found)


def _local_types(node: object, data: bytes) -> tuple[tuple[str, str], ...]:
    """参数与局部变量的声明类型。"""
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
    """`extends` / `implements` 里列的类型名。"""
    found: list[str] = []
    for field_name in ("superclass", "interfaces"):
        child = node.child_by_field_name(field_name)  # type: ignore[attr-defined]
        if child is not None:
            found.extend(_WORD.findall(_text(child, data)))
    return tuple(dict.fromkeys(n for n in found if n and n[0].isupper()))


def _own_body(node: object) -> Iterator[object]:
    """本声明自己的体内的节点——碰到嵌套的方法声明就不再往下。"""
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
    """只看直接子节点。

    不能往下钻：方法体里的匿名类有自己的 ``modifiers``，
    钻进去就会把内层的注解和修饰符算到外层声明头上。
    """
    for child in node.children:  # type: ignore[attr-defined]
        if child.type == wanted:
            return child
    return None


def _declared_name(node: object, data: bytes) -> str:
    """字段声明的名字在 ``variable_declarator`` 里，不在 ``name`` 字段上。"""
    declarator = _child_of_type(node, "variable_declarator")
    if declarator is None:
        return ""
    return _text(declarator.child_by_field_name("name"), data)  # type: ignore[attr-defined]


def _signature(node: object, kind: str, data: bytes) -> str:
    """方法签名 ``(参数) : 返回类型``；字段则记类型。

    只取签名不取方法体——判定「这段代码在做什么」时签名和文档通常就够，
    塞进源码只会让下游的 token 成本失控。
    """
    if kind in ("method", "constructor"):
        params = _text(node.child_by_field_name("parameters"), data)  # type: ignore[attr-defined]
        returns = _text(node.child_by_field_name("type"), data)  # type: ignore[attr-defined]
        return f"{params} : {returns}" if returns else params
    if kind in ("field", "parameter"):
        return _text(node.child_by_field_name("type"), data)  # type: ignore[attr-defined]
    return ""


def _javadoc(node: object, data: bytes) -> str:
    """紧邻声明之前的 javadoc。

    tree-sitter 把注释放在声明的兄弟节点上，不在声明内部，所以要往前看。
    非 javadoc 的块注释（`/* ... */`）不算——那通常是被注释掉的代码。
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
    """修饰符产生的 (term, field) 对。

    修饰符进倒排表而不是只挂在 `Element` 上，是为了让 `modifier` satisfier
    和词法、注解走同一条查表路径——否则它就得扫全表才能求值。

    ICF 会自动处理强弱：`public` 几乎所有符号都有，区分度趋零会被下限挡掉；
    `native` / `volatile` 罕见，ICF 高，正是有信息的那些。
    """
    return [(modifier, "modifier") for modifier in sorted(declaration.modifiers)]
