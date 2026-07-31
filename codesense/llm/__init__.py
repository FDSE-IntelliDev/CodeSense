"""LLM 适配器。

实现 `codesense.ql.judge.Judge` 这个端口。依赖方向是 llm → ql——
反过来会破坏 QL「只用标准库」的契约。
"""

from codesense.llm.config import DEFAULT_BASE_URL, DEFAULT_MODEL, LlmConfig, find_api_key
from codesense.llm.judge import OpenAICompatibleJudge

__all__ = [
    "DEFAULT_BASE_URL",
    "DEFAULT_MODEL",
    "LlmConfig",
    "OpenAICompatibleJudge",
    "find_api_key",
]
