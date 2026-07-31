"""意图判定的**端口**。

`intent` 是执行期唯一调 LLM 的算子，也是最贵的。但 QL 层按契约只用标准库，
所以这里只定义接口，真正调模型的适配器在 `codesense.llm`。

这样带来两个好处：QL 的测试不需要网络也不需要 API key；
换模型、换供应商不动算子一行代码。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass

from codesense.ql.frag import Element, Verdict

__all__ = ["Judge", "JudgeItem", "NullJudge", "UNSURE", "item_of"]

#: 判定不出来时的标签。**不是 "no"**——判不出和判为否是两回事，
#: 前者应该走降级策略，后者应该直接筛掉。
UNSURE = "unsure"


@dataclass(frozen=True, slots=True)
class JudgeItem:
    """交给模型判定的一个候选。

    只带模型真正用得上的字段。不传 `symbol_id` 之外的内部标识，
    也不传整段源码——判定的是「这个元素是不是在做某件事」，
    签名和文档通常就够，塞进源码只会让 token 成本失控。
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
    """判定一批元素是否满足某个意图。

    按批而不是按个：一次调用判五个，token 与延迟都摊薄了，
    而且模型看到同批的其它候选后判得更稳。
    """

    @abstractmethod
    def judge(self, concept: str, items: Sequence[JudgeItem]) -> dict[int, Verdict]:
        """返回 symbol_id → 判定。

        允许少返回——没返回的候选按 `UNSURE` 处理，走降级策略。
        **不允许多返回**：凭空出现的 symbol_id 会被调用方丢弃。
        """


class NullJudge(Judge):
    """什么都判不出来的判定器。

    默认注入它而不是 `None`，这样 `intent` 的降级路径在**没有配 LLM 的环境里
    也会被真正走到**——如果降级有 bug，测试就会发现，而不是等到线上。
    """

    def judge(self, concept: str, items: Sequence[JudgeItem]) -> dict[int, Verdict]:
        return {}
