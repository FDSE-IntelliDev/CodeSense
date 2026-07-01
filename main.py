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
from query_processing.semQL_composer import compose_semQL_from_semCon
from executor.surface_executor import run_surface_search
from executor.relation_executor import run_relation_executor
from executor.intention_executor import executor as run_intention_executor

from definition import OUTPUT_DIR,PROJECT_PATH,PROJECT_NAME
from utils.file_utils import save_res


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


def process_online(query: str, output_dir: str = f"{OUTPUT_DIR}/{PROJECT_NAME}"):
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    semQL_path = str(output_path / "semQL.json")
    semCon_path = str(output_path / "semCon.json")
    surface_result_path = str(output_path / "filtered_by_type.json")
    relation_result_path = str(output_path / "filtered_by_relation.json")
    intention_result_path = str(output_path / "intention_executor_result.json")

    print(f"=== [Online] Processing Search Query ===")
    print(f"User Query: '{query}'")

    print("\n[1/5] Extracting SemCon using LLM ...")
    extractor = LLMSemConExtractor()
    semCon_result = extractor.extract_semCon(query)
    save_res(semCon_path, semCon_result)
    print(f"  -> SemCon saved to {semCon_path}")

    print("\n[2/5] Composing SemQL from SemCon ...")
    semQL_result = compose_semQL_from_semCon(semCon_result, raw_query=query)
    save_res(semQL_path, semQL_result)
    print(f"  -> SemQL saved to {semQL_path}")

    print("\n[3/5] Running Surface Executor ...")
    surface_results = run_surface_search(output_dir=output_dir)
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
        "surface_result_path": surface_result_path,
        "relation_result_path": relation_result_path,
        "intention_result_path": intention_result_path,
        "final_results": final_results,
    }

def main():
    parser = argparse.ArgumentParser(description="CodeSearch Pipeline")
    parser.add_argument("--project_path", type=str, help="Path to the target codebase")
    parser.add_argument("--output_dir", type=str, help="Directory to save the parsing and indexing results")
    parser.add_argument("--query", type=str, help="A natural language search query for the online phase")

    args = parser.parse_args()

    if args.project_path and args.output_dir:
        process_offline(args.project_path, args.output_dir)
    else:
        print("Skipping offline parsing because --project_path or --output_dir missing.")

    if args.query:
        process_online(args.query, output_dir=args.output_dir or f"{OUTPUT_DIR}/{PROJECT_NAME}")
    else:
        print("Skipping online search because no --query provided.")

if __name__ == "__main__":
    main()
