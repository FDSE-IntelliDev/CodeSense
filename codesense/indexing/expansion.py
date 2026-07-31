"""把索引构建产物装配成 QL 认识的扩展表。

这是 indexing 与 ql 之间的桥。依赖方向单向：indexing 认识 ql 的数据类型，
ql 不认识 indexing——所以这个函数放在这边而不是那边。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from codesense.indexing.meta_annotations import meta_expansion_table
from codesense.ql.store import Expansion, InMemoryExpansionTable

__all__ = ["build_expansion_table"]


def build_expansion_table(
    *,
    lexical: Mapping[str, Sequence[tuple[str, float, str]]] | None = None,
    include_meta: bool = True,
) -> InMemoryExpansionTable:
    """建扩展表。

    ``lexical`` 是词法/向量那条链的产物（规范词 → 项目实际写法），
    ``include_meta`` 把框架声明的元注解关系并进来。

    两者键空间不重叠——元注解的键都带 ``@``——所以直接合并即可，
    不需要考虑冲突。真出现同键，以 ``lexical`` 为准并保留两边条目：
    元注解是事实，词法是估计，让打分去区分而不是在这里丢弃。
    """
    table: dict[str, list[Expansion]] = {}
    if include_meta:
        for key, entries in meta_expansion_table().items():
            table[key] = [Expansion(target, score, reason) for target, score, reason in entries]
    for key, entries in (lexical or {}).items():
        table.setdefault(key, []).extend(
            Expansion(target, score, reason) for target, score, reason in entries
        )
    return InMemoryExpansionTable(table)
