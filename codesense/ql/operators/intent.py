"""`intent` 算子：返回片段中满足某个意图的部分。

**执行期唯一调 LLM 的算子，也是最贵的。** 三条纪律
（``docs/design/05-operators.md``）在这里都落成了代码：

1. 放最后、作用在最小片段上——`max_items` 超限直接报错，
   逼调用方先用便宜约束收窄，而不是默默烧钱
2. 判定必须进证据——verdict 带 reason，否则用户无从判断该不该信
3. 必须能降级——LLM 不可用时按 `fallback` 走，不让整条查询失败
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from codesense.ql.context import EvalContext
from codesense.ql.frag import Element, Evidence, Frag, Verdict
from codesense.ql.judge import UNSURE, JudgeItem, item_of

__all__ = ["DEFAULT_MAX_ITEMS", "FALLBACKS", "intent"]

_log = logging.getLogger(__name__)

#: 交给 LLM 的候选数上限。超了就报错——
#: 「前面还有没用上的便宜约束」是编排错误，不该由钱来兜底。
DEFAULT_MAX_ITEMS = 200

#: 判不出来时怎么办。
#:
#:     keep   保留（召回优先，宁可多给）
#:     drop   丢弃（精度优先）
#:     error  直接失败（不接受静默降级的场景）
FALLBACKS = ("keep", "drop", "error")

_YES = "yes"


def intent(
    frag: Frag,
    concept: str,
    ctx: EvalContext,
    *,
    threshold: float = 0.5,
    batch_size: int = 5,
    fallback: str = "keep",
    max_items: int | None = DEFAULT_MAX_ITEMS,
) -> Frag:
    """判定片段里哪些元素真的在做 ``concept`` 说的那件事。

    ``concept`` 用自然语言写，通常直接取查询单元的 ``concept`` 字段。
    """
    if fallback not in FALLBACKS:
        raise ValueError(f"fallback 只能是 {FALLBACKS} 之一，收到 {fallback!r}")
    if not frag:
        return frag
    if max_items is not None and len(frag) > max_items:
        raise ValueError(
            f"intent 收到 {len(frag)} 个候选，超过 max_items={max_items}。"
            "它是最贵的算子，应当放在最后、作用在最小片段上——"
            "先用 hop / only / top 收窄，或显式调大 max_items。"
        )

    verdicts = _collect(frag, concept, ctx, batch_size)
    kept: dict[int, Element] = {}
    evidence: dict[int, Evidence] = {}
    undecided = 0

    for symbol_id, element in frag.nodes.items():
        verdict = verdicts.get(symbol_id)
        if verdict is None:
            undecided += 1
            if fallback == "error":
                raise RuntimeError(f"符号 {symbol_id} 判定失败，且 fallback='error'")
            if fallback == "drop":
                continue
            verdict = Verdict(source="fallback", label=UNSURE, reason="判定不可用，按降级策略保留")
        elif not _passes(verdict, threshold):
            continue
        kept[symbol_id] = element
        evidence[symbol_id] = frag.evidence_for(symbol_id).merge(Evidence(verdicts=(verdict,)))

    if undecided:
        _log.warning(
            "intent 有 %d/%d 个候选没判出来，按 fallback=%r 处理", undecided, len(frag), fallback
        )
    return Frag(
        nodes=kept,
        edges={key: e for key, e in frag.edges.items() if key[0] in kept and key[1] in kept},
        evidence=evidence,
        witnesses=tuple(p for p in frag.witnesses if all(n in kept for n in p.nodes)),
    )


def _passes(verdict: Verdict, threshold: float) -> bool:
    """判定为是、且置信度过线。

    `unsure` 不算通过——判不出和判为是是两回事，前者该走降级而不是直接放行。
    """
    return verdict.label == _YES and verdict.score >= threshold


def _collect(frag: Frag, concept: str, ctx: EvalContext, batch_size: int) -> dict[int, Verdict]:
    if batch_size < 1:
        raise ValueError(f"batch_size 必须为正，收到 {batch_size}")
    items = [item_of(element) for element in frag.nodes.values()]
    found: dict[int, Verdict] = {}
    known = set(frag.nodes)
    for batch in _batches(items, batch_size):
        try:
            answered = ctx.judge.judge(concept, batch)
        except Exception:  # noqa: BLE001 —— 判定失败必须降级，不能让整条查询挂掉
            _log.exception("intent 的一批判定失败，按降级处理")
            continue
        # 凭空出现的 symbol_id 丢弃：模型可能编号错乱，不能让它往结果里塞东西
        found.update({sid: v for sid, v in answered.items() if sid in known})
    return found


def _batches(items: Sequence[JudgeItem], size: int) -> list[Sequence[JudgeItem]]:
    return [items[i : i + size] for i in range(0, len(items), size)]
