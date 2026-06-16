import os
import argparse
import json
from pathlib import Path

# Offline parsing imports
from code_parser import run as run_code_parser
from ngram_split import SymbolNgramer
from invert_index import InvertedIndexBuilder

# Online query processing imports
from query_processing.llm_keyword_extractor import LLMKeywordExtractor
from query_processing.llm_semCon_extractor import LLMSemConExtractor
from query_processing.semQL_composer import compose_semQL_from_semCon
from search.invert_index_search import invert_index_search4symbol

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


def process_online(query: str):
    invert_index_path=f'{OUTPUT_DIR}/{PROJECT_NAME}/invert_index.json'
    ngramed_symbol_path=f'{OUTPUT_DIR}/{PROJECT_NAME}/ngramed_symbol.json'
    semQL_path=f'{OUTPUT_DIR}/{PROJECT_NAME}/semQL.json'
    semCon_path=f'{OUTPUT_DIR}/{PROJECT_NAME}/semCon.json'
    invert_index_search_result_path=f'{OUTPUT_DIR}/{PROJECT_NAME}/invert_index_search_result.json'

    print(f"=== [Online] Processing Search Query ===")
    print(f"User Query: '{query}'")

    # Extract query DSL via LLM
    # print("Extracting query DSL using LLM...")
    # extractor = LLMKeywordExtractor()
    # dsl_result = extractor.extract_keywords(query)
    # print("\n--- Extracted Query DSL ---")
    # print(json.dumps(dsl_result, indent=2, ensure_ascii=False))
    # save_res(semQL_path,dsl_result)

    # print("Extracting SemCon using LLM...")
    # extractor = LLMSemConExtractor()
    # semCon_result = extractor.extract_semCon(query)
    # print("\n--- Extracted SemCon ---")
    # print(json.dumps(semCon_result, indent=2, ensure_ascii=False))
    # save_res(semCon_path, semCon_result)
    #
    # semQL_result = compose_semQL_from_semCon(semCon_result, raw_query=query)
    # print("\n--- Composed SemQL ---")
    # print(json.dumps(semQL_result, indent=2, ensure_ascii=False))
    # save_res(semQL_path, semQL_result)

    print("\n--- Inverted Index Search ---")
    search_results = invert_index_search4symbol(invert_index_path=invert_index_path,ngramed_symbol_path=ngramed_symbol_path,query_dsl_result_path=semQL_path)
    save_res(invert_index_search_result_path,search_results)
    print(f"Search Results: {len(search_results)} matched elements found.")
    print("---------------------------\n")
    # return dsl_result

def main():
    parser = argparse.ArgumentParser(description="CodeSearch Pipeline")
    parser.add_argument("--project_path", type=str, help="Path to the target codebase")
    parser.add_argument("--output_dir", type=str, help="Directory to save the parsing and indexing results")
    parser.add_argument("--query", type=str, help="A natural language search query for the online phase")

    args = parser.parse_args([
        # "--project_path", PROJECT_PATH,
        # "--output", f"{OUTPUT_DIR}/youlai-boot-master",
        "--query", "Find the entry function that handles user login authentication"
    ])

    if args.project_path and args.output_dir:
        process_offline(args.project_path, args.output_dir)
    else:
        print("Skipping offline parsing because --project_path or --output_dir missing.")

    if args.query:
        process_online(args.query)
    else:
        print("Skipping online search because no --query provided.")

main()
