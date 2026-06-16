"""Query preprocessing package for CodeSearch."""

__all__ = [
    "KeyBertExtractor",
    "LLMKeywordExtractor",
    "LLMKeywordExtractorConfig",
]


def __getattr__(name):
    if name == "KeyBertExtractor":
        from .keybert_extractor import KeyBertExtractor

        return KeyBertExtractor
    if name in {"LLMKeywordExtractor", "LLMKeywordExtractorConfig"}:
        from .llm_keyword_extractor import LLMKeywordExtractor, LLMKeywordExtractorConfig

        return {
            "LLMKeywordExtractor": LLMKeywordExtractor,
            "LLMKeywordExtractorConfig": LLMKeywordExtractorConfig,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
