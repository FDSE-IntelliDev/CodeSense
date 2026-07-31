"""校验模型的结构提议是否在**这个代码库里**成立。

分工是三段的，不是二选一：

    模型提议   查询语义里有没有「A 相关的代码调用 B 相关的代码」这层意思
    统计校验   这层关系在这个代码库里成不成立
    统计参数化 从哪一侧出发、走几跳、代价多少

前面走过两个极端都不对：让模型决定一切（R@100 21%），
或者不让它碰结构（47%，且完全用不上图）。**提议是语义问题，校验是统计问题。**

实测判别力（netty，42221 符号、12 万条边）：

    真实关系   池↔内存块 4.75x   处理器↔流水线 2.20x   零拷贝↔文件通道 1.32x
    编造关系   零拷贝↔JSON 0.00x   池↔WebSocket 0.00x   DNS↔压缩 0.03x
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from codesense.ql.compile.partition import OVERLAP_FLOOR
from codesense.ql.context import EvalContext

__all__ = ["LIFT_FLOOR", "Relation", "relation_lift", "validate_groups", "validate_relations"]

#: 跨边数要达到随机期望的几倍，才认这个关系。
#:
#: 实测真实关系最低 1.32x、编造的最高 0.03x，分界很宽。取 1.2 偏保守：
#: 图约束是加权不是过滤，认错一个的代价有限，漏掉一个的代价更大。
LIFT_FLOOR = 1.2

#: 采样多少个源节点来数跨边。全量数在大项目上太贵，而判别只需要量级对。
SAMPLE_CAP = 400


@dataclass(frozen=True, slots=True)
class Relation:
    """一个校验过的关系。"""

    src: str
    dst: str
    lift: float
    detail: str = ""


def relation_lift(
    src_terms: Sequence[str], dst_terms: Sequence[str], ctx: EvalContext
) -> tuple[float, str]:
    """两组词命中的符号之间，边的密度是随机情况的几倍。

    随机基线取 ``|A|·|B|·2E/N²``——把图当成同样边数的随机图。
    这个基线粗糙，但要区分「4.75 倍」和「0 倍」绰绰有余。
    """
    total = ctx.symbols.count()
    if total < 2:
        return 0.0, "符号太少"
    left = {p.symbol_id for term in src_terms for p in ctx.postings.lookup(term)}
    right = {p.symbol_id for term in dst_terms for p in ctx.postings.lookup(term)}
    if not left or not right:
        return 0.0, "有一侧没有命中"

    sampled = sorted(left)[:SAMPLE_CAP]
    scale = len(left) / len(sampled)
    crossing = scale * sum(
        1 for node in sampled for edge in ctx.edges.out_edges(node) if edge.target_id in right
    )
    edges = sum(ctx.edges.degree(node) for node in sampled) * scale
    expected = len(left) * len(right) * edges / (total * total) if total else 0.0
    if expected <= 0:
        return 0.0, "图上没有边"
    lift = crossing / expected
    return lift, f"跨边 {crossing:.0f} vs 随机期望 {expected:.0f}（{lift:.2f}x）"


def validate_relations(
    proposed: Sequence[tuple[str, str]],
    groups: dict[str, Sequence[str]],
    ctx: EvalContext,
    *,
    floor: float = LIFT_FLOOR,
) -> tuple[list[Relation], list[str]]:
    """留下在这个代码库里真的成立的关系。

    返回 (通过的, 被否掉的理由)。理由要留着——用户得知道
    模型提的关系为什么没被采纳。
    """
    kept: list[Relation] = []
    rejected: list[str] = []
    for src, dst in proposed:
        if src not in groups or dst not in groups or src == dst:
            rejected.append(f"{src}→{dst}：引用了不存在的组")
            continue
        lift, detail = relation_lift(groups[src], groups[dst], ctx)
        if lift >= floor:
            kept.append(Relation(src=src, dst=dst, lift=lift, detail=detail))
        else:
            rejected.append(f"{src}→{dst}：{detail}，达不到 {floor}x，这个关系在本项目里不成立")
    return kept, rejected


def validate_groups(
    proposed: dict[str, Sequence[str]], ctx: EvalContext, *, floor: float = OVERLAP_FLOOR
) -> tuple[dict[str, list[str]], list[str]]:
    """校验模型的分组：组内的词真的落在同一批符号上吗？

    模型按**语义**分组（「零拷贝」「用户态内存」「性能」），但语义相近
    不代表落点相同。落点不同的组合在一起，等权相加就把答案摊薄了——
    实测这正是 R@100 从 65% 掉到 21% 的主因。

    组内凝聚度不够的，就把它并回去：**宁可一个宽单元，
    不要几个把答案摊薄的窄单元。**
    """
    usable = {
        name: [t for t in terms if ctx.postings.term_info(t) is not None]
        for name, terms in proposed.items()
    }
    usable = {name: terms for name, terms in usable.items() if terms}
    if len(usable) < 2:
        return {name: list(terms) for name, terms in usable.items()}, []

    kept: dict[str, list[str]] = {}
    notes: list[str] = []
    loose: list[str] = []
    for name, terms in usable.items():
        cohesion = _cohesion(terms, ctx)
        if len(terms) >= 2 and cohesion >= floor:
            kept[name] = terms
        else:
            loose.extend(terms)
            notes.append(
                f"组 {name!r} 凝聚度 {cohesion:.3f} 不足，并回主组——组内的词并不落在同一批符号上"
            )

    if not kept:
        return {"q": sorted({t for terms in usable.values() for t in terms})}, [
            "所有分组的凝聚度都不足，合成一个单元"
        ]
    if loose:
        biggest = max(kept, key=lambda name: len(kept[name]))
        kept[biggest] = sorted({*kept[biggest], *loose})
    return kept, notes


def _cohesion(terms: Sequence[str], ctx: EvalContext) -> float:
    """组内两两 Jaccard 的均值。"""
    postings = [{p.symbol_id for p in ctx.postings.lookup(term)} for term in terms]
    pairs = [
        (postings[i], postings[j])
        for i in range(len(postings))
        for j in range(i + 1, len(postings))
    ]
    if not pairs:
        return 0.0
    return sum(len(left & right) / max(len(left | right), 1) for left, right in pairs) / len(pairs)
