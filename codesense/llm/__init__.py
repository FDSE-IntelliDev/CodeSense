"""LLM adapters.

Implements the `codesense.ql.judge.Judge` port. The dependency runs llm to
ql -- the reverse would break QL's standard-library-only contract.
"""

from codesense.llm.codegen import OPERATOR_SPEC, ScriptGenerator
from codesense.llm.compiler import QueryUnderstanding
from codesense.llm.config import DEFAULT_BASE_URL, DEFAULT_MODEL, LlmConfig, find_api_key
from codesense.llm.judge import OpenAICompatibleJudge

__all__ = [
    "DEFAULT_BASE_URL",
    "DEFAULT_MODEL",
    "LlmConfig",
    "OpenAICompatibleJudge",
    "OPERATOR_SPEC",
    "QueryUnderstanding",
    "ScriptGenerator",
    "find_api_key",
]
