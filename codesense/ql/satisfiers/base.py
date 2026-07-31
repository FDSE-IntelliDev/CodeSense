"""Satisfier：一个查询单元被满足的一种方式。

判断一段代码是不是「和性能有关」，词法只是**最弱的一种**证据。
注解、结构位置、修饰符、语义相似度都能满足同一个单元，
所以单元与词法解绑，`Satisfier` 是这个解绑的落点。

设计依据见 ``docs/design/04-query-unit.md``。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import ClassVar

from codesense.ql.context import EvalContext
from codesense.ql.fields import IndexField
from codesense.ql.frag import UnitHit
from codesense.ql.registry import Registry
from codesense.ql.store.base import Expansion, TermInfo
from codesense.ql.unit import Term

__all__ = ["SATISFIERS", "Satisfier", "collect_term_hits"]

HitsBySymbol = Mapping[int, tuple[UnitHit, ...]]

#: 「这个词就是调用方给的，不是扩展出来的」。
_EXACT = "exact"

#: satisfier 类型注册表，供编译产物按名字构造。
SATISFIERS: Registry[type[Satisfier]] = Registry("satisfier")


class Satisfier(ABC):
    """把一个单元的判定条件求值成「哪些符号命中、各命中多少分」。

    实现类必须声明 ``signal``——它会写进证据，让结果能说清
    「这个元素是被词法命中的还是被注解命中的」。
    """

    signal: ClassVar[str]

    @abstractmethod
    def hits(self, unit: str, ctx: EvalContext) -> HitsBySymbol:
        """求值。返回 symbol_id → 该 satisfier 给出的全部证据。"""


def collect_term_hits(
    *,
    unit: str,
    signal: str,
    terms: Sequence[Term],
    ctx: EvalContext,
    weight: float,
    fields: Sequence[IndexField] | None,
) -> HitsBySymbol:
    """词 → 扩展 → 倒排 → 证据。词法类 satisfier 共用这一段。

    只做**第二跳**（规范词 → 项目里的实际写法）。第一跳（`performance`
    → `cache`/`buffer`）在编译期由 LLM 随「查询拆单元」那一次调用一起给出，
    所以运行时拿到的 `terms` 已经是规范词，见
    ``docs/design/09-grounding.md`` 第六节。

    打分是**连乘**：每一跳都是一次打折。经过语义联想、缩写映射、
    再命中在 doc 而不是 name 之后，这条证据本就该远低于直接命中名字的那条。
    """
    allowed = None if fields is None else frozenset(fields)
    found: dict[int, list[UnitHit]] = defaultdict(list)

    for term in terms:
        for surface in _surfaces(term, ctx):
            info = ctx.postings.term_info(surface.target)
            if info is None or _too_generic(surface, info, ctx):
                continue
            for posting in ctx.postings.lookup(surface.target):
                if allowed is not None and posting.field not in allowed:
                    continue
                score = (
                    weight
                    * term.weight
                    * surface.score
                    * ctx.field_weights.weight(posting.field)
                    * info.icf_ratio
                )
                if score < ctx.min_hit_score:
                    continue
                found[posting.symbol_id].append(
                    UnitHit(
                        unit=unit,
                        signal=signal,
                        detail=_detail(term, surface),
                        field=str(posting.field),
                        score=score,
                    )
                )
    return {sid: tuple(hits) for sid, hits in found.items()}


def _too_generic(surface: Expansion, info: TermInfo, ctx: EvalContext) -> bool:
    """ICF 下限**只管扩展出来的词，不管查询自己带的词**。

    这是 benchmark 逼出来的修正。原先对所有词一视同仁地卡下限，
    结果在大项目上灾难性地反噬：netty 有 42221 个符号，`buf` 出现在
    15.4% 的符号里、`allocator` 3.6%，双双低于下限被丢掉——
    而查询问的正是缓冲区分配。`PooledByteBufAllocator` 因此完全召回不到。

    下限的本意是挡住扩展带来的噪音（[09](docs/design/09-grounding.md) 第七节
    「低 ICF 的词直接不进扩展表」），**从来不是丢掉调用方明确要求的词**。
    低 ICF 的查询词该被**降权**（`icf_ratio` 本来就在打分里连乘），
    而不是被**排除**。
    """
    if surface.reason == _EXACT:
        return False
    return info.icf_ratio < ctx.icf_floor


def _surfaces(term: Term, ctx: EvalContext) -> list[Expansion]:
    """词本身 + 扩展表里的项目实际写法。

    词本身永远排第一且不打折——项目里就这么写的时候，没有理由降权。
    """
    return [Expansion(term.value, 1.0, _EXACT), *ctx.expansion.expand(term.value)]


def _detail(term: Term, surface: Expansion) -> str:
    """证据里的可读说明：命中了什么、为什么算命中。"""
    if surface.reason == _EXACT:
        return term.value
    return f"{surface.target}←{term.value}({surface.reason})"
