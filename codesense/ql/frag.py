"""代码片段（Frag）及其构成元素。

算子之间流动的东西只有一种：`Frag`——一个带证据标注的代码子图。
设计依据见 ``docs/design/03-data-model.md``。

本模块只定义数据与其上的纯运算，**不做任何 IO**：不读库、不读文件、
不查配置。片段的来源（索引、图遍历）由 ``codesense.ql.store`` 提供。
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TypeVar

__all__ = [
    "Edge",
    "EdgeKey",
    "Element",
    "Evidence",
    "Frag",
    "Path",
    "UnitHit",
    "Verdict",
]

#: 边的去重键：(源, 目标, 类型)。同一对节点之间不同 kind 的边彼此独立。
EdgeKey = tuple[int, int, str]

_T = TypeVar("_T")


@dataclass(frozen=True, slots=True)
class Element:
    """代码库里的一个元素。

    ``frozen``：元素在多个算子之间流转，任何原地修改都会让上游拿到被篡改的
    数据。要附加信息就进 `Evidence`，不要改元素本身。
    """

    symbol_id: int
    name: str
    kind: str
    file: str
    span: tuple[int, int]
    signature: str = ""
    container: str = ""
    language: str = ""
    doc: str = ""
    modifiers: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class Edge:
    """两个元素之间的一条有向关系。

    ``confidence`` 与 ``provenance`` 不是装饰：动态分派、反射、接口多实现
    都会让边带不确定性，查询时要能按置信度过滤，出结果时要能说清来源。
    """

    source_id: int
    target_id: int
    kind: str
    site: tuple[int, int] | None = None
    confidence: float = 1.0
    provenance: str = ""

    @property
    def key(self) -> EdgeKey:
        return (self.source_id, self.target_id, self.kind)


@dataclass(frozen=True, slots=True)
class Path:
    """一条路径。只存 symbol_id，元素本体在 `Frag.nodes` 里。

    否则同一个元素会在几百条路径里重复出现几百份。
    """

    nodes: tuple[int, ...]
    edges: tuple[Edge, ...]

    def __len__(self) -> int:
        return len(self.edges)


@dataclass(frozen=True, slots=True)
class UnitHit:
    """某个查询单元被满足的一条证据。

    ``signal`` 区分单元是被哪种信号满足的——单元可以由词法、注解、结构位置、
    修饰符、语义相似度等多种信号满足，词法只是其中最弱的一种。
    """

    unit: str
    signal: str
    detail: str
    field: str = ""
    score: float = 1.0
    span: tuple[int, int] | None = None


@dataclass(frozen=True, slots=True)
class Verdict:
    """一次判定（如 `intent` 算子的 LLM 判断），必须带理由。"""

    source: str
    label: str
    reason: str = ""
    score: float = 1.0


@dataclass(frozen=True, slots=True)
class Evidence:
    """这个元素为什么在结果里。

    三条约束（``docs/design/03-data-model.md``）：

    1. 只追加，不覆盖——可解释性来自完整的因果链。
    2. 不参与相等判断——否则 ``a | a != a``。由 `Frag.__eq__` 保证。
    3. 必须可序列化——它要落盘供人事后查。
    """

    unit_hits: tuple[UnitHit, ...] = ()
    verdicts: tuple[Verdict, ...] = ()

    @property
    def scores(self) -> Mapping[str, float]:
        """按单元汇总的分数。派生量，不可单独设置。"""
        acc: dict[str, float] = {}
        for hit in self.unit_hits:
            acc[hit.unit] = acc.get(hit.unit, 0.0) + hit.score
        return MappingProxyType(acc)

    def merge(self, other: Evidence) -> Evidence:
        """合并两份证据，保序去重。

        交集运算必须调它：一个元素同时满足两边时，**两边的理由都得留**。
        """
        return Evidence(
            unit_hits=_dedup(self.unit_hits + other.unit_hits),
            verdicts=_dedup(self.verdicts + other.verdicts),
        )


def _dedup(items: tuple[_T, ...]) -> tuple[_T, ...]:
    """保序去重。证据条目都是 frozen dataclass，可哈希。"""
    seen: set[_T] = set()
    out: list[_T] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return tuple(out)


@dataclass(frozen=True, slots=True, eq=False)
class Frag:
    """代码库的一个片段：一组节点、它们之间的边、以及每个节点的证据。

    这是算子之间唯一流动的类型，所有算子都是 ``Frag -> Frag``。

    相等性**只看节点与边**，不看证据和路径见证——否则 ``a | a != a``。
    片段不可哈希（它是容器，不该做字典键）。
    """

    nodes: Mapping[int, Element] = field(default_factory=dict)
    edges: Mapping[EdgeKey, Edge] = field(default_factory=dict)
    evidence: Mapping[int, Evidence] = field(default_factory=dict)
    witnesses: tuple[Path, ...] = ()

    __hash__ = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        # 冻结映射：frozen 只挡住重新赋值，挡不住原地改内容。
        object.__setattr__(self, "nodes", MappingProxyType(dict(self.nodes)))
        object.__setattr__(self, "edges", MappingProxyType(dict(self.edges)))
        object.__setattr__(self, "evidence", MappingProxyType(dict(self.evidence)))
        dangling = {
            side for key in self.edges for side in (key[0], key[1]) if side not in self.nodes
        }
        if dangling:
            raise ValueError(f"边指向了不在片段里的节点: {sorted(dangling)}")

    # ---- 基本性质 ----

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Frag):
            return NotImplemented
        return dict(self.nodes) == dict(other.nodes) and dict(self.edges) == dict(other.edges)

    def __len__(self) -> int:
        return len(self.nodes)

    def __iter__(self) -> Iterator[Element]:
        return iter(self.nodes.values())

    def __contains__(self, symbol_id: object) -> bool:
        return symbol_id in self.nodes

    def __bool__(self) -> bool:
        return bool(self.nodes)

    def evidence_for(self, symbol_id: int) -> Evidence:
        """取某个节点的证据；没有则返回空证据，不抛异常。"""
        return self.evidence.get(symbol_id, Evidence())

    # ---- 片段代数 ----

    def __or__(self, other: Frag) -> Frag:
        """并：节点并、边并、证据合并。"""
        return Frag(
            nodes={**self.nodes, **other.nodes},
            edges={**self.edges, **other.edges},
            evidence=_merge_evidence(self.evidence, other.evidence),
            witnesses=_dedup(self.witnesses + other.witnesses),
        )

    def __and__(self, other: Frag) -> Frag:
        """交：节点交；边保留两端都在的；**证据合并**。

        证据合并这条最容易漏，漏了就会出现「结果里有个元素但说不出它为什么在」。
        """
        kept = self.nodes.keys() & other.nodes.keys()
        return _induced(
            nodes={sid: self.nodes[sid] for sid in kept},
            edges={**self.edges, **other.edges},
            evidence=_merge_evidence(self.evidence, other.evidence),
            witnesses=self.witnesses + other.witnesses,
        )

    def __sub__(self, other: Frag) -> Frag:
        """差：去掉 other 的节点及其关联边；保留 self 的证据。"""
        kept = self.nodes.keys() - other.nodes.keys()
        return _induced(
            nodes={sid: self.nodes[sid] for sid in kept},
            edges=self.edges,
            evidence=self.evidence,
            witnesses=self.witnesses,
        )

    # ---- 投影 ----

    def induced(self, symbol_ids: Iterable[int]) -> Frag:
        """取子集诱导的子图。"""
        kept = {sid for sid in symbol_ids if sid in self.nodes}
        return _induced(
            nodes={sid: self.nodes[sid] for sid in kept},
            edges=self.edges,
            evidence=self.evidence,
            witnesses=self.witnesses,
        )

    def roots(self) -> Frag:
        """入度为 0 的节点——路径起点，没有任何边指向它们。"""
        has_incoming = {key[1] for key in self.edges}
        return self.induced(self.nodes.keys() - has_incoming)

    def leaves(self) -> Frag:
        """出度为 0 的节点——路径终点，它们不指向任何东西。"""
        has_outgoing = {key[0] for key in self.edges}
        return self.induced(self.nodes.keys() - has_outgoing)

    def only_nodes(self) -> Frag:
        """丢掉边与路径见证，退化成纯节点集。

        逃生舱：当片段结构反而碍事时，一行退回集合语义。
        """
        return Frag(nodes=self.nodes, evidence=self.evidence)


def _merge_evidence(
    left: Mapping[int, Evidence], right: Mapping[int, Evidence]
) -> dict[int, Evidence]:
    merged = dict(left)
    for symbol_id, ev in right.items():
        existing = merged.get(symbol_id)
        merged[symbol_id] = ev if existing is None else existing.merge(ev)
    return merged


def _induced(
    *,
    nodes: Mapping[int, Element],
    edges: Mapping[EdgeKey, Edge],
    evidence: Mapping[int, Evidence],
    witnesses: tuple[Path, ...],
) -> Frag:
    """按给定节点集裁掉悬空的边与路径，保证 `Frag` 的不变式成立。"""
    kept_edges = {key: edge for key, edge in edges.items() if key[0] in nodes and key[1] in nodes}
    kept_paths = tuple(p for p in witnesses if all(n in nodes for n in p.nodes))
    return Frag(
        nodes=nodes,
        edges=kept_edges,
        evidence={sid: ev for sid, ev in evidence.items() if sid in nodes},
        witnesses=_dedup(kept_paths),
    )
