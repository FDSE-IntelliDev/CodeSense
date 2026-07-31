"""同一个单元被多个信号命中时，分数怎么合成。

三种策略的实际后果不同（``docs/design/04-query-unit.md``）：

    max        任一强信号即可，召回优先
    sum        多个弱信号可累积，但容易被一堆噪音词刷高
    noisy_or   多个独立弱信号可累积但有上界，**默认**

`noisy_or` 要求各分量在 [0, 1]，所以打分环节必须把 ICF 归一化。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from codesense.ql.registry import Registry

__all__ = ["COMBINERS", "combine"]

Combiner = Callable[[Sequence[float]], float]

#: 合成策略注册表。加一种策略只需在这里注册，不必改分发代码。
COMBINERS: Registry[Combiner] = Registry("合成策略")


@COMBINERS.decorator("max")
def _combine_max(scores: Sequence[float]) -> float:
    return max(scores) if scores else 0.0


@COMBINERS.decorator("sum")
def _combine_sum(scores: Sequence[float]) -> float:
    return sum(scores)


@COMBINERS.decorator("noisy_or")
def _combine_noisy_or(scores: Sequence[float]) -> float:
    """``1 - Π(1 - sᵢ)``。

    分量超出 [0, 1] 会让结果失去意义（负分量能把总分推过 1，
    大于 1 的分量能让乘积变号），所以这里直接夹紧而不是放任。
    """
    residual = 1.0
    for score in scores:
        residual *= 1.0 - min(max(score, 0.0), 1.0)
    return 1.0 - residual


def combine(strategy: str, scores: Sequence[float]) -> float:
    """按名字合成。未知策略立刻报错，不静默退化成某个默认值。"""
    return COMBINERS.get(strategy)(scores)
