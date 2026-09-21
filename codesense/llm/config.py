"""LLM configuration.

**Parameters travel through the pipeline, secrets travel through the
environment.** Endpoint, model name and timeout are ordinary parameters,
passed in explicitly by the caller (script command line to constructor)
rather than read from a config file -- a config file becomes a second source
of truth and "which value is actually in effect" stops being answerable.

The API key is the one exception: it cannot be a command-line argument (it
would land in shell history and the process list), so it is read from the
`CODESENSE_API_KEY` environment variable, falling back to the untracked
`config.yml`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = ["DEFAULT_BASE_URL", "DEFAULT_MODEL", "LlmConfig"]

#: dashscope's OpenAI-compatible endpoint. Change here or override in config
#: to switch provider.
DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL = "qwen3.7-plus"

#: The environment wins over the config file -- in CI and containers the
#: environment is usually all there is.
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
                f"no API key; set the {ENV_API_KEY} environment variable, "
                "or put LLM.api-key in the untracked config.yml."
            )

    @classmethod
    def load(cls, *, secrets_file: Path | str = "config.yml", **params: Any) -> LlmConfig:
        """Build a config: parameters from the caller, the key found
        automatically.

        ``params`` accepts `base_url` / `model` / `timeout` -- pipeline
        parameters that should be threaded down from the command line. The
        key is not in ``params`` unless explicitly overridden.
        """
        api_key = params.pop("api_key", None) or find_api_key(secrets_file)
        return cls(api_key=api_key, **params)

    def __repr__(self) -> str:
        """Never print the key.

        This shows up in logs, exception tracebacks and `pytest -v` output --
        the default repr would leak the key into exactly the places most
        likely to be copy-pasted elsewhere.
        """
        return f"LlmConfig(model={self.model!r}, base_url={self.base_url!r}, api_key=<hidden>)"


def find_api_key(secrets_file: Path | str = "config.yml") -> str:
    """The environment wins, falling back to the untracked config file."""
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
