"""自然语言 → 查询规格。

编译器唯一需要 LLM 的一步。产出的是 `QuerySpec`（结构化中间表示），
**不是可执行代码**——顺序由 `codesense.ql.compile.planner` 按预估代价决定，
不由模型决定。模型不知道 `buffer` 在这个项目里命中 2365 个符号，规划器知道。

放在 `codesense.llm` 而不是 `codesense.ql`，因为 QL 层按契约只用标准库。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence

from codesense.llm.config import LlmConfig
from codesense.ql.compile.spec import QuerySpec

__all__ = ["PROMPT", "SpecCompiler"]

_log = logging.getLogger(__name__)

PROMPT = """\
你在把一条代码检索需求拆成结构化的查询规格。

代码库：{project}
查询：{query}

这个代码库里出现过的词（**只能从中挑 terms**）：
{vocab}

输出 JSON：
{{
  "units": [
    {{"name": "简短英文名", "concept": "这个槽位在找什么，一句中文",
      "terms": ["词1", ...], "annotations": ["@注解名", ...], "modifiers": ["static", ...]}}
  ],
  "graph": [{{"src": "单元名", "dst": "单元名", "hops": [1, 2]}}],
  "concept": "整条查询的语义判定标准，一句中文",
  "kinds": ["method", "class"]
}}

要求：
- **拆成 2~4 个单元**，每个单元是一个独立的语义槽位（如「缓冲区」「磁盘」「性能」），
  不要把所有词堆进一个单元——单元之间的关系要靠 graph 表达
- terms 必须来自上面的词表，每个单元 5~15 个
- 只在「A 相关的代码调用/包含 B 相关的代码」这种意思成立时才写 graph
- annotations 可以写词表里没有的框架注解
- concept 是最后交给模型逐个判定用的，要具体、可判真假
"""


class SpecCompiler:
    """把自然语言编译成查询规格。"""

    def __init__(self, config: LlmConfig, session: object | None = None) -> None:
        self._config = config
        self._session = session

    def compile(self, query: str, project: str, vocabulary: Sequence[str]) -> QuerySpec | None:
        """编译。失败返回 None——调用方决定是降级还是放弃。"""
        content = self._ask(
            PROMPT.format(project=project, query=query, vocab=", ".join(vocabulary))
        )
        if content is None:
            return None
        start, end = content.find("{"), content.rfind("}")
        if start < 0 or end <= start:
            _log.warning("编译输出里找不到 JSON")
            return None
        try:
            payload = json.loads(content[start : end + 1])
        except json.JSONDecodeError:
            _log.warning("编译输出不是合法 JSON")
            return None
        payload.setdefault("query", query)
        try:
            return QuerySpec.from_dict(payload)
        except (ValueError, KeyError, TypeError) as exc:
            _log.warning("编译产出的规格不合法: %s", exc)
            return None

    def _ask(self, prompt: str) -> str | None:
        session = self._session
        if session is None:
            import requests

            session = requests.Session()
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
        except Exception:  # noqa: BLE001 —— 编译失败要能降级，不该炸掉整条查询
            _log.exception("调用编译接口失败")
            return None
