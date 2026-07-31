"""从「打过分的词」确定性地构造查询规格。

模型**提议**语义结构（哪些词相关、怎么分组、组之间有没有关系），
统计**校验**它在这个代码库里成不成立（`validate`），
统计再**决定**模型问不出来的部分：

    偏好什么种类  词的 posting 落在哪些种类的符号上
    查哪些域     词的 posting 落在哪些域上
    执行顺序     `df` 让选择性在执行前可估（`planner`）

分界线是：**语义问题问模型，事实问索引。** 查询问「实体字段上的校验」，
模型给的 kinds 里偏偏没有 `field`——那不是它该答的问题。
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from codesense.ql.compile.partition import Cluster, partition
from codesense.ql.compile.spec import GraphConstraint, QuerySpec
from codesense.ql.compile.validate import validate_groups, validate_relations
from codesense.ql.context import EvalContext
from codesense.ql.fields import IndexField
from codesense.ql.satisfiers import AnnotationSatisfier, LexicalSatisfier
from codesense.ql.unit import QueryUnit, Term

__all__ = ["KIND_PREFERENCE_FLOOR", "build_spec", "infer_kinds"]

#: 某个种类要占到这个比例才值得当偏好。定低了等于没偏好，
#: 定高了会漏掉「答案确实集中在字段上」这种情形。
KIND_PREFERENCE_FLOOR = 0.25

#: 域偏好的下限，同理。
FIELD_FLOOR = 0.15


@dataclass(frozen=True, slots=True)
class ScoredTerm:
    """LLM 判定为相关的一个词。分数是它给的相关度。"""

    value: str
    score: float = 1.0


def infer_kinds(terms: Sequence[str], ctx: EvalContext) -> tuple[str, ...]:
    """从词的 posting 落点推断该偏好哪些种类的符号。

    这是「访问路径选择」的一半：查询问什么种类，看它的词命中什么种类，
    比问模型可靠——模型答的是它对查询措辞的印象，统计答的是这个索引的事实。
    """
    counts: Counter[str] = Counter()
    for term in terms:
        for posting in ctx.postings.lookup(term):
            element = ctx.symbols.get(posting.symbol_id)
            if element is not None:
                counts[element.kind] += 1
    if not counts:
        return ()
    total = sum(counts.values())
    return tuple(
        kind for kind, count in counts.most_common() if count / total >= KIND_PREFERENCE_FLOOR
    )


def infer_fields(terms: Sequence[str], ctx: EvalContext) -> tuple[IndexField, ...]:
    """推断该查哪些域。

    另一半访问路径选择。若某个词的命中几乎全在 `doc` 上，
    只查 `name` 就会整个落空。
    """
    counts: Counter[str] = Counter()
    for term in terms:
        for posting in ctx.postings.lookup(term):
            counts[str(posting.field)] += 1
    if not counts:
        return ()
    total = sum(counts.values())
    chosen = [field for field, count in counts.most_common() if count / total >= FIELD_FLOOR]
    return tuple(IndexField(field) for field in chosen)


def build_spec(
    query: str,
    terms: Sequence[ScoredTerm] | Mapping[str, float] | Sequence[str],
    ctx: EvalContext,
    *,
    concept: str = "",
    annotations: Sequence[str] = (),
    groups: Mapping[str, Sequence[str]] | None = None,
    relations: Sequence[tuple[str, str]] = (),
) -> tuple[QuerySpec, list[str]]:
    """把模型的提议组装成规格，并把校验记录一并返回。

    ``groups`` / ``relations`` 是模型的**提议**——先过统计校验，
    没通过的会被合并或丢弃，理由记在返回的第二项里。
    没给分组时退回按 posting 重叠度自动划分。
    """
    scored = _normalise(terms)
    known = [item for item in scored if ctx.postings.term_info(item.value) is not None]
    if not known:
        raise ValueError("没有一个词能在索引里查到")

    notes: list[str] = []
    values = [item.value for item in known]
    if groups:
        checked, group_notes = validate_groups(dict(groups), ctx)
        notes += group_notes
        clusters = [
            Cluster(tuple(sorted(members)), f"模型分组 {name!r}，凝聚度校验通过")
            for name, members in checked.items()
        ]
        names = list(checked)
        kept_relations, rejected = validate_relations(relations, checked, ctx)
        notes += rejected
        notes += [f"采纳关系 {r.src}→{r.dst}：{r.detail}" for r in kept_relations]
        constraints = tuple(
            GraphConstraint(src=f"u{names.index(r.src)}", dst=f"u{names.index(r.dst)}")
            for r in kept_relations
        )
    else:
        clusters = partition(values, ctx)
        constraints = ()
    weights = {item.value: item.score for item in known}
    units: list[QueryUnit] = []
    for index, cluster in enumerate(clusters):
        satisfiers: list[object] = [
            LexicalSatisfier(
                terms=tuple(Term(t, weight=weights.get(t, 1.0)) for t in cluster.terms),
                weight=0.5,
            )
        ]
        # 注解信号只挂在第一个单元上：它是独立证据，重复挂等于给它多倍权重
        if annotations and index == 0:
            satisfiers.append(AnnotationSatisfier(names=tuple(annotations)))
        units.append(
            QueryUnit(
                name=f"u{index}" if len(clusters) > 1 else "q",
                concept=cluster.reason,
                satisfiers=tuple(satisfiers),
            )
        )

    return (
        QuerySpec(
            query=query,
            units=tuple(units),
            graph=constraints if len(units) > 1 else (),
            concept=concept,
            kinds=infer_kinds(values, ctx),
        ),
        notes,
    )


def _normalise(
    terms: Sequence[ScoredTerm] | Mapping[str, float] | Sequence[str],
) -> list[ScoredTerm]:
    if isinstance(terms, Mapping):
        return [ScoredTerm(str(k), float(v)) for k, v in terms.items()]
    found: list[ScoredTerm] = []
    for item in terms:
        if isinstance(item, ScoredTerm):
            found.append(item)
        elif isinstance(item, str):
            found.append(ScoredTerm(item.lower()))
    return found
