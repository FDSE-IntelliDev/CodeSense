"""查询单元（Query Unit）。

**它是语义槽位，不是关键词，也不是一条 regex。**
单元是后续所有条件的挂载点——`hop` 和 `intent` 都挂在单元上，
所以单元的身份必须一路保留到最后，不能在匹配完成后被拍平。

设计依据见 ``docs/design/04-query-unit.md``。
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["QueryUnit", "Term"]


@dataclass(frozen=True, slots=True)
class Term:
    """单元的一个词，带来源与理由。

    ``source`` 的区分是有实际后果的：

        literal   查询里直接出现，置信度最高但代码里往往最少出现
        synonym   语义等价，可互换
        derived   语义联想（`performance` → `buffer`），**单独命中不可下结论**

    ``derived`` 词大幅提召回、明显伤精度，所以要么和同单元其它信号合成，
    要么靠 `hop` 的图约束锚住，要么交给 `intent` 复核。
    """

    value: str
    source: str = "literal"
    weight: float = 1.0
    reason: str = ""

    def __post_init__(self) -> None:
        if not self.value:
            raise ValueError("Term.value 不能为空")


@dataclass(frozen=True, slots=True)
class QueryUnit:
    """一个语义槽位，可以被多种信号满足。

    ``concept`` 是给人和 `intent` 算子看的自然语言描述，不参与匹配。
    ``satisfiers`` 里放的是 `codesense.ql.satisfiers` 的实例——
    这里不直接引用它们的类型，避免和 satisfier 模块循环依赖。
    """

    name: str
    concept: str = ""
    satisfiers: tuple[object, ...] = ()
    combine: str = "noisy_or"

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("QueryUnit.name 不能为空")


@dataclass(frozen=True, slots=True)
class UnitScore:
    """一个符号在某个单元上的最终得分与构成。"""

    unit: str
    score: float
    parts: tuple[float, ...] = field(default_factory=tuple)
