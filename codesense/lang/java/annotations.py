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

本模块只有数据类型与纯函数——扫 AST 的部分在 `codesense.indexing.java`，
因为注解和修饰符挂在同一个节点上，一次遍历取完。
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass

__all__ = ["AnnotationUse", "arg_tokens", "posting_terms"]

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
