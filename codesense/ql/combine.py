"""How several signals hitting one unit combine into a single score.

The three strategies behave differently in practice
(``docs/design/04-query-unit.md``):

    max        any one strong signal suffices; favours recall
    sum        weak signals accumulate, but noise terms inflate it easily
    noisy_or   weak signals accumulate with an upper bound; the default

`noisy_or` needs components in [0, 1], which is why scoring normalises ICF.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from codesense.ql.registry import Registry

__all__ = ["COMBINERS", "combine"]

Combiner = Callable[[Sequence[float]], float]

#: Registry of strategies. Adding one means registering it here, nothing else.
COMBINERS: Registry[Combiner] = Registry("combiner")


@COMBINERS.decorator("max")
def _combine_max(scores: Sequence[float]) -> float:
    return max(scores) if scores else 0.0


@COMBINERS.decorator("sum")
def _combine_sum(scores: Sequence[float]) -> float:
    return sum(scores)


@COMBINERS.decorator("noisy_or")
def _combine_noisy_or(scores: Sequence[float]) -> float:
    """``1 - Prod(1 - s_i)``.

    Components outside [0, 1] make the result meaningless -- a negative one
    pushes the total past 1, one above 1 flips the sign of the product --
    so they are clamped rather than trusted.
    """
    residual = 1.0
    for score in scores:
        residual *= 1.0 - min(max(score, 0.0), 1.0)
    return 1.0 - residual


def combine(strategy: str, scores: Sequence[float]) -> float:
    """Combine by name. An unknown strategy raises rather than quietly
    falling back to some default."""
    return COMBINERS.get(strategy)(scores)
