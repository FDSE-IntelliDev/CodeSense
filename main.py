import os
import argparse
import json
from pathlib import Path

# Offline parsing imports
from code_parser import run as run_code_parser
from ngram_split import SymbolNgramer
from invert_index import InvertedIndexBuilder

# Online query processing imports
from query_processing.llm_semCon_extractor import LLMSemConExtractor
from query_processing.semQL_composer import (
    compose_query_plans_from_semCon,
    compose_semQL_from_semCon,
)
from executor.surface_executor import run_surface_search
from executor.relation_executor import run_relation_executor
from executor.intention_executor import executor as run_intention_executor

from definition import PROJECT_OUTPUT_DIR, QUERY_ID, get_query_output_dir
from utils.file_utils import save_res,load_res


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
    output_dir: str = PROJECT_OUTPUT_DIR,
    query_id: int = QUERY_ID,
):
    """Run the online query pipeline.

    Args:
        query: natural-language user query.
        output_dir: project-level output/index directory, e.g.
            ``output/<project>``. Offline artifacts such as ``symbols_index.json``
            and ``invert_index.json`` are read from here.
        query_id: per-query id used to isolate online intermediate outputs under
            ``output/<project>/query_<query_id>``.
    """
    project_output_path = Path(output_dir)
    output_path = Path(get_query_output_dir(str(project_output_path), query_id))
    output_path.mkdir(parents=True, exist_ok=True)

    semQL_path = str(output_path / "semQL.json")
    semCon_path = str(output_path / "semCon.json")
    query_plan_path = str(output_path / "query_plan.json")
    surface_semQL_path = str(output_path / "surface_semql.json")
    relation_semQL_path = str(output_path / "relation_semql.json")
    intention_semQL_path = str(output_path / "intention_semql.json")
    surface_result_path = str(output_path / "filtered_by_type_hop_0.json")
    surface_evidence_path = str(output_path / "surface_evidence_hop_0.json")
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
    surface_results = run_surface_search(
        surface_plan_path=surface_semQL_path,
        output_dir=str(output_path),
        project_output_dir=str(project_output_path),
    )
    print(f"  -> Surface results: {len(surface_results)} candidates saved to {surface_result_path}")

    print("\n[4/5] Running Relation Executor ...")
    relation_results = run_relation_executor(
        semQL_path=semQL_path,
        surface_search_result_path=surface_result_path,
        output_path=relation_result_path,
    )
    print(f"  -> Relation results: {len(relation_results)} candidates saved to {relation_result_path}")

    print("\n[5/5] Running Intention Executor ...")
    final_results = run_intention_executor(
        semQL_path=semQL_path,
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

def main():
    parser = argparse.ArgumentParser(description="CodeSearch Pipeline")
    parser.add_argument("--init",action="store_true",help="Initialize the project before searching")
    parser.add_argument("--project_path", type=str, help="Path to the target codebase")
    parser.add_argument("--output_dir", type=str, help="Directory to save the parsing and indexing results")
    parser.add_argument("--query", type=str, help="A natural language search query for the online phase")
    parser.add_argument("--query_id", type=int, default=QUERY_ID, help="Query id used for output/<project>/query_<id> online artifacts")

    args = parser.parse_args(
        [
            "--project_path","/Users/bytedance/old6ma/projects/youlai-boot-master",
            "--output_dir","/Users/bytedance/old6ma/CodeSearch/output/youlai-boot-master",
            "--query","Find the entry function that handles user login authentication.",
            "--query_id","1"
        ]
    )

    if args.init:
        process_offline(args.project_path, args.output_dir)
    else:
        print("Skipping offline phase.")

    if args.query:
        process_online(
            args.query,
            output_dir=args.output_dir or PROJECT_OUTPUT_DIR,
            query_id=args.query_id,
        )
    else:
        print("Skipping online search because no --query provided.")

if __name__ == "__main__":
    main()
