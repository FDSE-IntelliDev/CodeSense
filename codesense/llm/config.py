"""LLM 配置。

**参数走流水线，密钥走环境。** 端点、模型名、超时都是普通参数，由调用方
显式传进来（脚本的命令行参数 → 构造函数），不从配置文件读——
配置文件会变成第二处事实来源，"到底生效的是哪个值"就说不清了。

只有 API key 例外：它不能当命令行参数（会进 shell 历史和进程列表），
所以从环境变量 `CODESENSE_API_KEY` 读，回落到未被跟踪的 `config.yml`。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = ["DEFAULT_BASE_URL", "DEFAULT_MODEL", "LlmConfig"]

#: dashscope 的 OpenAI 兼容端点。换供应商改这里或在配置里覆盖。
DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL = "qwen-plus"

#: 环境变量优先于配置文件——CI 与容器里通常只有环境变量。
ENV_API_KEY = "CODESENSE_API_KEY"

_KEY_ALIASES = ("api-key", "api_key", "apikey", "key")


@dataclass(frozen=True, slots=True)
class LlmConfig:
    api_key: str
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    timeout: float = 60.0

    def __post_init__(self) -> None:
        if not self.api_key:
            raise ValueError(
                f"没有 API key。设环境变量 {ENV_API_KEY}，"
                "或在未被跟踪的 config.yml 里配 LLM.api-key。"
            )

    @classmethod
    def load(cls, *, secrets_file: Path | str = "config.yml", **params: Any) -> LlmConfig:
        """构造配置：参数由调用方给，密钥自动找。

        ``params`` 里能传 `base_url` / `model` / `timeout`——它们是流水线参数，
        应当一路从命令行传下来。密钥不在 ``params`` 里，除非显式覆盖。
        """
        api_key = params.pop("api_key", None) or find_api_key(secrets_file)
        return cls(api_key=api_key, **params)

    def __repr__(self) -> str:
        """永远不打印密钥。

        它会出现在日志、异常栈、`pytest -v` 的输出里——
        默认 repr 会把密钥泄进这些地方，而那正是最容易被复制粘贴出去的地方。
        """
        return f"LlmConfig(model={self.model!r}, base_url={self.base_url!r}, api_key=<已隐藏>)"


def find_api_key(secrets_file: Path | str = "config.yml") -> str:
    """环境变量优先，回落到未被跟踪的配置文件。"""
    return os.environ.get(ENV_API_KEY, "").strip() or _find_key(_read(Path(secrets_file)))


def _read(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    import yaml

    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    section = loaded.get("LLM") or loaded.get("llm") or {}
    if isinstance(section, list):
        merged: dict[str, Any] = {}
        for entry in section:
            if isinstance(entry, dict):
                merged.update(entry)
        return merged
    return section if isinstance(section, dict) else {}


def _find_key(settings: dict[str, Any]) -> str:
    for alias in _KEY_ALIASES:
        value = settings.get(alias)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""
