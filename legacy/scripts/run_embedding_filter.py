"""对聚类后的候选做一次 term-level embedding 过滤。

    python -m scripts.run_embedding_filter
    python -m scripts.run_embedding_filter --input xxx.json --plan yyy.json

只有接线。过滤逻辑在 codesense.filters.embedding_filter.EmbeddingFilter。
"""

from __future__ import annotations

import argparse
from pathlib import Path

from codesense.config import load_config
from codesense.filters.embedding_filter import run_embedding_filter
from codesense.utils.file_utils import load_res, save_res


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--input", type=Path, default=None, help="默认 filtered_by_cluster.json")
    p.add_argument("--plan", type=Path, default=None, help="intention_semql.json")
    p.add_argument("--output", type=Path, default=None, help="默认 filtered_by_embedding.json")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    query_dir = load_config().query_output_dir()

    candidates = load_res(str(args.input or query_dir / "filtered_by_cluster.json"))
    plan = load_res(str(args.plan or query_dir / "intention_semql.json"))

    result = run_embedding_filter(
        candidates,
        plan.get("query_profile", {}),
        plan.get("execution_plan", {}).get("embedding", {}),
    )

    output = args.output or query_dir / "filtered_by_embedding.json"
    save_res(str(output), result)
    buckets = {k: len(v) for k, v in result.items() if isinstance(v, list)}
    print(f"{buckets} -> {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
