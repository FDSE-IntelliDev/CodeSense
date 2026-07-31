"""按统计信息决定词该分几个单元——**不问 LLM**。

这是 MySQL 式分工的核心一条：优化器不问用户怎么 join，它查统计信息。
同样地，「这些词该分成几个单元」是个统计问题，不是语义问题——
判据是**这些词是否落在同一批符号上**，而这个答案在倒排索引里。

实测证据（``docs/design/06-script-and-execution.md``）：让 LLM 按语义把
netty 的零拷贝查询拆成「零拷贝 / 用户态内存 / 性能」三个单元，R@100 从
65% 掉到 21%。而统计说这些词两两 Jaccard 平均只有 0.011——
它们描述的是同一件事的不同侧面，本来就该是一个单元。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from codesense.ql.context import EvalContext

__all__ = ["OVERLAP_FLOOR", "Cluster", "partition"]

#: 两个词算「属于同一个单元」的 Jaccard 下限。
#:
#: 定得高是刻意的：**拆错的代价远大于不拆**。不拆最坏是一个宽单元
#: （仍能召回，只是排序差些），拆错会把答案分到不同单元再被权重摊薄。
OVERLAP_FLOOR = 0.08

#: 一个词的 posting 超过这个比例就不参与聚类——它和谁都重叠，
#: 会把本该分开的簇粘成一坨。
HUB_RATIO = 0.25

#: 最多分几个单元。再多就说明聚类没聚出结构，不如不拆。
MAX_UNITS = 3


@dataclass(frozen=True, slots=True)
class Cluster:
    """一组该放进同一个单元的词。"""

    terms: tuple[str, ...]
    reason: str = ""


def partition(terms: Sequence[str], ctx: EvalContext) -> list[Cluster]:
    """按 posting 重叠度把词分组。

    **默认不拆**：只有当聚类真的分出了结构（多于一个簇，且每个簇都不是
    孤零零一个词）才拆，否则返回单个簇。
    """
    usable = [term for term in terms if ctx.postings.term_info(term) is not None]
    if len(usable) < 4:
        return [Cluster(tuple(usable), "词太少，不拆")]

    total = max(ctx.symbols.count(), 1)
    postings = {term: {p.symbol_id for p in ctx.postings.lookup(term)} for term in usable}
    hubs = {term for term, ids in postings.items() if len(ids) > total * HUB_RATIO}

    groups = _connected([term for term in usable if term not in hubs], postings, OVERLAP_FLOOR)
    solid = [group for group in groups if len(group) >= 2]
    if len(solid) < 2 or len(solid) > MAX_UNITS:
        return [
            Cluster(
                tuple(usable),
                f"聚类没分出结构（{len(solid)} 个成形的簇），不拆——拆错比不拆贵",
            )
        ]

    loose = [term for group in groups if len(group) < 2 for term in group] + sorted(hubs)
    clusters = [Cluster(tuple(sorted(group)), f"{len(group)} 个词互相重叠") for group in solid]
    if loose:
        # 落单的词并进最大的簇：单独成一个单元只会被权重摊薄
        biggest = max(range(len(clusters)), key=lambda i: len(clusters[i].terms))
        merged = tuple(sorted({*clusters[biggest].terms, *loose}))
        clusters[biggest] = Cluster(merged, clusters[biggest].reason + "，并入落单的词")
    return clusters


def _connected(
    terms: Sequence[str], postings: dict[str, set[int]], floor: float
) -> list[list[str]]:
    """按「重叠度超过下限」连边，取连通分量。

    用连通分量而不是 k-means 之类：簇的**数量**本身就是要推断的东西，
    不该由参数给定。
    """
    parent = {term: term for term in terms}

    def find(term: str) -> str:
        while parent[term] != term:
            parent[term] = parent[parent[term]]
            term = parent[term]
        return term

    for index, left in enumerate(terms):
        for right in terms[index + 1 :]:
            if _jaccard(postings[left], postings[right]) >= floor:
                parent[find(left)] = find(right)

    groups: dict[str, list[str]] = {}
    for term in terms:
        groups.setdefault(find(term), []).append(term)
    return list(groups.values())


def _jaccard(left: set[int], right: set[int]) -> float:
    union = len(left | right)
    return len(left & right) / union if union else 0.0
