import os
import sys
import json
import time
from typing import List, Dict
from codesense.config import PROJECT_OUTPUT_DIR, PROJECT_PATH
from codesense.parsers.java_lsp_client import JavaLSPClient, JavaCallChainExtractor
from codesense.parsers.read_tools import get_symbol_code


def flatten_call_tree(node: dict, current_chain: List[Dict], all_chains: List[List[Dict]]):
    """
    Traverse the callees_tree to build flattened root-to-leaf paths.
    """
    callees = node.get("callees_tree", [])
    if not callees:
        # leaf node
        all_chains.append(list(current_chain))
        return
    for child in callees:
        callee_info = child.get("callee", {})
        func_name = callee_info.get("name")
        # Check cycles to prevent infinite recursion
        if any(c.get("func_name") == func_name for c in current_chain):
            all_chains.append(list(current_chain))
            continue
        file_uri = callee_info.get("uri", "")
        file_path = file_uri.replace("file://", "")
        chain_item = {
            "func_name": func_name,
            "code": "" # To be augmented next if needed
        }
        current_chain.append(chain_item)
        flatten_call_tree(child, current_chain, all_chains)
        current_chain.pop()


def load_symbols() -> List[Dict]:
    symbols_file = f"{PROJECT_OUTPUT_DIR}/symbols_index.json"
    with open(symbols_file, "r", encoding="utf-8") as f:
        return json.load(f)


def run():
    print(f"Project path: {PROJECT_PATH}")
    symbols = load_symbols()
    # Filter methods only
    methods = [s for s in symbols if s.get("type") in ["method", "function"] and s.get("language") == "java"]
    # Further filter out library methods, only keep those in the project root
    methods = [s for s in methods if s.get("file", "").startswith(PROJECT_PATH)]
    print(f"Total methods snippet elements: {len(methods)}")
    lsp_client = JavaLSPClient(project_root=PROJECT_PATH, jdtls_path="jdtls", verbose=False)
    print("Starting LSP Server... waiting 10 seconds for initialization.")
    lsp_client.start()
    time.sleep(10)
    extractor = JavaCallChainExtractor(lsp_client)
    result_set = []
    part_id = 0
    part_files = []
    out_dir = PROJECT_OUTPUT_DIR
    os.makedirs(out_dir, exist_ok=True)
    tmp_dir = f"{out_dir}/tmp"
    os.makedirs(tmp_dir, exist_ok=True)
    try:
        total = len(methods)
        for idx, func in enumerate(methods):
            func_name = func.get("name")
            filepath = func.get("file")

            if func.get("type") not in ["method", "function"]:
                continue

            if idx % 10 == 0:
                print(f"Scanning {idx}/{total}: {func_name}")
            try:
                # Need call chain with enough depth. Layer=None extracts until no more callers/callees
                chain_res = extractor.get_call_chain(filepath, func_name, layer=None)
            except Exception as e:
                print(f"Failed extracting {func_name}: {e}")
                continue
            if "error" in chain_res:
                continue
            callers_tree = chain_res.get("callers_tree", [])
            if callers_tree:
                # This function is called by others, not an entry point.
                continue
            func_code = get_symbol_code(PROJECT_PATH, func) or ""
            target_info = chain_res.get("target", {})
            # Root function item
            root_item = {
                "func_name": target_info.get("name", func_name),
                "code": func_code
            }
            # Extract paths starting from the entry point
            func_chains = []
            flatten_call_tree(chain_res, [root_item], func_chains)
            result_set.extend(func_chains)

            if len(result_set) >= 100:
                part_file = f"{tmp_dir}/word2vec_call_chains_part_{part_id}.json"
                with open(part_file, "w", encoding="utf-8") as f:
                    json.dump(result_set, f, ensure_ascii=False, indent=2)
                part_files.append(part_file)
                part_id += 1
                result_set = []

        if result_set:
            part_file = f"{tmp_dir}/word2vec_call_chains_part_{part_id}.json"
            with open(part_file, "w", encoding="utf-8") as f:
                json.dump(result_set, f, ensure_ascii=False, indent=2)
            part_files.append(part_file)
            result_set = []
    finally:
        lsp_client.stop()
        print("LSP Server stopped.")

    out_file = f"{out_dir}/word2vec_call_chains.json"
    print(f"Merging {len(part_files)} partial files into {out_file}...")
    total_chains = 0
    with open(out_file, "w", encoding="utf-8") as out_f:
        out_f.write("[\n")
        first = True
        for pf in part_files:
            with open(pf, "r", encoding="utf-8") as in_f:
                part_data = json.load(in_f)
                for item in part_data:
                    if not first:
                        out_f.write(",\n")
                    json.dump(item, out_f, ensure_ascii=False)
                    first = False
                    total_chains += 1
            os.remove(pf)
        out_f.write("\n]\n")
    print(f"Done. Extracted {total_chains} isolated path chains. Output saved to {out_file}")


if __name__ == "__main__":
    run()
