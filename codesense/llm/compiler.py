"""自然语言 → 相关词 + 判定标准。

**分工**：模型做 NLP，统计做优化。

模型这一步做的是它擅长的：读懂查询，从项目词表里挑出相关的词，
写出一句可判真假的意图。它**不决定**分几个单元、偏好什么种类、
什么执行顺序——那些是统计问题，答案在索引里（`df`、posting 分布），
不在模型脑子里。实测模型在这几件事上判得很差：查询问「实体字段上的
校验约束」，它给的 kinds 里偏偏没有 `field`，把答案全筛没了。

这和 MySQL 优化器同一个道理：优化器不问用户怎么 join，它查统计信息。

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
  "annotations": ["@注解名", ...],
  "concept": "一句话的判定标准，用来逐个判断某段代码算不算答案"
}}

要求：
- terms 挑 15~30 个，全部来自上面的词表，按相关度打分
  （直接指向查询意图的给 0.8~1.0，间接相关的给 0.3~0.6）
- 不要挑 get/set/value 这类通用词
- annotations 可以写词表里没有的框架注解，没有就给空列表
- concept 要具体、可判真假，不要复述查询
"""


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
        """读懂查询：挑词、给分、写判定标准。**不决定任何结构。**

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
        return {
            "terms": {str(k).lower(): _score(v) for k, v in terms.items()},
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
