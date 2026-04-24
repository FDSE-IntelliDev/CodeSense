"""Tiny runner for Step A + Step B keyword expansion pipeline."""

from __future__ import annotations

import json
from query_processing.keyword_expansion import ExpansionConfig, KeywordExpander


def main() -> None:
    config = ExpansionConfig(
        symbols_index_path="/Users/huangzhuochen/PycharmProjects/CodeSearch/output/youlai-boot-master/symbols_index.json",
        ngram_index_path="/Users/huangzhuochen/PycharmProjects/CodeSearch/output/youlai-boot-master/ngramed_symbol.json",
        seed_top_k=20,
        expansion_top_n=10,
        model="qwen-plus",
        temperature=0.0,
    )
    expander = KeywordExpander(config)

    query = "function that performs security check"
    base_keywords = [
        {"keyword": "security", "score": 0.91},
        # {"keyword": "disk", "score": 0.52},
    ]

    out = expander.search_and_expansion(query, base_keywords)
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
