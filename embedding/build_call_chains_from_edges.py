import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Dict, List, Set

sys.path.append(str(Path(__file__).resolve().parent.parent))

from definition import OUTPUT_DIR, PROJECT_NAME, PROJECT_PATH
from parsers.read_tools import get_symbol_code


def _load_symbols(conn: sqlite3.Connection) -> Dict[int, Dict]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT symbol_id, name, type, file,
               start_line, end_line, name_line, name_col,
               signature, language, doc, container
        FROM code_symbols
        """
    ).fetchall()
    symbols: Dict[int, Dict] = {}
    for row in rows:
        symbols[int(row["symbol_id"])] = {
            "symbol_id": row["symbol_id"],
            "name": row["name"],
            "type": row["type"],
            "file": row["file"],
            "range": {"start_line": row["start_line"], "end_line": row["end_line"]},
            "name_pos": [row["name_line"], row["name_col"]],
            "signature": row["signature"] or "",
            "language": row["language"] or "",
            "doc": row["doc"] or "",
            "container": row["container"] or "",
        }
    return symbols


def _load_adjacency(conn: sqlite3.Connection) -> Dict[int, List[int]]:
    rows = conn.execute(
        """
        SELECT
          source_symbol_id,
          CASE
            WHEN impl_resolution_status = 'resolved' AND target_impl_symbol_id IS NOT NULL
            THEN target_impl_symbol_id
            ELSE target_symbol_id
          END AS resolved_target_symbol_id
        FROM code_edges
        WHERE kind = 'calls'
        ORDER BY source_symbol_id, call_line, call_col, resolved_target_symbol_id
        """
    ).fetchall()
    adjacency: Dict[int, List[int]] = {}
    for source_id, target_id in rows:
        adjacency.setdefault(int(source_id), [])
        if int(target_id) not in adjacency[int(source_id)]:
            adjacency[int(source_id)].append(int(target_id))
    return adjacency


def _entrypoints(adjacency: Dict[int, List[int]]) -> List[int]:
    sources = set(adjacency)
    targets = {target for targets in adjacency.values() for target in targets}
    return sorted(source for source in sources if source not in targets and adjacency.get(source))


def _chain_item(project_root: str, symbol: Dict) -> Dict:
    func_name = symbol.get("signature") or symbol.get("name", "")
    return {
        "func_name": func_name,
        "code": get_symbol_code(project_root, symbol) or "",
    }


def _walk_chains(
    node_id: int,
    adjacency: Dict[int, List[int]],
    symbols: Dict[int, Dict],
    project_root: str,
    current: List[Dict],
    result: List[List[Dict]],
    visited: Set[int],
    max_depth: int,
) -> None:
    if len(current) >= max_depth:
        result.append(list(current))
        return

    children = [child for child in adjacency.get(node_id, []) if child in symbols]
    if not children:
        result.append(list(current))
        return

    for child in children:
        if child in visited:
            result.append(list(current))
            continue
        current.append(_chain_item(project_root, symbols[child]))
        visited.add(child)
        _walk_chains(child, adjacency, symbols, project_root, current, result, visited, max_depth)
        visited.remove(child)
        current.pop()


def build_call_chains_from_edges(db_path: str, project_root: str, max_depth: int = 20) -> List[List[Dict]]:
    conn = sqlite3.connect(db_path)
    try:
        symbols = _load_symbols(conn)
        adjacency = _load_adjacency(conn)
        chains: List[List[Dict]] = []
        for entry_id in _entrypoints(adjacency):
            if entry_id not in symbols:
                continue
            root = _chain_item(project_root, symbols[entry_id])
            _walk_chains(
                node_id=entry_id,
                adjacency=adjacency,
                symbols=symbols,
                project_root=project_root,
                current=[root],
                result=chains,
                visited={entry_id},
                max_depth=max_depth,
            )
        return chains
    finally:
        conn.close()


def default_paths(project_name: str = PROJECT_NAME) -> Dict[str, str]:
    base = Path(OUTPUT_DIR) / project_name
    return {
        "db": str(base / "codegraph.sqlite"),
        "output": str(base / "word2vec_call_chains.from_edges.json"),
    }


def main() -> None:
    paths = default_paths()

    chains = build_call_chains_from_edges(paths["db"], PROJECT_PATH, max_depth=20)
    out = Path(paths["output"])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(chains, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"chains": len(chains), "output": str(out)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
