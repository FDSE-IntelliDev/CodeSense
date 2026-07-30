"""用倒排索引 + 缩写扩展跑一次符号检索。

    python -m scripts.run_invert_index_search
    python -m scripts.run_invert_index_search --semql xxx.json

只有接线。匹配逻辑在 codesense.search.invert_index_search。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from codesense.config import load_config
from codesense.search.full_term_matcher import FullTermMatcher
from codesense.search.invert_index_search import (
    _has_requested_conditions,
    _map_subtokens_to_symbols,
)


def invert_index_search4symbol(
    invert_index_path: str,
    ngramed_symbol_path: str,
    query_dsl_result_path: str,
    properties: tuple = ("include",),
) -> list:
    """
    根据倒排索引和拆词符号进行检索，返回匹配的完整代码元素。

    properties: which property groups to match ("include",) or ("exclude",).
    """
    # 1. 从文件读取查询条件 (semQL)
    with open(query_dsl_result_path, encoding="utf-8") as f:
        semQL = json.load(f)

    # 请求的 include/exclude 没有任何条件时，无需加载索引和执行匹配。
    if not _has_requested_conditions(semQL, properties):
        return []

    # 2. 初始化匹配器
    matcher = FullTermMatcher(
        invert_index_path=invert_index_path,
        ngramed_symbol_path=ngramed_symbol_path,
    )

    # 3. 执行匹配
    result = matcher.match_ngram(semQL, properties)
    matched_subtokens = result.get("matched_subtokens", {})

    # 4. 读取 ngramed_symbols 以便查找完整信息
    with open(ngramed_symbol_path, encoding="utf-8") as f:
        ngramed_symbols = json.load(f)

    # 5. 从 ngramed_symbols 中过滤出完整的代码元素信息。
    return _map_subtokens_to_symbols(ngramed_symbols, matched_subtokens)["symbols"]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--semql", type=Path, default=None, help="默认 semQL.json")
    p.add_argument(
        "--properties",
        nargs="+",
        default=["include"],
        help="取 include 还是 exclude 条件",
    )
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    cfg = load_config()
    project_dir = cfg.project_output_dir

    elements = invert_index_search4symbol(
        invert_index_path=str(project_dir / "invert_index.json"),
        ngramed_symbol_path=str(project_dir / "ngramed_symbol.json"),
        query_dsl_result_path=str(args.semql or cfg.query_output_dir() / "semQL.json"),
        properties=tuple(args.properties),
    )
    print(json.dumps(elements, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
