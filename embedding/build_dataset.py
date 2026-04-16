"""Build documents and train pairs from symbols/call/dependency JSON indexes."""

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List

from negative_sampling import NegativeSampler
from schema import SymbolDocument, TrainPair, build_symbol_id
from definition import OUTPUT_DIR,EMBEDDING_DIR

def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_jsonl(path: Path, rows: List[Dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def flatten_symbol_text(doc: SymbolDocument) -> str:
    parts = [
        f"name: {doc.name}",
        f"type: {doc.type}",
        f"language: {doc.language}",
        f"file: {doc.file}",
        f"container: {doc.container}",
        f"signature: {doc.signature}",
        f"doc: {doc.doc}",
        f"callees: {', '.join(doc.neighbors.get('callees', []))}",
        f"callers: {', '.join(doc.neighbors.get('callers', []))}",
        f"deps: {', '.join(doc.deps)}",
    ]
    return " | ".join(parts)


def build_documents(symbols: List[Dict], calls: List[Dict], deps: List[Dict]) -> List[SymbolDocument]:
    callees_map = defaultdict(list)
    callers_map = defaultdict(list)
    dep_map = defaultdict(set)

    for edge in calls:
        caller = edge.get("caller", "")
        callee = edge.get("callee", "")
        caller_file = edge.get("caller_file", "")
        callee_file = edge.get("callee_file", "")

        caller_key = f"{caller_file}::{caller}"
        callee_key = f"{callee_file}::{callee}"

        if callee:
            callees_map[caller_key].append(callee)
        if caller:
            callers_map[callee_key].append(caller)

    for item in deps:
        source = item.get("source_file", "")
        target = item.get("target_file", "")
        if source and target:
            dep_map[source].add(target)

    docs: List[SymbolDocument] = []

    for symbol in symbols:
        file_path = symbol.get("file", "")
        name = symbol.get("name", "")
        line_range = symbol.get("range", {}) or {}
        start_line = line_range.get("start_line", -1)
        end_line = line_range.get("end_line", -1)

        symbol_id = build_symbol_id(file_path, name, start_line, end_line)
        key = f"{file_path}::{name}"

        doc = SymbolDocument(
            symbol_id=symbol_id,
            name=name,
            type=symbol.get("type", ""),
            language=symbol.get("language", ""),
            file=file_path,
            container=symbol.get("container", ""),
            signature=symbol.get("signature", ""),
            doc=symbol.get("doc", ""),
            start_line=start_line if isinstance(start_line, int) else -1,
            end_line=end_line if isinstance(end_line, int) else -1,
            neighbors={
                "callees": sorted(set(callees_map.get(key, []))),
                "callers": sorted(set(callers_map.get(key, []))),
            },
            deps=sorted(dep_map.get(file_path, set())),
        )
        doc.text = flatten_symbol_text(doc)
        docs.append(doc)

    return docs


def build_query_templates(doc: SymbolDocument) -> List[str]:
    queries: List[str] = []
    if doc.name:
        queries.append(f"find function {doc.name}")
        queries.append(f"implementation of {doc.name}")
    if doc.signature:
        queries.append(f"code with signature {doc.signature}")
    if doc.doc:
        cleaned = doc.doc.strip()
        if cleaned:
            queries.append(cleaned)
    if doc.container and doc.name:
        queries.append(f"{doc.container} {doc.name} logic")
    return queries


def build_train_pairs(docs: List[SymbolDocument]) -> List[TrainPair]:
    sampler = NegativeSampler(docs)
    pairs: List[TrainPair] = []

    for i, doc in enumerate(docs):
        queries = build_query_templates(doc)
        if not queries:
            continue

        negative_indices = sampler.sample(i, n_easy=1, n_medium=1, n_hard=2)
        negative_ids = [docs[idx].symbol_id for idx in negative_indices]

        for j, q in enumerate(queries):
            pairs.append(
                TrainPair(
                    query_id=f"q_{i:06d}_{j}",
                    query_text=q,
                    positive_symbol_id=doc.symbol_id,
                    negative_symbol_ids=negative_ids,
                    meta={"source": "template", "hardness": "mixed"},
                )
            )

    return pairs


def main() -> None:
    parser = argparse.ArgumentParser(description="Build dataset for dual-encoder code search training")
    parser.add_argument("--project-root", type=str, required=True)
    parser.add_argument("--symbols", type=str, required=True)
    parser.add_argument("--calls", type=str, required=True)
    parser.add_argument("--deps", type=str, required=True)
    parser.add_argument("--out-dir", type=str, default="embedding/data")
    args = parser.parse_args([
        "--project-root", "CodeSearch/embedding",
        "--symbols", f"{OUTPUT_DIR}/youlai-boot-master/symbols_index.json",
        "--calls", f"{OUTPUT_DIR}/youlai-boot-master/call_graph.json",
        "--deps", f"{OUTPUT_DIR}/youlai-boot-master/dependency_graph.json",
        "--out-dir", f"{EMBEDDING_DIR}/embedding/data",
    ])

    symbols = read_json(Path(args.symbols))
    calls = read_json(Path(args.calls))
    deps = read_json(Path(args.deps))

    docs = build_documents(symbols, calls, deps)
    pairs = build_train_pairs(docs)

    out_dir = Path(args.out_dir)
    write_jsonl(out_dir / "documents.jsonl", [d.to_dict() for d in docs])
    write_jsonl(out_dir / "train_pairs.jsonl", [p.to_dict() for p in pairs])

    manifest = {
        "project_root": args.project_root,
        "num_documents": len(docs),
        "num_train_pairs": len(pairs),
        "files": {
            "documents": str(out_dir / "documents.jsonl"),
            "train_pairs": str(out_dir / "train_pairs.jsonl"),
        },
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
