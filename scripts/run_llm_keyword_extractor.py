"""跑一次 LLM 关键词抽取，把结果追加到一个 JSON 文件里。

脚本只做参数解析和调用，抽取逻辑在 codesense.query.llm_keyword_extractor。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from codesense.config import REPO_ROOT, load_config
from codesense.query.llm_keyword_extractor import (
    LLMKeywordExtractor,
    LLMKeywordExtractorConfig,
)

DEFAULT_QUERIES = REPO_ROOT / "data" / "dsl_samples" / "query.json"
DEFAULT_RESULTS = REPO_ROOT / "data" / "dsl_samples" / "extracted_results.json"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--queries", type=Path, default=DEFAULT_QUERIES, help="查询集 JSON")
    p.add_argument("--category", default="feature_localization", help="查询集里的分类名")
    p.add_argument("--index", type=int, default=4, help="取该分类下第几条查询")
    p.add_argument("--out", type=Path, default=DEFAULT_RESULTS, help="结果追加到哪个文件")
    p.add_argument("--top-n", type=int, default=6)
    p.add_argument("--temperature", type=float, default=0.0)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    cfg = load_config()

    queries = json.loads(args.queries.read_text(encoding="utf-8"))["queries"]
    query = queries[args.category][args.index]

    extractor = LLMKeywordExtractor(
        config=LLMKeywordExtractorConfig(
            model=cfg.llm.model,
            top_n=args.top_n,
            temperature=args.temperature,
        )
    )
    result = extractor.extract_keywords(query)

    results = []
    if args.out.exists():
        results = json.loads(args.out.read_text(encoding="utf-8"))
    results.append(result)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(results, ensure_ascii=False, indent=4), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
