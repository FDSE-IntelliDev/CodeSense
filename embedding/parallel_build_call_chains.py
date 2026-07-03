import os
import sys
import json
import time
from typing import List, Dict
from multiprocessing import Pool

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from definition import PROJECT_OUTPUT_DIR, PROJECT_PATH
from parsers.parallel_java_lsp_client import ParallelJavaLSPClient, ParallelJavaCallChainExtractor
from parsers.read_tools import get_symbol_code


def flatten_call_tree(node: dict, current_chain: List[Dict], all_chains: List[List[Dict]]):
	"""Traverse the callees_tree to build flattened root-to-leaf paths."""
	callees = node.get("callees_tree", [])
	if not callees:
		all_chains.append(list(current_chain))
		return
	for child in callees:
		callee_info = child.get("callee", {})
		func_name = callee_info.get("name")
		if any(c.get("func_name") == func_name for c in current_chain):
			all_chains.append(list(current_chain))
			continue
		chain_item = {"func_name": func_name, "code": ""}
		current_chain.append(chain_item)
		flatten_call_tree(child, current_chain, all_chains)
		current_chain.pop()


def load_symbols() -> List[Dict]:
	symbols_file = f"{PROJECT_OUTPUT_DIR}/symbols_index.json"
	with open(symbols_file, "r", encoding="utf-8") as f:
		return json.load(f)


def process_chunk(worker_id: int, methods_chunk: List[Dict]) -> List[str]:
	"""Worker proc: start isolated LSP, extract call chains for its chunk."""
	print(f"[Worker {worker_id}] starting chunk with {len(methods_chunk)} methods")
	lsp_client = ParallelJavaLSPClient(project_root=PROJECT_PATH, jdtls_path="jdtls", verbose=False, worker_id=str(worker_id))
	lsp_client.start()
	# warm up
	time.sleep(8)
	extractor = ParallelJavaCallChainExtractor(lsp_client)
	local_results: List[List[Dict]] = []
	part_files: List[str] = []
	part_id = 0
	out_dir = PROJECT_OUTPUT_DIR
	os.makedirs(out_dir, exist_ok=True)
	tmp_dir=f"{PROJECT_OUTPUT_DIR}/tmp"
	os.makedirs(tmp_dir, exist_ok=True)

	try:
		total = len(methods_chunk)
		for idx, func in enumerate(methods_chunk):
			func_name = func.get("name")
			filepath = func.get("file")
			if not func_name or not filepath:
				continue
			# ensure types for static checkers
			filepath = str(filepath)
			func_name = str(func_name)
			if idx % 10 == 0:
				print(f"[Worker {worker_id}] processing {idx}/{total}: {func_name}")
			try:
				chain_res = extractor.get_call_chain(filepath, func_name, layer=None)
			except Exception as e:
				print(f"[Worker {worker_id}] extractor error for {func_name}: {e}")
				continue
			if "error" in chain_res:
				continue
			callers_tree = chain_res.get("callers_tree", [])
			if callers_tree:
				# not an entry point
				continue
			func_code = get_symbol_code(PROJECT_PATH, func) or ""
			target_info = chain_res.get("target", {})
			root_item = {"func_name": target_info.get("name", func_name), "code": func_code}
			func_chains: List[List[Dict]] = []
			flatten_call_tree(chain_res, [root_item], func_chains)
			local_results.extend(func_chains)

			if len(local_results) >= 50:
				part_file = f"{tmp_dir}/word2vec_call_chains_w{worker_id}_p{part_id}.json"
				with open(part_file, "w", encoding="utf-8") as f:
					json.dump(local_results, f, ensure_ascii=False, indent=2)
				part_files.append(part_file)
				part_id += 1
				local_results = []

		if local_results:
			part_file = f"{tmp_dir}/word2vec_call_chains_w{worker_id}_p{part_id}.json"
			with open(part_file, "w", encoding="utf-8") as f:
				json.dump(local_results, f, ensure_ascii=False, indent=2)
			part_files.append(part_file)
			local_results = []
	finally:
		lsp_client.stop()
		print(f"[Worker {worker_id}] stopped, extracted {len(part_files)} parts")
	return part_files


def run_parallel(num_workers: int = 4):
	symbols = load_symbols()
	methods = [s for s in symbols if s.get("type") in ["method", "function"] and s.get("language") == "java"]
	methods = [s for s in methods if s.get("file", "").startswith(PROJECT_PATH)]
	print(f"Total candidate methods: {len(methods)}")

	if not methods:
		print("No methods to process.")
		return

	# distribute round-robin to balance chunk sizes
	chunks: List[List[Dict]] = [[] for _ in range(num_workers)]
	for i, m in enumerate(methods):
		chunks[i % num_workers].append(m)

	worker_args = [(i, chunks[i]) for i in range(num_workers) if chunks[i]]

	all_part_files: List[str] = []
	start = time.time()
	with Pool(processes=len(worker_args)) as pool:
		results = pool.starmap(process_chunk, worker_args)
	for r in results:
		all_part_files.extend(r)
	end = time.time()
	print(f"Parallel extraction done in {end-start:.1f}s, merging parts...")

	out_dir = PROJECT_OUTPUT_DIR
	os.makedirs(out_dir, exist_ok=True)
	out_file = f"{out_dir}/word2vec_call_chains.json"

	total_chains = 0
	with open(out_file, "w", encoding="utf-8") as out_f:
		out_f.write("[\n")
		first = True
		for pf in all_part_files:
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
	print(f"Saved {total_chains} total chains to {out_file}")


if __name__ == "__main__":
	# adjust workers according to machine capacity
	workers = int(os.environ.get("PARALLEL_WORKERS", "4"))
	run_parallel(num_workers=workers)
