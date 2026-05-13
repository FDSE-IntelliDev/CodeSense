"""Minimal runner for LLM keyword extraction."""

from __future__ import annotations

import json
from query_processing.llm_keyword_extractor import (
    LLMKeywordExtractor,
    LLMKeywordExtractorConfig,
)
from definition import BASE_MODEL


def main() -> None:
    with open('/Users/huangzhuochen/PycharmProjects/CodeSearch/DSL/query.json','r') as f:
        query = json.load(f)['queries']["feature_localization"][0]

    extractor = LLMKeywordExtractor(
        config=LLMKeywordExtractorConfig(
            model=BASE_MODEL,
            top_n=6,
            temperature=0.0,
        )
    )
    result = extractor.extract_keywords(query)
    import os
    results=[]
    if os.path.exists('/Users/huangzhuochen/PycharmProjects/CodeSearch/DSL/extracted_results.json'):
        with open('/Users/huangzhuochen/PycharmProjects/CodeSearch/DSL/extracted_results.json','r') as f:
           results= json.load(f)
    results.append(result)
    with open('/Users/huangzhuochen/PycharmProjects/CodeSearch/DSL/extracted_results.json','w') as f:
        json.dump(results,f,ensure_ascii=False, indent=4)
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
