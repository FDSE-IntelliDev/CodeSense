"""The `intent` operator: the part of a fragment that satisfies an intent.

**The only operator that calls an LLM at query time, and the most
expensive.** The three disciplines from ``docs/design/05-operators.md`` are
all enforced in code here:

1. It runs last, on the smallest fragment -- exceeding `max_items` raises
   rather than quietly spending money, forcing the caller to narrow with
   cheap constraints first.
2. Verdicts go into the evidence with a reason, or a user has no basis for
   trusting the result.
3. It must degrade -- when the LLM is unavailable `fallback` decides what
   happens, and the query does not fail.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from codesense.ql.context import EvalContext
from codesense.ql.frag import Element, Evidence, Frag, Verdict
from codesense.ql.judge import UNSURE, JudgeItem, item_of

__all__ = ["DEFAULT_MAX_ITEMS", "FALLBACKS", "intent"]

_log = logging.getLogger(__name__)

#: Ceiling on candidates handed to the LLM. Exceeding it raises: leaving
#: cheap constraints unused ahead of it is an orchestration error, and money
#: should not paper over it.
DEFAULT_MAX_ITEMS = 200

#: What to do when nothing could be decided.
#:
#:     keep   keep it (favours recall; err towards showing more)
#:     drop   discard it (favours precision)
#:     error  fail outright (when silent degradation is unacceptable)
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
    """Decide which elements really do what ``concept`` describes.

    ``concept`` is prose, usually taken straight from a unit's ``concept``.
    """
    if fallback not in FALLBACKS:
        raise ValueError(f"fallback must be one of {FALLBACKS}, got {fallback!r}")
    if not frag:
        return frag
    if max_items is not None and len(frag) > max_items:
        raise ValueError(
            f"intent received {len(frag)} candidates, over max_items={max_items}. "
            "It is the most expensive operator and belongs last, on the smallest "
            "fragment -- narrow with hop / only / top first, or raise max_items "
            "explicitly."
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
                raise RuntimeError(f"symbol {symbol_id} could not be judged, fallback='error'")
            if fallback == "drop":
                continue
            verdict = Verdict(
                source="fallback", label=UNSURE, reason="judging unavailable; kept by fallback"
            )
        elif not _passes(verdict, threshold):
            continue
        kept[symbol_id] = element
        evidence[symbol_id] = frag.evidence_for(symbol_id).merge(Evidence(verdicts=(verdict,)))

    if undecided:
        _log.warning(
            "intent left %d/%d candidates undecided; applying fallback=%r",
            undecided,
            len(frag),
            fallback,
        )
    return Frag(
        nodes=kept,
        edges={key: e for key, e in frag.edges.items() if key[0] in kept and key[1] in kept},
        evidence=evidence,
        witnesses=tuple(p for p in frag.witnesses if all(n in kept for n in p.nodes)),
    )


def _passes(verdict: Verdict, threshold: float) -> bool:
    """A yes verdict whose confidence clears the threshold.

    `unsure` does not pass: undecided and decided-yes are different, and the
    first belongs on the fallback path rather than waved through.
    """
    return verdict.label == _YES and verdict.score >= threshold


def _collect(frag: Frag, concept: str, ctx: EvalContext, batch_size: int) -> dict[int, Verdict]:
    if batch_size < 1:
        raise ValueError(f"batch_size must be positive, got {batch_size}")
    items = [item_of(element) for element in frag.nodes.values()]
    found: dict[int, Verdict] = {}
    known = set(frag.nodes)
    for batch in _batches(items, batch_size):
        try:
            answered = ctx.judge.judge(concept, batch)
        except Exception:  # noqa: BLE001 -- judging must degrade, not kill the query
            _log.exception("a batch of intent judging failed; degrading")
            continue
        # Drop ids nobody asked about: a confused model must not inject results
        found.update({sid: v for sid, v in answered.items() if sid in known})
    return found


def _batches(items: Sequence[JudgeItem], size: int) -> list[Sequence[JudgeItem]]:
    return [items[i : i + size] for i in range(0, len(items), size)]
