"""执行计划：一串有序的步骤，可跑、可打印、可解释。

计划本身就是产物。跑之前能看到规划器**为什么**这么排（每步的预估规模与
代价都印在旁边），跑之后能看到预估和实际差多少——差得离谱就说明估计模型
该修了。

设计依据见 ``docs/design/06-script-and-execution.md``。
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field

from codesense.ql.compile.cost import Estimate, estimate_hop, estimate_intent, estimate_unit
from codesense.ql.context import EvalContext
from codesense.ql.frag import Frag
from codesense.ql.operators import eval_unit, intent, reach, score_of, top
from codesense.ql.unit import QueryUnit

__all__ = ["Boost", "EvalUnit", "Filter", "Intent", "Narrow", "Plan", "State", "Step", "Trace"]

#: 落在图约束邻域里的候选获得的乘性加成。图是独立于词法的证据，
#: 所以加成而不是替代——它不该把词法完全不沾边的东西捧上来。
BOOST = 0.6


#: 元素种类命中偏好时的加成。比图加成弱——种类是模型猜的，图是索引里的事实。
KIND_PREFERENCE = 0.3


def _top_with_boost(
    frag: Frag, limit: int, boosted: set[int], preferred: set[int] = frozenset()
) -> Frag:
    """按「词法分数 × 图加成 × 种类偏好」取前 n 个。"""
    if not boosted and not preferred:
        return top(frag, limit)
    ordered = sorted(
        frag.nodes,
        key=lambda sid: (
            -score_of(frag, sid)
            * (1 + BOOST * (sid in boosted))
            * (1 + KIND_PREFERENCE * (sid in preferred)),
            sid,
        ),
    )
    return frag.induced(ordered[:limit])


_log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Trace:
    """一步跑完之后的实际情况。"""

    label: str
    estimated: int
    actual: int
    seconds: float
    skipped: str = ""

    @property
    def drift(self) -> float:
        """预估与实际的倍数偏差。用来检验估计模型准不准。"""
        return self.actual / max(self.estimated, 1)


@dataclass
class State:
    """执行过程中的工作集。

    ``projected`` 让同一套 `estimate` 既能用于真跑，也能用于**不跑的静态预演**：
    预演时没有真的 Frag，只有一路传下来的预计行数。
    """

    units: dict[str, Frag] = field(default_factory=dict)
    current: Frag = field(default_factory=Frag)
    trace: list[Trace] = field(default_factory=list)
    projected: int | None = None
    projected_units: dict[str, int] = field(default_factory=dict)

    #: 被图约束加权过的符号。排序时用，不影响集合成员。
    boosted: set[int] = field(default_factory=set)

    @property
    def rows(self) -> int:
        return len(self.current) if self.projected is None else self.projected

    def unit_rows(self, name: str) -> int:
        if self.projected is None:
            return len(self.units.get(name, Frag()))
        return self.projected_units.get(name, 0)

    @property
    def stopped(self) -> bool:
        """工作集已经空了——后面的步骤没有意义。"""
        return bool(self.units) and not self.current


class Step(ABC):
    """计划里的一步。"""

    label: str

    @abstractmethod
    def estimate(self, ctx: EvalContext, state: State) -> Estimate:
        """不执行地估算这一步的输出与代价。"""

    @abstractmethod
    def apply(self, ctx: EvalContext, state: State) -> None:
        """就地更新工作集。"""


@dataclass(slots=True)
class EvalUnit(Step):
    """求值一个查询单元，并集进工作集。

    ``seed`` 表示它是第一个。命中多个单元的符号分数更高（证据累加），
    但只命中一个也不会被淘汰——**淘汰的活交给 `Boost` 和 `Narrow`**。
    """

    unit: QueryUnit
    seed: bool = False
    label: str = ""

    def __post_init__(self) -> None:
        self.label = f"unit({self.unit.name}){'  ← 种子' if self.seed else ''}"

    def estimate(self, ctx: EvalContext, state: State) -> Estimate:
        """这一步输出的是**并集**，不是单元本身。"""
        guess = estimate_unit(self.unit, ctx)
        if self.seed:
            return guess
        # 并集：按独立假设 |A∪B| = N·(1 − (1−|A|/N)(1−|B|/N))
        total = max(ctx.symbols.count(), 1)
        merged = total * (1 - (1 - state.rows / total) * (1 - guess.rows / total))
        return Estimate(rows=round(merged), cost=guess.cost, detail=guess.detail)

    def apply(self, ctx: EvalContext, state: State) -> None:
        found = eval_unit(self.unit, ctx)
        state.units[self.unit.name] = found
        # **并集，不是交集。** 单元落在不同元素上——「同时含有 performance 和
        # disk 关键词的元素几乎不存在」（01 章）。交集会把答案杀光：实测
        # netty 的零拷贝查询里 gold 全在一个单元内，交完一个不剩。
        # 单元之间的关系由图约束表达，不由集合运算表达。
        state.current = found if self.seed else (state.current | found)


@dataclass(slots=True)
class Filter(Step):
    """用一个已经求值过的单元收窄工作集。"""

    unit_name: str
    label: str = ""

    def __post_init__(self) -> None:
        self.label = f"& {self.unit_name}"

    def estimate(self, ctx: EvalContext, state: State) -> Estimate:
        other = state.unit_rows(self.unit_name)
        return Estimate(rows=min(state.rows, other), cost=float(state.rows))

    def apply(self, ctx: EvalContext, state: State) -> None:
        state.current = state.current & state.units.get(self.unit_name, Frag())


@dataclass(slots=True)
class Boost(Step):
    """图约束：给结构上连着的候选**加权**，而不是把别的筛掉。

    「performance 相关的代码调用了 disk 相关的代码」说的是两个单元之间的
    关系，不是对候选集的过滤。用它做硬过滤会把只命中一侧的答案全杀掉——
    而那恰恰是最常见的情形。

    起点刻意取**小的一侧**：`hop` 的代价随起点数线性增长。
    """

    src_name: str
    dst_name: str
    edge: tuple[str, ...] = ("calls", "contains")
    hops: tuple[int, int] = (1, 2)
    weight: float = 0.6
    label: str = ""

    def __post_init__(self) -> None:
        self.label = f"boost({self.src_name} ↔ {self.dst_name}, {self.hops[0]}~{self.hops[1]} 跳)"

    def estimate(self, ctx: EvalContext, state: State) -> Estimate:
        touched = estimate_hop(
            state.unit_rows(self.src_name),
            ctx,
            hops=self.hops,
            dst_rows=state.unit_rows(self.dst_name),
        )
        # 加权不改变候选数量，只改变排序
        return Estimate(rows=state.rows, cost=touched.cost, detail=touched.detail)

    def apply(self, ctx: EvalContext, state: State) -> None:
        src = state.units.get(self.src_name, Frag())
        if not src or not state.current:
            return
        near = reach(src, ctx, edge=list(self.edge), direction="any", hops=self.hops)
        state.boosted |= set(near.nodes) & set(state.current.nodes)


@dataclass(slots=True)
class Narrow(Step):
    """收窄到最贵那步能承受的规模。

    ``kind`` 是**偏好不是过滤**。模型给的种类不可靠：实测查询问「实体字段上的
    校验约束」，模型给的 kinds 里偏偏没有 `field`，硬过滤会把答案全删光。
    和图约束同一个道理——不可靠的信号加权，可靠的信号才过滤。
    """

    kind: tuple[str, ...] | None = None
    limit: int | None = None
    by: str | None = None
    label: str = ""

    def __post_init__(self) -> None:
        parts = []
        if self.kind:
            parts.append(f"偏好 {'/'.join(self.kind[:3])}{'…' if len(self.kind) > 3 else ''}")
        if self.limit:
            parts.append(f"top {self.limit}")
        self.label = "narrow(" + ", ".join(parts) + ")"

    def estimate(self, ctx: EvalContext, state: State) -> Estimate:
        rows = min(state.rows, self.limit) if self.limit else state.rows
        return Estimate(rows=rows, cost=float(state.rows))

    def apply(self, ctx: EvalContext, state: State) -> None:
        found = state.current
        preferred = (
            {sid for sid, e in found.nodes.items() if e.kind in self.kind} if self.kind else set()
        )
        if self.limit:
            found = _top_with_boost(found, self.limit, state.boosted, preferred)
        state.current = found


@dataclass(slots=True)
class Intent(Step):
    """语义判定。**永远是最后一步**，而且规划器会先用 `Narrow` 压住它的输入。"""

    concept: str
    threshold: float = 0.5
    max_items: int | None = 60
    label: str = ""

    def __post_init__(self) -> None:
        self.label = (
            f'intent("{self.concept[:28]}…")'
            if len(self.concept) > 28
            else f'intent("{self.concept}")'
        )

    def estimate(self, ctx: EvalContext, state: State) -> Estimate:
        return estimate_intent(state.rows)

    def apply(self, ctx: EvalContext, state: State) -> None:
        state.current = intent(
            state.current,
            self.concept,
            ctx,
            threshold=self.threshold,
            max_items=self.max_items,
        )


@dataclass(frozen=True, slots=True)
class Plan:
    """一串有序的步骤，外加规划器为什么这么排的说明。"""

    steps: tuple[Step, ...]
    reasoning: tuple[str, ...] = ()

    def run(self, ctx: EvalContext, *, skip: tuple[type[Step], ...] = ()) -> State:
        """执行。``skip`` 用来试跑时跳过昂贵的步骤（通常是 `Intent`）。

        工作集一旦空了就停——后面的步骤既不会改变结果，
        又可能白白花掉一次 LLM 调用。
        """
        state = State()
        for step in self.steps:
            if isinstance(step, skip):
                state.trace.append(
                    Trace(step.label, 0, len(state.current), 0.0, skipped="试跑跳过")
                )
                continue
            if state.stopped:
                state.trace.append(Trace(step.label, 0, 0, 0.0, skipped="工作集已空"))
                continue
            predicted = step.estimate(ctx, state).rows
            started = time.perf_counter()
            step.apply(ctx, state)
            state.trace.append(
                Trace(step.label, predicted, len(state.current), time.perf_counter() - started)
            )
        return state

    def explain(self, ctx: EvalContext) -> str:
        """跑之前打印计划与预估。"""
        lines = ["执行计划："]
        lines += [f"  · {why}" for why in self.reasoning]
        lines.append("")
        state = State(projected=0)
        for index, step in enumerate(self.steps, 1):
            guess = step.estimate(ctx, state)
            lines.append(f"  {index}. {step.label:<44}{guess}")
            # 把预估结果一路传下去，让后面几步基于前面的**预计**规模来估
            if isinstance(step, EvalUnit) and not step.seed:
                # 单元本身的规模要单独记：图约束选方向时看的是它，不是交集
                state.projected_units[step.unit.name] = estimate_unit(step.unit, ctx).rows
            elif isinstance(step, EvalUnit):
                state.projected_units[step.unit.name] = guess.rows
            state.projected = guess.rows
        return "\n".join(lines)

    @staticmethod
    def report(trace: Sequence[Trace]) -> str:
        """跑之后对比预估与实际。差得离谱说明估计模型该修了。"""
        lines = [f"  {'步骤':<44}{'预估':>8}{'实际':>8}{'耗时':>9}"]
        for item in trace:
            if item.skipped:
                lines.append(f"  {item.label:<44}{item.skipped:>16}")
                continue
            spent = f"{item.seconds * 1000:.0f}ms"
            lines.append(f"  {item.label:<44}{item.estimated:>8}{item.actual:>8}{spent:>9}")
        return "\n".join(lines)
