"""对 Surface 候选做一次语义聚类过滤。

    python -m scripts.run_cluster
    python -m scripts.run_cluster --input xxx.json --plan yyy.json --threshold 0.3

只有接线：读候选、读计划、造 embedder/clusterer、跑一遍、写两个产物。
聚类逻辑在 codesense.filters.cluster_pipeline。

注意 --threshold 的默认值取自计划里的 cluster policy，不再像原来那样
在脚本里写死 0.3——写死的那份和 policy 里的同名字段长期不一致。
"""

from __future__ import annotations

import argparse
from pathlib import Path

from codesense.config import load_config
from codesense.filters.cluster_pipeline import (
    CodeEmbedder,
    FiltrationDispatcher,
    SymbolClusterer,
)
from codesense.utils.file_utils import load_res, save_res


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--input", type=Path, default=None, help="候选集，默认 filtered_by_type.json")
    p.add_argument("--plan", type=Path, default=None, help="intention_semql.json")
    p.add_argument("--out-dir", type=Path, default=None, help="产物目录")
    p.add_argument(
        "--threshold",
        type=float,
        default=None,
        help="聚类距离阈值；默认取计划里 cluster.distance_threshold",
    )
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    query_dir = load_config().query_output_dir()

    candidates = load_res(str(args.input or query_dir / "filtered_by_type.json"))
    plan = load_res(str(args.plan or query_dir / "intention_semql.json"))
    policy = plan.get("execution_plan", {}).get("cluster", {})
    query_text = plan.get("query_profile", {}).get("semantic_text", "")
    threshold = args.threshold
    if threshold is None:
        threshold = float(policy.get("distance_threshold", 0.3))

    print(f"候选 {len(candidates)} 条，聚类阈值 {threshold}")
    print("正在加载本地 Embedding 模型 ...")
    dispatcher = FiltrationDispatcher(CodeEmbedder(), SymbolClusterer(distance_threshold=threshold))
    result = dispatcher.run_pipeline(candidates, query_text, policy)

    out_dir = args.out_dir or query_dir
    save_res(str(out_dir / "filtered_by_cluster.json"), result.get("kept", []))
    save_res(str(out_dir / "exclude_by_cluster.json"), result.get("discarded", []))

    stats = result.get("stats", {})
    print(
        f"初始 {stats.get('total_initial')} | 聚类数 {stats.get('num_clusters')} | "
        f"保留 {stats.get('total_kept')} | 丢弃 {stats.get('total_discarded')}"
    )
    print(f"-> {out_dir / 'filtered_by_cluster.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
