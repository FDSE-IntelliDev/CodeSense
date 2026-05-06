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

def save_res(save_path,data):
    with open(save_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def process_online(query: str):
    print(f"=== [Online] Processing Search Query ===")
    print(f"User Query: '{query}'")

    # Extract query DSL via LLM
    print("Extracting query DSL using LLM...")
    extractor = LLMKeywordExtractor()
    dsl_result = extractor.extract_keywords(query)

    print("\n--- Extracted Query DSL ---")
    print(json.dumps(dsl_result, indent=2, ensure_ascii=False))
    save_res("./output/query_dsl_result.json",dsl_result)
    print("---------------------------\n")
    return dsl_result

def main():
    parser = argparse.ArgumentParser(description="CodeSearch Pipeline")
    parser.add_argument("--project_path", type=str, help="Path to the target codebase")
    parser.add_argument("--output_dir", type=str, help="Directory to save the parsing and indexing results")
    parser.add_argument("--query", type=str, help="A natural language search query for the online phase")

    args = parser.parse_args([
        "--project_path", "/Users/huangzhuochen/IdeaProjects/youlai-boot-master",
        "--output", "./output/youlai-boot-master",
        "--query", "function that performs security check"
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
