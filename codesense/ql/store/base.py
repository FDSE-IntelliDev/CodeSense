"""索引访问的抽象接口。

算子只依赖这里的 ABC，不依赖具体存储。具体实现有两个：
``memory`` 用于测试与小规模，``sqlite`` 是真正的 IO 边界。

索引拆成四个产物（``docs/design/09-grounding.md`` 第五节），
因为它们的重建触发条件不同：

    symbols     代码变化时重建
    postings    符号或切分器/词表变化时重建
    terms       postings 变化时重算
    expansion   LLM 提示词 / 向量模型 / 阈值变化时重建，**与代码无关**

绑在一起就得整体重建；分开之后调阈值、换 embedding 不必碰索引。
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from codesense.ql.fields import IndexField
from codesense.ql.frag import Edge, Element

__all__ = [
    "EdgeStore",
    "Expansion",
    "ExpansionTable",
    "Posting",
    "PostingIndex",
    "SymbolStore",
    "TermInfo",
]


@dataclass(frozen=True, slots=True)
class Posting:
    """倒排表里的一条记录。

    **只存 symbol_id，不存符号对象。** 现有实现把整个符号对象内联进
    posting，1718 个符号就占 2.2 MB；外推到内核量级是 1 GB vs 11 MB。
    """

    symbol_id: int
    field: IndexField
    tf: int = 1


@dataclass(frozen=True, slots=True)
class TermInfo:
    """一个 term 的统计量。

    ``icf`` 按**符号**算而不是按调用链——两者量纲不同，不可混用。
    """

    term: str
    df: int
    total_symbols: int
    source: str = ""

    @property
    def icf(self) -> float:
        """``log(符号总数 / df)``。df 为 0 时记 0，不抛。"""
        if self.df <= 0 or self.total_symbols <= 0:
            return 0.0
        return math.log(self.total_symbols / self.df)

    @property
    def icf_ratio(self) -> float:
        """归一化到 [0, 1] 的 ICF，即 ``icf / log(符号总数)``。

        打分要用这个而不是裸 ICF，两个理由：``noisy_or`` 要求分量在 [0, 1]；
        裸 ICF 的量纲随项目大小变（``log(N)`` 是上界），阈值没法跨项目复用。
        """
        if self.total_symbols <= 1:
            return 0.0
        return self.icf / math.log(self.total_symbols)


@dataclass(frozen=True, slots=True)
class Expansion:
    """扩展表里的一条：从某个键扩展到某个目标，带分数和理由。

    ``reason`` 不是装饰——它决定这条扩展可不可信。``"meta"``（框架声明的
    元注解关系）是事实，分数 1.0；``"prefix"`` / ``"ctx"`` 是估计。
    """

    target: str
    score: float
    reason: str = ""


class SymbolStore(ABC):
    """symbol_id → Element。片段里节点本体的唯一来源。"""

    @abstractmethod
    def get(self, symbol_id: int) -> Element | None:
        """取一个元素；不存在返回 None。"""

    @abstractmethod
    def get_many(self, symbol_ids: Iterable[int]) -> dict[int, Element]:
        """批量取。缺失的 id 直接不出现在结果里，不抛。"""

    @abstractmethod
    def count(self) -> int:
        """符号总数。`TermInfo.icf` 的分母。"""


class PostingIndex(ABC):
    """term → postings，以及 term 的统计量。

    这一层**保持精确**：不做任何模糊匹配。模糊性全部放在 `ExpansionTable`，
    这样索引不需要改数据结构、不引入近似误差，调阈值也不必重建索引。
    """

    @abstractmethod
    def lookup(self, term: str) -> Sequence[Posting]:
        """精确查一个 term；没有返回空序列。"""

    @abstractmethod
    def term_info(self, term: str) -> TermInfo | None:
        """取 term 的统计量；不存在返回 None。"""

    @abstractmethod
    def terms(self) -> Iterable[str]:
        """遍历全部 term。建扩展表时要用。"""


class ExpansionTable(ABC):
    """离线算好的扩展表：规范词 → 项目里的实际写法。

    查询时纯查表，**不做向量计算、不调 LLM**。
    """

    @abstractmethod
    def expand(self, key: str) -> Sequence[Expansion]:
        """展开一个键；没有返回空序列。"""


class EdgeStore(ABC):
    """图的邻接访问。

    `hop` 需要的是**路径**而不是可达集，所以这里按方向取边而不是取邻居 id，
    调用方才能沿边回溯出路径。
    """

    @abstractmethod
    def out_edges(
        self,
        symbol_id: int,
        *,
        kinds: Sequence[str] | None = None,
        min_confidence: float = 0.0,
    ) -> Sequence[Edge]:
        """从该节点出发的边。``kinds=None`` 表示不限类型。"""

    @abstractmethod
    def in_edges(
        self,
        symbol_id: int,
        *,
        kinds: Sequence[str] | None = None,
        min_confidence: float = 0.0,
    ) -> Sequence[Edge]:
        """指向该节点的边。``dir="backward"`` 与双向 BFS 的反向半程要用。"""

    @abstractmethod
    def degree(self, symbol_id: int, *, kinds: Sequence[str] | None = None) -> int:
        """总度数。hub 节点限流要用——工具方法会被所有人调用，
        经过它们的路径几乎没有信息量。"""
