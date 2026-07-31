"""按 relation_semql.json 对 Surface 候选做结构关系过滤。

    python -m scripts.run_relation \\
        output/<project>/query_1/relation_semql.json \\
        output/<project>/query_1/filtered_by_type.json

这里只有接线：读计划、读候选、造执行器、调一下、写产物。
真正的执行逻辑在 codesense.executors.relation_executor.RelationExecutor，
因为那里能被 import、能被测试。
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from codesense.config import load_config
from codesense.executors.relation_executor import RelationExecutor
from codesense.utils.file_utils import load_res, save_res


def default_relation_result_path() -> str:
    """默认产物位置。产物布局属于边界层的知识，所以住在这里，不在核心包。"""
    return str(load_config().query_output_dir() / "filtered_by_relation.json")


def run_relation_executor(
    relation_plan_path: str,
    surface_search_result_path: str,
    output_path: str | None = None,
    layer: int | None = 1,
    worker_count: int = 4,
) -> list[dict[str, Any]]:
    """读计划与候选、跑一遍关系执行器、按需落盘。

    ``output_path`` 传 None 表示不保存，只返回结果。
    """
    relation_plan = load_res(relation_plan_path)
    candidates = load_res(surface_search_result_path)
    if not isinstance(candidates, list):
        candidates = []

    executor = RelationExecutor(
        candidate_path=surface_search_result_path,
        layer=layer,
        worker_count=worker_count,
    )
    try:
        results = executor.execute(relation_plan, candidates)
    finally:
        executor.close()

    if output_path:
        save_res(output_path, results)
    return results


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("relation_plan_path", help="relation_semql.json")
    p.add_argument("surface_search_result_path", help="Surface 阶段的候选集 JSON")
    p.add_argument("--output", default=None, help="产物路径，默认 filtered_by_relation.json")
    p.add_argument("--layer", type=int, default=1)
    p.add_argument("--worker-count", type=int, default=4)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    results = run_relation_executor(
        relation_plan_path=args.relation_plan_path,
        surface_search_result_path=args.surface_search_result_path,
        output_path=args.output or default_relation_result_path(),
        layer=args.layer,
        worker_count=args.worker_count,
    )
    print(f"{len(results)} 条 -> {Path(args.output or default_relation_result_path())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
