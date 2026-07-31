"""查询规格：编译器的中间表示。

自然语言先编译成它（那一步要 LLM，在 `codesense.llm` 里），
再由 `planner` 排成执行计划。中间隔这一层的好处是**规格可以手写**——
调试时不必每次都过一遍模型。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from codesense.ql.satisfiers import AnnotationSatisfier, LexicalSatisfier, ModifierSatisfier
from codesense.ql.unit import QueryUnit, Term

__all__ = ["GraphConstraint", "QuerySpec", "normalise_hops", "normalise_kinds"]

#: 图约束的默认跳数。跳数越多「有关系」这个结论越弱。
DEFAULT_HOPS = (1, 2)

#: 模型说的种类 → 索引里的种类。
#:
#: **这是个真实的阻抗不匹配。** 模型说 "class" 时心里含接口和枚举，
#: 而索引里 `interface` 是独立的 kind。不映射就会静默丢结果——
#: 实测 netty 的零拷贝查询里，`FileRegion` 正是接口，
#: 被 `kind=class` 直接筛没了，R@100 从 50% 掉到 25%。
KIND_ALIASES: dict[str, tuple[str, ...]] = {
    "class": ("class", "interface", "enum", "record", "annotation_type"),
    "type": ("class", "interface", "enum", "record", "annotation_type"),
    "interface": ("interface",),
    "enum": ("enum",),
    "record": ("record",),
    "method": ("method", "constructor"),
    "function": ("method", "constructor"),
    "constructor": ("constructor",),
    "field": ("field",),
    "variable": ("field",),
    "annotation": ("annotation_type",),
}


def normalise_kinds(raw: object) -> tuple[str, ...]:
    """把模型给的种类映射成索引认识的那些，未知的原样保留。"""
    if not raw:
        return ()
    values = [raw] if isinstance(raw, str) else list(raw)
    found: dict[str, None] = {}
    for item in values:
        if not isinstance(item, str):
            continue
        for kind in KIND_ALIASES.get(item.strip().lower(), (item.strip().lower(),)):
            found.setdefault(kind, None)
    return tuple(found)


def normalise_hops(raw: object) -> tuple[int, int]:
    """把模型给的跳数收成一个合法闭区间。"""
    if isinstance(raw, int):
        return (raw, raw) if raw >= 0 else DEFAULT_HOPS
    try:
        values = [int(v) for v in raw]  # type: ignore[union-attr]
    except (TypeError, ValueError):
        return DEFAULT_HOPS
    if not values:
        return DEFAULT_HOPS
    if len(values) == 1:
        values = [1, values[0]]
    low, high = max(values[0], 0), max(values[1], 0)
    return (low, high) if low <= high else (high, low)


@dataclass(frozen=True, slots=True)
class GraphConstraint:
    """两个单元之间的图约束。

    方向是**语义上的**（谁调用谁）；真正执行时从哪一侧出发由规划器决定，
    因为那是代价问题不是语义问题。
    """

    src: str
    dst: str
    edge: tuple[str, ...] = ("calls", "contains")
    hops: tuple[int, int] = (1, 2)

    def __post_init__(self) -> None:
        """规格来自 LLM，不变式得自己守。

        实测模型会给出 `hops: [2]` 甚至 `[]`——直接用就会在别处
        以 IndexError 的形式炸掉，而那时已经离出错点很远了。
        """
        object.__setattr__(self, "hops", normalise_hops(self.hops))
        if not self.edge:
            object.__setattr__(self, "edge", ("calls", "contains"))


@dataclass(frozen=True, slots=True)
class QuerySpec:
    """一条查询的完整结构。"""

    query: str
    units: tuple[QueryUnit, ...]
    graph: tuple[GraphConstraint, ...] = ()
    concept: str = ""
    kinds: tuple[str, ...] = ()
    limit: int | None = None

    def __post_init__(self) -> None:
        if not self.units:
            raise ValueError("查询规格至少要有一个单元")
        names = [unit.name for unit in self.units]
        if len(names) != len(set(names)):
            raise ValueError(f"单元名重复: {names}")
        known = set(names)
        for constraint in self.graph:
            unknown = {constraint.src, constraint.dst} - known
            if unknown:
                raise ValueError(f"图约束引用了不存在的单元: {sorted(unknown)}")

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> QuerySpec:
        """从 LLM 产出的 JSON 构造。**容错但不猜**：结构不对就报错。"""
        units = tuple(_unit(item) for item in payload.get("units", ()))
        graph = tuple(
            GraphConstraint(
                src=str(item["src"]),
                dst=str(item["dst"]),
                edge=tuple(item.get("edge") or ("calls", "contains")),
                hops=normalise_hops(item.get("hops")),
            )
            for item in payload.get("graph", ())
            if isinstance(item, dict) and "src" in item and "dst" in item
        )
        return cls(
            query=str(payload.get("query", "")),
            units=units,
            graph=graph,
            concept=str(payload.get("concept") or ""),
            kinds=normalise_kinds(payload.get("kinds")),
            limit=payload.get("limit"),
        )


def _unit(payload: dict[str, Any]) -> QueryUnit:
    satisfiers: list[object] = []
    terms = [t for t in payload.get("terms", ()) if isinstance(t, str) and t]
    if terms:
        satisfiers.append(LexicalSatisfier(terms=tuple(Term(t.lower()) for t in terms)))
    annotations = [a for a in payload.get("annotations", ()) if isinstance(a, str) and a]
    if annotations:
        satisfiers.append(AnnotationSatisfier(names=tuple(annotations)))
    modifiers = [m for m in payload.get("modifiers", ()) if isinstance(m, str) and m]
    if modifiers:
        satisfiers.append(ModifierSatisfier(modifiers=tuple(modifiers)))
    if not satisfiers:
        raise ValueError(f"单元 {payload.get('name')!r} 没有任何可执行的条件")
    return QueryUnit(
        name=str(payload["name"]),
        concept=str(payload.get("concept") or ""),
        satisfiers=tuple(satisfiers),
    )


def units_named(spec: QuerySpec, names: Sequence[str]) -> tuple[QueryUnit, ...]:
    wanted = set(names)
    return tuple(unit for unit in spec.units if unit.name in wanted)
