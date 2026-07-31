"""按 SemQL 做一次精确代码搜索（code_element / code_line）。

    python -m scripts.run_exact_search
    python -m scripts.run_exact_search --semql output/<p>/query_1/semQL.json

只有接线。搜索逻辑在 codesense.search.exact_code_search.ExactCodeSearcher。

> 这个入口原来是核心包里的 `exact_code_search(symbols_index_path, semql_path)`，
> 142 行里有 135 行是**硬编码的 semql 字面量**——它先读 `semql_path`，
> 然后一行 `semql = {...}` 把读到的东西整个覆盖掉，那个路径参数其实是摆设。
> 现在那份样例存成了 `data/dsl_samples/exact_search_sample_semql.json`，
> 不传 `--semql` 时用它，行为和以前一致；传了就真的按你给的文件走。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from codesense.config import REPO_ROOT, load_config
from codesense.search.exact_code_search import ExactCodeSearcher
from codesense.utils.file_utils import load_res

SAMPLE_SEMQL = REPO_ROOT / "data" / "dsl_samples" / "exact_search_sample_semql.json"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--semql",
        type=Path,
        default=None,
        help=f"SemQL JSON；不给则用样例 {SAMPLE_SEMQL.name}",
    )
    p.add_argument("--symbols", type=Path, default=None, help="默认 symbols_index.json")
    p.add_argument("--top-k", type=int, default=50)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    cfg = load_config()

    semql_path = args.semql or SAMPLE_SEMQL
    if not Path(semql_path).exists():
        print(f"SemQL 文件不存在：{semql_path}")
        return 1
    semql = load_res(str(semql_path))

    symbols_path = args.symbols or cfg.project_output_dir / "symbols_index.json"
    results = ExactCodeSearcher(str(symbols_path)).search(semql, top_k=args.top_k)
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
