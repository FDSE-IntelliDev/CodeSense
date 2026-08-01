"""在符号表上做一次正则/关键词检索。

脚本只做参数解析和调用，检索逻辑在 codesense.search.regex_search。
"""

from __future__ import annotations

import argparse
import json

from codesense.config import load_config
from codesense.search.regex_search import search_symbols_by_keywords


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("keywords", nargs="+", help="要搜的关键词")
    p.add_argument("--mode", choices=["and", "or"], default="and")
    p.add_argument("--case-sensitive", action="store_true")
    p.add_argument("--word-boundary", action="store_true")
    p.add_argument("--limit", type=int, default=50)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    cfg = load_config()

    result = search_symbols_by_keywords(
        symbols_index_path=str(cfg.project_output_dir / "symbols_index.json"),
        keywords=args.keywords,
        mode=args.mode,
        case_sensitive=args.case_sensitive,
        use_word_boundary=args.word_boundary,
        limit=args.limit,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
