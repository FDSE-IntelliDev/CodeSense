"""Minimal runner for LLM keyword extraction."""

from __future__ import annotations

import json
from query_processing.llm_keyword_extractor import (
    LLMKeywordExtractor,
    LLMKeywordExtractorConfig,
)


def main() -> None:
    query = "function that performs readahead in disk"

    extractor = LLMKeywordExtractor(
        config=LLMKeywordExtractorConfig(
            model="qwen-plus",
            top_n=6,
            temperature=0.0,
        )
    )
    result = extractor.extract_keywords(query)
    print(json.dumps({"query": query, "keywords": result}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
