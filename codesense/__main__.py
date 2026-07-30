import os
import argparse
import json
from pathlib import Path

# Offline parsing imports
from codesense.indexing.code_parser import run as run_code_parser
from codesense.indexing.ngram_split import SymbolNgramer
from codesense.indexing.invert_index import InvertedIndexBuilder

# Online query processing imports
from codesense.query.llm_semCon_extractor import LLMSemConExtractor
from codesense.query.semQL_composer import (
    compose_query_plans_from_semCon,
    compose_semQL_from_semCon,
)
from codesense.executors.surface_executor import run_surface_search
from codesense.executors.intention_executor import run_intention_executor
# Relation 阶段的接线搬到了 scripts/run_relation.py（核心包只留 RelationExecutor）
from scripts.run_relation import run_relation_executor

from codesense.config import load_config
from codesense.utils.file_utils import save_res,load_res


def process_offline(project_path: str, output_dir: str):
    print(f"=== [Offline] Starting parsing for project: {project_path} ===")

    # 1. Parse code to get symbols_index, call_graph, dependency_graph
    print("1/3 Running code_parser...")
    run_code_parser(project_path, output_dir)
    print("code_parser finished.")

    # 2. Extract ngrams from symbols
    symbols_index_path = os.path.join(output_dir, "symbols_index.json")
    ngramed_symbol_path = os.path.join(output_dir, "ngramed_symbol.json")
    print("2/3 Running ngram_split...")
    ngramer = SymbolNgramer(
        symbols_index_path=symbols_index_path,
        output_path=ngramed_symbol_path
    )
    ngramer.build_ngramed_symbol()
    print("ngram_split finished.")

    # 3. Build inverted index
    invert_index_path = os.path.join(output_dir, "invert_index.json")
    print("3/3 Running invert_index...")
    builder = InvertedIndexBuilder(
        symbols_index_path=ngramed_symbol_path,
        output_path=invert_index_path
    )
    builder.build_invert_index()
    print("invert_index finished.")
    print("=== [Offline] Indexing completed successfully ===\n")


def process_online(
    query: str,
    output_dir: str | None = None,
    query_id: int | None = None,
):
    """Run the online query pipeline.

    Args:
        query: natural-language user query.
        output_dir: project-level output/index directory, e.g.
            ``output/<project>``. Offline artifacts such as ``symbols_index.json``
            and ``invert_index.json`` are read from here. Defaults to the
            configured project output dir.
        query_id: per-query id used to isolate online intermediate outputs under
            ``output/<project>/query_<query_id>``. Defaults to ``query.default_id``.
    """
    cfg = load_config()
    project_output_path = Path(output_dir) if output_dir else cfg.project_output_dir
    qid = cfg.query.default_id if query_id is None else query_id
    output_path = project_output_path / f"query_{qid}"
    output_path.mkdir(parents=True, exist_ok=True)

    semQL_path = str(output_path / "semQL.json")
    semCon_path = str(output_path / "semCon.json")
    query_plan_path = str(output_path / "query_plan.json")
    surface_semQL_path = str(output_path / "surface_semql.json")
    relation_semQL_path = str(output_path / "relation_semql.json")
    intention_semQL_path = str(output_path / "intention_semql.json")
    surface_result_path = str(output_path / "filtered_by_type.json")
    surface_evidence_path = str(output_path / "surface_evidence_hop.json")
    surface_group_search_result_path = str(
        output_path / "surface_group_search_results.json"
    )
    relation_result_path = str(output_path / "filtered_by_relation.json")
    intention_result_path = str(output_path / "intention_executor_result.json")

    print(f"=== [Online] Processing Search Query ===")
    print(f"User Query: '{query}'")

    print("\n[1/5] Extracting SemCon using LLM ...")
    # extractor = LLMSemConExtractor()
    # semCon_result = extractor.extract_semCon(query)
    # save_res(semCon_path, semCon_result)
    semCon_result=load_res(semCon_path)
    print(f"  -> SemCon saved to {semCon_path}")

    print("\n[2/5] Planning domain SemQL ...")
    query_plans = compose_query_plans_from_semCon(semCon_result, raw_query=query)
    save_res(surface_semQL_path, query_plans.surface.to_dict())
    save_res(relation_semQL_path, query_plans.relation.to_dict())
    save_res(intention_semQL_path, query_plans.intention.to_dict())
    save_res(
        query_plan_path,
        query_plans.manifest(
            raw_query=query,
            surface_path=Path(surface_semQL_path).name,
            relation_path=Path(relation_semQL_path).name,
            intention_path=Path(intention_semQL_path).name,
        ),
    )
    print(f"  -> Surface plan saved to {surface_semQL_path}")
    print(f"  -> Relation plan saved to {relation_semQL_path}")
    print(f"  -> Intention plan saved to {intention_semQL_path}")
    print(f"  -> Query plan manifest saved to {query_plan_path}")

    # Compatibility output: existing executors still read the combined plan.
    # semQL_result = compose_semQL_from_semCon(semCon_result, raw_query=query)
    # save_res(semQL_path, semQL_result)
    # print(f"  -> Legacy combined SemQL saved to {semQL_path}")

    print("\n[3/5] Running Surface Executor ...")
    # surface_results = run_surface_search(
    #     surface_plan_path=surface_semQL_path,
    #     output_dir=str(output_path),
    #     project_output_dir=str(project_output_path),
    # )
    # print(f"  -> Surface results: {len(surface_results)} candidates saved to {surface_result_path}")

    print("\n[4/5] Running Relation Executor ...")
    # relation_results = run_relation_executor(
    #     relation_plan_path=relation_semQL_path,
    #     surface_search_result_path=surface_result_path,
    #     output_path=relation_result_path,
    # )
    # print(f"  -> Relation results: {len(relation_results)} candidates saved to {relation_result_path}")

    print("\n[5/5] Running Intention Executor ...")
    final_results = run_intention_executor(
        intention_plan_path=intention_semQL_path,
        relation_executor_result_path=relation_result_path,
        output_path=intention_result_path,
    )
    print(f"  -> Final results: {len(final_results)} candidates saved to {intention_result_path}")
    print("=== [Online] Pipeline completed ===\n")

    return {
        "semCon_path": semCon_path,
        "semQL_path": semQL_path,
        "query_plan_path": query_plan_path,
        "surface_semQL_path": surface_semQL_path,
        "relation_semQL_path": relation_semQL_path,
        "intention_semQL_path": intention_semQL_path,
        "surface_result_path": surface_result_path,
        "surface_evidence_path": surface_evidence_path,
        "surface_group_search_result_path": surface_group_search_result_path,
        "relation_result_path": relation_result_path,
        "intention_result_path": intention_result_path,
        "project_output_dir": str(project_output_path),
        "query_output_dir": str(output_path),
        "final_results": final_results,
    }

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="codesense", description="CodeSense Pipeline")
    parser.add_argument("--config", type=str, help="Path to a config YAML (default: configs/default.yaml)")
    parser.add_argument("--init",action="store_true",help="Initialize the project before searching")
    parser.add_argument("--project_path", type=str, help="Path to the target codebase (default: target.project_path)")
    parser.add_argument("--output_dir", type=str, help="Directory to save the parsing and indexing results")
    parser.add_argument("--query", type=str, help="A natural language search query for the online phase")
    parser.add_argument("--query_id", type=int, help="Query id used for output/<project>/query_<id> online artifacts")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)

    # 命令行参数优先，没给的从配置里取——参数不写死在代码里（ARCHITECTURE.md 规则 7）。
    cfg = load_config(args.config) if args.config else load_config()
    project_path = args.project_path or str(cfg.project_path)
    output_dir = args.output_dir or str(cfg.project_output_dir)

    if args.init:
        process_offline(project_path, output_dir)
    else:
        print("Skipping offline phase.")

    if args.query:
        process_online(
            args.query,
            output_dir=output_dir,
            query_id=args.query_id,
        )
    else:
        print("Skipping online search because no --query provided.")

    return 0

if __name__ == "__main__":
    raise SystemExit(main())
