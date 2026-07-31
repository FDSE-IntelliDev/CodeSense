"""基于 OpenAI 兼容接口的意图判定器。

实现 `codesense.ql.judge.Judge` 这个端口。QL 层不认识它——
依赖方向是 llm → ql，反过来会破坏 QL「只用标准库」的契约。

用 `requests` 直接打 ``/chat/completions``，不引入 openai SDK：
dashscope、vLLM、Ollama 都兼容这个接口，少一个依赖少一处版本纠纷。
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Sequence

from codesense.llm.config import LlmConfig
from codesense.ql.frag import Verdict
from codesense.ql.judge import UNSURE, Judge, JudgeItem

__all__ = ["PROMPT", "OpenAICompatibleJudge"]

_log = logging.getLogger(__name__)

PROMPT = """\
你在判断代码元素是否满足一个意图。

意图：{concept}

候选元素：
{items}

对每个候选给出判断，只输出 JSON 数组，不要任何其它文字：
[{{"id": <候选的 id>, "label": "yes|no|unsure", "score": <0到1的置信度>, "reason": "<一句话理由>"}}]

要求：
- 判不出来就给 "unsure"，**不要**为了给答案而猜
- reason 要指出具体依据（名字、签名、所在类），不要复述意图
- 每个候选恰好一条，id 必须用上面给的
"""

#: 模型常把 JSON 包在 ```json fence 里，即便要求了不要。
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


class OpenAICompatibleJudge(Judge):
    """打 OpenAI 兼容的 chat/completions 接口。

    ``session`` 可注入，测试时塞个假的就不需要网络。
    """

    def __init__(self, config: LlmConfig, session: object | None = None) -> None:
        self._config = config
        self._session = session

    def judge(self, concept: str, items: Sequence[JudgeItem]) -> dict[int, Verdict]:
        if not items:
            return {}
        content = self._ask(PROMPT.format(concept=concept, items=_render(items)))
        if content is None:
            return {}
        return _parse(content, {item.symbol_id for item in items})

    def _ask(self, prompt: str) -> str | None:
        session = self._session or _default_session()
        try:
            response = session.post(  # type: ignore[attr-defined]
                f"{self._config.base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {self._config.api_key}"},
                json={
                    "model": self._config.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0,
                },
                timeout=self._config.timeout,
            )
            response.raise_for_status()
            return str(response.json()["choices"][0]["message"]["content"])
        except Exception:  # noqa: BLE001 —— 判定失败必须降级，由 intent 决定怎么处理
            # 不要把异常内容写进日志正文：请求头里带着密钥，某些库会把它塞进异常。
            _log.exception("调用判定接口失败")
            return None


def _default_session() -> object:
    import requests

    return requests.Session()


def _render(items: Sequence[JudgeItem]) -> str:
    lines = []
    for item in items:
        parts = [f"id={item.symbol_id}", f"{item.kind} {item.name}"]
        if item.container:
            parts.append(f"位于 {item.container}")
        if item.signature:
            parts.append(f"签名 {item.signature}")
        if item.doc:
            parts.append(f"文档 {item.doc[:200]}")
        lines.append("- " + "；".join(parts))
    return "\n".join(lines)


def _parse(content: str, known: set[int]) -> dict[int, Verdict]:
    """从模型输出里抠出判定。

    容忍 ```json fence 与前后的废话——模型经常不听「只输出 JSON」。
    解析失败返回空，交给 `intent` 走降级，而不是抛异常炸掉整条查询。
    """
    payload = _FENCE.search(content)
    text = payload.group(1) if payload else content
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end <= start:
        _log.warning("判定输出里找不到 JSON 数组")
        return {}
    try:
        rows = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        _log.warning("判定输出不是合法 JSON")
        return {}

    found: dict[int, Verdict] = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        try:
            symbol_id = int(row["id"])
        except (KeyError, TypeError, ValueError):
            continue
        if symbol_id not in known:
            continue  # 模型编号错乱时不能让它往结果里塞东西
        found[symbol_id] = Verdict(
            source="llm",
            label=str(row.get("label") or UNSURE).strip().lower(),
            reason=str(row.get("reason") or ""),
            score=_score(row.get("score")),
        )
    return found


def _score(raw: object) -> float:
    try:
        return min(max(float(raw), 0.0), 1.0)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
