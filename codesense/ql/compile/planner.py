"""把查询规格排成执行计划。

**这是编译器里唯一真正做优化的地方。** 算子集合是固定的，
决定快慢的是顺序——而顺序能优化，是因为倒排索引的 `df` 让我们
在执行前就能估出每个单元会命中多少符号（`cost` 模块）。

设计文档（[01](../../../docs/design/01-motivation.md)）说的
「顺序由查询决定，不是写死在代码里」，落到实处就是这个模块。
"""

from __future__ import annotations

from dataclasses import dataclass

from codesense.ql.compile.cost import estimate_unit
from codesense.ql.compile.plan import Boost, EvalUnit, Intent, Narrow, Plan, Step
from codesense.ql.compile.spec import QuerySpec
from codesense.ql.context import EvalContext
from codesense.ql.satisfiers.lexical import AnnotationSatisfier, LexicalSatisfier, ModifierSatisfier
from codesense.ql.unit import QueryUnit

__all__ = ["USELESS_RATIO", "plan"]

#: 单元覆盖率超过这个比例才丢弃。
#:
#: 定得很高是**吃过亏的**：原先设 0.5，在 142 个符号的 petclinic 上
#: 把唯一含答案的单元丢掉了（10 个词的并集轻易过半），只剩一个 2 词的
#: 无关单元。这和当初 ICF 下限犯的是同一个错——**把调用方明确要的东西
#: 排除，而不是降权**。丢一个单元省的是一遍倒排扫描，代价却是答案没了。
#:
#: 现在只丢「几乎命中全部符号」的单元，宽窄之分交给 `_specificity` 加权。
USELESS_RATIO = 0.9

#: 交给 `intent` 的候选上限。它比查表贵几千倍，输入必须先压住。
INTENT_INPUT_CAP = 60

#: 图约束「从小的一侧出发」的判定倍数。两侧规模接近时方向无所谓，
#: 差出这个倍数才值得为它调整方向。
ASYMMETRY = 3

#: 单元权重的下限。再宽泛的单元也还有一点信息，不该归零。
MIN_UNIT_WEIGHT = 0.15


def _specificity(rows: int, total: int) -> float:
    """单元的特异性：覆盖面越大越不值钱。

    这是 ICF 在**单元**层面的同一个道理。不加这一步，一个覆盖 38% 代码库的
    单元会和只覆盖 1% 的单元等权相加——实测在 netty 上正是这个把答案挤出
    了前 60 名：真正的 gold 只强命中窄单元，却输给了在宽单元里刷了三个词的噪音。
    """
    return max(MIN_UNIT_WEIGHT, 1.0 - rows / max(total, 1))


def _reweighted(unit: QueryUnit, factor: float) -> QueryUnit:
    """按特异性缩放单元里每个 satisfier 的权重。"""
    scaled: list[object] = []
    for satisfier in unit.satisfiers:
        if isinstance(satisfier, LexicalSatisfier):
            scaled.append(
                LexicalSatisfier(
                    terms=satisfier.terms,
                    weight=satisfier.weight * factor,
                    fields=satisfier.fields,
                )
            )
        elif isinstance(satisfier, AnnotationSatisfier):
            scaled.append(
                AnnotationSatisfier(
                    units=satisfier.units,
                    names=satisfier.names,
                    weight=satisfier.weight * factor,
                )
            )
        elif isinstance(satisfier, ModifierSatisfier):
            scaled.append(
                ModifierSatisfier(modifiers=satisfier.modifiers, weight=satisfier.weight * factor)
            )
        else:
            scaled.append(satisfier)
    return QueryUnit(
        name=unit.name, concept=unit.concept, satisfiers=tuple(scaled), combine=unit.combine
    )


@dataclass(frozen=True, slots=True)
class _Sized:
    unit: object
    rows: int
    cost: float


def plan(spec: QuerySpec, ctx: EvalContext) -> Plan:
    """按预估选择性把规格排成计划。"""
    sized = sorted(
        (
            _Sized(unit=unit, rows=(guess := estimate_unit(unit, ctx)).rows, cost=guess.cost)
            for unit in spec.units
        ),
        key=lambda item: item.rows,
    )
    total = max(ctx.symbols.count(), 1)
    why: list[str] = []
    steps: list[Step] = []

    useful, dropped = _partition(sized, total)
    for item in dropped:
        why.append(
            f"丢掉单元 {item.unit.name!r}：预计命中 {item.rows} 个"
            f"（占 {100 * item.rows / total:.0f}%），几乎等于全表"
        )
    if not useful:
        # 全都太宽泛也不能什么都不做——留最窄的那个，至少有个结果
        useful, dropped = sized[:1], sized[1:]
        why.append("所有单元都很宽泛，保留最窄的一个避免空计划")

    for position, item in enumerate(useful):
        factor = _specificity(item.rows, total)
        steps.append(EvalUnit(_reweighted(item.unit, factor), seed=position == 0))
        why.append(
            f"{'先跑' if position == 0 else '并入'} {item.unit.name!r}"
            f"（预计 {item.rows} 个，占 {100 * item.rows / total:.0f}%，权重 ×{factor:.2f}）"
        )
    why.append("单元之间取**并集**不是交集——它们本来就落在不同元素上（01 章）")

    steps, why = _add_graph(spec, {item.unit.name: item.rows for item in useful}, steps, why)

    if spec.kinds or spec.concept:
        limit = INTENT_INPUT_CAP if spec.concept else spec.limit
        steps.append(Narrow(kind=spec.kinds or None, limit=limit, by=useful[0].unit.name))
        if spec.concept:
            why.append(f"判定前先压到 {INTENT_INPUT_CAP} 个：intent 比查表贵几千倍")
    elif spec.limit:
        steps.append(Narrow(limit=spec.limit, by=useful[0].unit.name))

    if spec.concept:
        steps.append(Intent(spec.concept, max_items=INTENT_INPUT_CAP))
        why.append("intent 放最后：它是唯一调 LLM 的算子，前面每一步都在替它省钱")

    return Plan(steps=tuple(steps), reasoning=tuple(why))


def _partition(sized: list[_Sized], total: int) -> tuple[list[_Sized], list[_Sized]]:
    useful = [item for item in sized if item.rows <= total * USELESS_RATIO and item.rows > 0]
    dropped = [item for item in sized if item not in useful]
    return useful, dropped


def _add_graph(
    spec: QuerySpec, rows: dict[str, int], steps: list[Step], why: list[str]
) -> tuple[list[Step], list[str]]:
    """插入图约束，并决定从哪一侧出发。

    图约束是**加权**不是过滤——只命中一侧的答案不该被杀掉。
    起点永远取小的一侧：`hop` 的代价随起点数线性增长。
    """
    for constraint in spec.graph:
        if constraint.src not in rows or constraint.dst not in rows:
            why.append(f"跳过图约束 {constraint.src}→{constraint.dst}：有一侧的单元没被保留")
            continue
        src, dst = constraint.src, constraint.dst
        if rows[dst] * ASYMMETRY < rows[src]:
            src, dst = dst, src
            why.append(
                f"图约束反向：从 {src!r}（{rows[src]} 个）出发而不是 "
                f"{dst!r}（{rows[dst]} 个）——hop 的代价随起点数线性增长"
            )
        steps.append(Boost(src, dst, edge=constraint.edge, hops=constraint.hops))
    return steps, why
