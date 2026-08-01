"""The **port** for intent judging.

`intent` is the only operator that calls an LLM at query time, and the most
expensive one. The QL layer is contractually standard-library only, so this
defines the interface and the adapter that actually calls a model lives in
`codesense.llm`.

Two benefits: QL tests need neither network nor API key, and swapping model
or provider touches no operator code.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass

from codesense.ql.frag import Element, Verdict

__all__ = ["Judge", "JudgeItem", "NullJudge", "UNSURE", "item_of"]

#: Label for "could not decide". **Not "no"** -- undecided and decided-against
#: are different: the first should take the fallback path, the second should
#: simply be filtered out.
UNSURE = "unsure"


@dataclass(frozen=True, slots=True)
class JudgeItem:
    """One candidate handed to the model.

    Carries only what the model can use. No internal identifiers beyond
    `symbol_id`, and no source body -- the question is whether an element
    does a certain thing, for which signature and doc usually suffice, and
    including bodies would put token cost out of control.
    """

    symbol_id: int
    name: str
    kind: str
    signature: str = ""
    doc: str = ""
    container: str = ""


def item_of(element: Element) -> JudgeItem:
    return JudgeItem(
        symbol_id=element.symbol_id,
        name=element.name,
        kind=element.kind,
        signature=element.signature,
        doc=element.doc,
        container=element.container,
    )


class Judge(ABC):
    """Decide whether a batch of elements satisfies an intent.

    Batched rather than one at a time: five per call amortises tokens and
    latency, and seeing its peers makes the model's judgements steadier.
    """

    @abstractmethod
    def judge(self, concept: str, items: Sequence[JudgeItem]) -> dict[int, Verdict]:
        """Return symbol_id to verdict.

        Returning fewer is allowed -- anything missing is treated as `UNSURE`
        and takes the fallback path. Returning **more** is not: symbol ids
        that were never asked about are discarded by the caller.
        """


class NullJudge(Judge):
    """A judge that decides nothing.

    Injected by default instead of `None`, so `intent`'s fallback path is
    **genuinely exercised in environments without an LLM** -- a bug there
    surfaces in tests rather than in production.
    """

    def judge(self, concept: str, items: Sequence[JudgeItem]) -> dict[int, Verdict]:
        return {}
