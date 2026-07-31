"""自然语言 → 相关词 + 判定标准。

**分工**：模型做 NLP，统计做优化。

模型**提议**，统计**校验**，统计**参数化**——三段，不是二选一。

模型这一步做它擅长的：读懂查询、挑相关的词、按语义分组、
指出查询里有没有「A 相关的代码调用 B 相关的代码」这层意思、
写出一句可判真假的意图。这些都是阅读理解。

但它的提议**不直接生效**：分组要过凝聚度校验（组内的词真落在同一批符号上吗），
关系要过边密度校验（这个关系在这个代码库里成不成立），
种类偏好和执行顺序压根不问它——那些是统计问题
（`codesense.ql.compile.validate` / `build` / `planner`）。

走过两个极端都不对：让模型决定一切，R@100 21%；不让它碰结构，47%
但完全用不上图。**提议是语义问题，校验是统计问题。**

放在 `codesense.llm` 而不是 `codesense.ql`，因为 QL 层按契约只用标准库。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence

from codesense.llm.config import LlmConfig

__all__ = ["PROMPT", "QueryUnderstanding"]

_log = logging.getLogger(__name__)

PROMPT = """\
你在为一个代码检索系统理解查询。

代码库：{project}
查询：{query}

这个代码库里出现过的词（**terms 只能从中挑**）：
{vocab}

输出 JSON：
{{
  "terms": {{"词": 相关度0到1, ...}},
  "groups": {{"组名": ["词1", "词2", ...], ...}},
  "relations": [["组名A", "组名B"], ...],
  "annotations": ["@注解名", ...],
  "concept": "一句话的判定标准，用来逐个判断某段代码算不算答案"
}}

要求：
- terms 挑 15~30 个，全部来自上面的词表，按相关度打分
  （直接指向查询意图的给 0.8~1.0，间接相关的给 0.3~0.6）
- 不要挑 get/set/value 这类通用词
- groups 把 terms 按语义分组。**查询只讲一件事就只给一个组**；
  只有查询确实在讲两件不同的东西时才分（如「性能」和「磁盘」是两件事）
- relations 只在查询确实是「A 相关的代码调用/包含 B 相关的代码」这个意思时才给，
  否则给空列表。**这些提议会用代码库里的实际边去核对，编造的会被丢掉**
- annotations 可以写词表里没有的框架注解，没有就给空列表
- concept 要具体、可判真假，不要复述查询
"""


def _groups(raw: object, terms: dict[str, float]) -> dict[str, list[str]]:
    """模型给的分组，只保留确实在 terms 里的词。"""
    if not isinstance(raw, dict):
        return {}
    found: dict[str, list[str]] = {}
    for name, members in raw.items():
        if not isinstance(members, list):
            continue
        kept = [str(m).lower() for m in members if isinstance(m, str) and str(m).lower() in terms]
        if kept:
            found[str(name)] = kept
    return found


def _relations(raw: object) -> list[tuple[str, str]]:
    if not isinstance(raw, list):
        return []
    found: list[tuple[str, str]] = []
    for item in raw:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            found.append((str(item[0]), str(item[1])))
        elif isinstance(item, dict) and "src" in item and "dst" in item:
            found.append((str(item["src"]), str(item["dst"])))
    return found


def _score(raw: object) -> float:
    try:
        return min(max(float(raw), 0.0), 1.0)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 1.0


class QueryUnderstanding:
    """读懂查询。结构与优化交给 `codesense.ql.compile`。"""

    def __init__(self, config: LlmConfig, session: object | None = None) -> None:
        self._config = config
        self._session = session

    def understand(self, query: str, project: str, vocabulary: Sequence[str]) -> dict | None:
        """读懂查询：挑词、给分、按语义分组、指出关系、写判定标准。

        **这些都是提议**——分组和关系要过 `codesense.ql.compile.validate`
        的统计校验才生效。

        失败返回 None——调用方决定是降级还是放弃。
        """
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
        terms = payload.get("terms")
        if isinstance(terms, list):  # 模型偶尔给列表而不是打分字典
            terms = {str(t): 1.0 for t in terms if isinstance(t, str)}
        if not isinstance(terms, dict) or not terms:
            _log.warning("模型没给出可用的词")
            return None
        scored = {str(k).lower(): _score(v) for k, v in terms.items()}
        return {
            "terms": scored,
            "groups": _groups(payload.get("groups"), scored),
            "relations": _relations(payload.get("relations")),
            "annotations": [a for a in payload.get("annotations", ()) if isinstance(a, str)],
            "concept": str(payload.get("concept") or ""),
        }

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
