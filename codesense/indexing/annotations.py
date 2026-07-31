"""从 Java 源码里抽注解。

注解是 Java 项目里信噪比最高的信号——`@RestController` 直接说明这是
HTTP 入口，比任何关键词都准——而且抽取几乎免费，因为解析器本来就在跑
tree-sitter，节点就在 AST 上。

**一条注解是三样东西**（``docs/design/09-grounding.md`` 第八节）：

    名字      → `annotation` 域的 posting
    被标注关系 → `annotated_by` 边
    参数      → `annotation_arg` 域的 posting

只索引名字会丢掉后两者。`@PreAuthorize("@ss.hasPerm('sys:user:query')")`
的权限串、`@Schema(description=…)` 的自然语言描述都在参数里。
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass

__all__ = ["AnnotationUse", "JavaAnnotationExtractor", "arg_tokens", "posting_terms"]

#: 带 `modifiers` 子节点的声明。注解就挂在 `modifiers` 上。
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

_ANNOTATION_NODES = ("marker_annotation", "annotation")

#: 参数里切出可检索片段用的分隔符：引号、括号、点、冒号、逗号、等号……
#: 保留字母数字与下划线，其余一律当分隔符。
_ARG_SPLIT = re.compile(r"[^A-Za-z0-9_]+")


@dataclass(frozen=True, slots=True)
class AnnotationUse:
    """一处注解的使用。

    ``target_kind`` 记它标在哪——标在方法上的 `@Transactional` 和标在类上的
    含义不同，查询时要能区分。
    """

    name: str
    args: str
    target_kind: str
    target_name: str
    line: int

    @property
    def at_name(self) -> str:
        """带 ``@`` 的写法，用作扩展表的键（元注解展开在那一层做）。"""
        return f"@{self.name}"


def arg_tokens(args: str) -> tuple[str, ...]:
    """把注解参数切成可检索的片段。

    `"@ss.hasPerm('sys:user:query')"` → ``ss hasPerm sys user query``。
    保序去重，因为顺序本身有信息（`sys:user:query` 是层级）。
    """
    seen: dict[str, None] = {}
    for token in _ARG_SPLIT.split(args):
        if token and not token.isdigit():
            seen.setdefault(token.lower(), None)
    return tuple(seen)


def posting_terms(
    use: AnnotationUse, split: Callable[[str], Sequence[str]]
) -> list[tuple[str, str]]:
    """一处注解使用产生的 (term, field) 对。

    三类 term，对应设计文档说的「注解是三样东西」里的两样
    （第三样是 `annotated_by` 边，由边构建那一步负责）：

        @Cacheable        整体名字 → annotation      供元注解展开精确命中
        cache / able      切分单元 → annotation      让 @AppCache 也能命中 cache 单元
        user / query      参数片段 → annotation_arg  权限串、URL、描述都在这

    ``split`` 注入进来而不是在这里 import——切分器是第三方依赖
    （srctoolkit/Ronin），而这个函数本身是纯的、可测的。
    """
    terms: list[tuple[str, str]] = [(use.at_name, "annotation")]
    terms.extend((unit, "annotation") for unit in split(use.name))
    terms.extend((token, "annotation_arg") for token in arg_tokens(use.args))
    seen: dict[tuple[str, str], None] = {}
    for item in terms:
        seen.setdefault(item, None)
    return list(seen)


class JavaAnnotationExtractor:
    """基于 tree-sitter 的 Java 注解抽取。

    parser 从构造函数注入，因为加载 tree-sitter 语法是**外部依赖**，
    不该在这个类里发生——它只负责遍历与提取。
    """

    def __init__(self, parser: object) -> None:
        self._parser = parser

    @classmethod
    def for_java(cls) -> JavaAnnotationExtractor:
        """便利构造。在这里 import 是刻意的：让不用它的人不必装 tree-sitter。"""
        from tree_sitter_languages import get_parser

        return cls(get_parser("java"))

    def extract(self, source: str) -> list[AnnotationUse]:
        """从一份源码里抽出全部注解使用。"""
        data = source.encode("utf-8")
        tree = self._parser.parse(data)  # type: ignore[attr-defined]
        return list(self._walk(tree.root_node, data))

    def _walk(self, node: object, data: bytes) -> Iterator[AnnotationUse]:
        kind = _DECLARATIONS.get(node.type)  # type: ignore[attr-defined]
        if kind is not None:
            yield from self._at(node, kind, data)
        for child in node.children:  # type: ignore[attr-defined]
            yield from self._walk(child, data)

    def _at(self, declaration: object, kind: str, data: bytes) -> Iterator[AnnotationUse]:
        target = _text(declaration.child_by_field_name("name"), data)  # type: ignore[attr-defined]
        if not target:
            target = _declared_name(declaration, data)
        for node in _annotation_nodes(declaration):
            name = _text(node.child_by_field_name("name"), data)
            if not name:
                continue
            yield AnnotationUse(
                name=name,
                args=_text(node.child_by_field_name("arguments"), data),
                target_kind=kind,
                target_name=target,
                line=node.start_point[0] + 1,
            )


def _annotation_nodes(declaration: object) -> Sequence[object]:
    """只取本声明**自己**的注解。

    不能整棵子树扫：方法体里可能有匿名类，它的注解属于那个类而不是这个方法。
    注解只出现在声明的 ``modifiers`` 子节点里。
    """
    for child in declaration.children:  # type: ignore[attr-defined]
        if child.type == "modifiers":
            return [c for c in child.children if c.type in _ANNOTATION_NODES]
    return []


def _declared_name(declaration: object, data: bytes) -> str:
    """字段声明的名字在 ``variable_declarator`` 里，不在 ``name`` 字段上。"""
    for child in declaration.children:  # type: ignore[attr-defined]
        if child.type == "variable_declarator":
            return _text(child.child_by_field_name("name"), data)
    return ""


def _text(node: object | None, data: bytes) -> str:
    if node is None:
        return ""
    return data[node.start_byte : node.end_byte].decode("utf-8")  # type: ignore[attr-defined]
