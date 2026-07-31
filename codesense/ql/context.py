"""求值上下文：算子运行时需要的一切外部依赖。

所有依赖从构造函数注入，模块里不读配置、不开数据库、不碰全局状态。
这样算子既可测（注入内存实现），又能换存储而不改代码。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from codesense.ql.fields import DEFAULT_FIELD_WEIGHTS, FieldWeights
from codesense.ql.store.base import EdgeStore, ExpansionTable, PostingIndex, SymbolStore

__all__ = ["EvalContext"]


@dataclass(frozen=True, slots=True)
class EvalContext:
    """把索引访问与打分参数打成一包传给算子。"""

    symbols: SymbolStore
    postings: PostingIndex
    expansion: ExpansionTable
    edges: EdgeStore
    field_weights: FieldWeights = field(default=DEFAULT_FIELD_WEIGHTS)

    #: 归一化 ICF 的下限。低于它的词不参与打分——`get`（1718 个符号里占 207 个）
    #: 这类词什么都"相似"，扩展它只会制造噪音。
    #: 0.34 对应样例项目上裸 ICF ≈ 2.5（log(1718) ≈ 7.45）。
    icf_floor: float = 0.34

    #: 单条证据的最低分。低于它的命中直接丢弃，避免证据链被噪音淹没。
    min_hit_score: float = 1e-6
