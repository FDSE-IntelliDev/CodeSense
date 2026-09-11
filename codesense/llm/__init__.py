"""LLM adapters.

Implements the `codesense.ql.judge.Judge` port. The dependency runs llm to
ql -- the reverse would break QL's standard-library-only contract.
"""

from codesense.llm.codegen import OPERATOR_SPEC, ScriptGenerator
from codesense.llm.compiler import QueryUnderstanding
from codesense.llm.config import DEFAULT_BASE_URL, DEFAULT_MODEL, LlmConfig, find_api_key
from codesense.llm.judge import OpenAICompatibleJudge
from codesense.llm.schema import (
    EndpointKind,
    QueryRelation,
    QueryUnderstandingResult,
    RelationEndpoint,
    RelationKind,
    SemanticTerm,
    SemanticUnit,
    TargetKind,
    TermSource,
    query_understanding_response_format,
)

__all__ = [
    "DEFAULT_BASE_URL",
    "DEFAULT_MODEL",
    "EndpointKind",
    "LlmConfig",
    "OpenAICompatibleJudge",
    "OPERATOR_SPEC",
    "QueryRelation",
    "QueryUnderstanding",
    "QueryUnderstandingResult",
    "RelationEndpoint",
    "RelationKind",
    "ScriptGenerator",
    "SemanticTerm",
    "SemanticUnit",
    "TargetKind",
    "TermSource",
    "find_api_key",
    "query_understanding_response_format",
]
