import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

sys.path.append(str(Path(__file__).resolve().parent.parent))

from definition import OUTPUT_DIR, PROJECT_NAME


def normalize_func_name(name: Any) -> str:
    """Normalize LSP display names like `foo(String) : void` to `foo`."""
    text = str(name or "").strip()
    if not text:
        return ""
    text = text.split(":", 1)[0].strip()
    text = text.split("(", 1)[0].strip()
    text = text.split(".", 1)[-1].strip()
    return text


def build_symbol_name_set(symbols: List[Dict[str, Any]]) -> Set[str]:
    names: Set[str] = set()
    for symbol in symbols:
        raw_name = str(symbol.get("name") or "").strip()
        raw_signature = str(symbol.get("signature") or "").strip()
        if raw_name:
            names.add(raw_name)
            names.add(normalize_func_name(raw_name))
        if raw_signature:
            names.add(raw_signature)
            names.add(normalize_func_name(raw_signature))
    return {name for name in names if name}


def filter_call_chains(
    call_chains: List[List[Dict[str, Any]]],
    symbol_names: Set[str],
) -> Tuple[List[List[Dict[str, Any]]], Dict[str, Any]]:
    kept: List[List[Dict[str, Any]]] = []
    removed = 0
    missing_counter: Dict[str, int] = {}

    for chain in call_chains:
        missing: List[str] = []
        for item in chain:
            raw_name = item.get("func_name", "")
            normalized = normalize_func_name(raw_name)
            if raw_name not in symbol_names and normalized not in symbol_names:
                missing.append(str(raw_name or ""))

        if missing:
            removed += 1
            for name in missing:
                missing_counter[name] = missing_counter.get(name, 0) + 1
            continue
        kept.append(chain)

    top_missing = sorted(missing_counter.items(), key=lambda item: (-item[1], item[0]))[:50]
    summary = {
        "input_chains": len(call_chains),
        "kept_chains": len(kept),
        "removed_chains": removed,
        "unique_missing_functions": len(missing_counter),
        "top_missing_functions": top_missing,
    }
    return kept, summary


def default_paths(project_name: str = PROJECT_NAME) -> Dict[str, str]:
    base = Path(OUTPUT_DIR) / project_name
    return {
        "symbols": str(base / "symbols_index.json"),
        "input": str(base / "word2vec_call_chains.json"),
        "output": str(base / "word2vec_call_chains.symbol_filtered.json"),
        "summary": str(base / "word2vec_call_chains.symbol_filtered.summary.json"),
    }


def main() -> None:
    paths = default_paths()
    parser = argparse.ArgumentParser(description="Remove call chains containing functions absent from symbols_index.json.")
    parser.add_argument("--symbols", default=paths["symbols"], help="Path to symbols_index.json.")
    parser.add_argument("--input", default=paths["input"], help="Path to word2vec_call_chains.json.")
    parser.add_argument("--output", default=paths["output"], help="Filtered call-chain output path.")
    parser.add_argument("--summary", default=paths["summary"], help="Filtering summary output path.")
    args = parser.parse_args()

    symbols = json.loads(Path(args.symbols).read_text(encoding="utf-8"))
    call_chains = json.loads(Path(args.input).read_text(encoding="utf-8"))
    symbol_names = build_symbol_name_set(symbols)
    filtered, summary = filter_call_chains(call_chains, symbol_names)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(filtered, ensure_ascii=False, indent=2), encoding="utf-8")

    summary_path = Path(args.summary)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({**summary, "output": str(output_path), "summary": str(summary_path)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
