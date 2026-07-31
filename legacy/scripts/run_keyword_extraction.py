"""用 KeyBERT 抽一次关键词，人工看看结果对不对。

脚本只做参数解析和调用，抽取逻辑在 codesense.query.keybert_extractor。
"""

from __future__ import annotations

import argparse

from codesense.query import KeyBertExtractor


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("query", nargs="?", default="function that performs readahead in disk")
    p.add_argument("--top-n", type=int, default=6)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    extractor = KeyBertExtractor(top_n=args.top_n)
    result = extractor.extract_keywords(args.query)
    print(f"query: {args.query}")
    print("keywords:")
    for item in result:
        print(f"- {item['keyword']} -> {item['score']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
