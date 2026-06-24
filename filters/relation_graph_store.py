import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from definition import OUTPUT_DIR, PROJECT_NAME


DEFAULT_CODE_DB_PATH = str(Path(OUTPUT_DIR) / PROJECT_NAME / "codegraph.sqlite")


def _base_name(name: Any) -> str:
    return str(name or "").strip().split("(", 1)[0].strip()


def _symbol_key(value: Any) -> str:
    return str(value)


class RelationGraphStore:
    def __init__(self, db_path: str = DEFAULT_CODE_DB_PATH):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row

    def close(self) -> None:
        self.conn.close()

    @classmethod
    def open_if_ready(cls, db_path: str = DEFAULT_CODE_DB_PATH) -> Optional["RelationGraphStore"]:
        if not Path(db_path).exists():
            return None
        store = cls(db_path)
        try:
            if not store._has_edges():
                store.close()
                return None
            return store
        except sqlite3.Error:
            store.close()
            return None

    def _has_edges(self) -> bool:
        row = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='code_edges'"
        ).fetchone()
        if not row:
            return False
        count = self.conn.execute("SELECT COUNT(*) AS n FROM code_edges").fetchone()
        return int(count["n"]) > 0

    def resolve_symbols(self, file_name: Optional[str], func_name: str) -> Optional[Set[int]]:
        name = _base_name(func_name)
        if not name:
            return None

        params: List[Any] = [name]
        sql = """
            SELECT symbol_id
            FROM code_symbols
            WHERE name = ?
        """
        if file_name:
            sql += " AND (file = ? OR file LIKE ?)"
            params.extend([file_name, f"%/{file_name}"])

        rows = self.conn.execute(sql, params).fetchall()
        if not rows:
            return None
        return {int(row["symbol_id"]) for row in rows}

    def reachable(self, start_ids: Iterable[int], direction: str, max_depth: Optional[int]) -> Set[int]:
        frontier = {int(sid) for sid in start_ids}
        if not frontier:
            return set()
        visited = set(frontier)
        result: Set[int] = set()
        depth = 0

        while frontier and (max_depth is None or depth < max_depth):
            placeholders = ",".join("?" for _ in frontier)
            if direction == "out":
                sql = f"SELECT target_symbol_id AS next_id FROM code_edges WHERE kind='calls' AND source_symbol_id IN ({placeholders})"
            else:
                sql = f"SELECT source_symbol_id AS next_id FROM code_edges WHERE kind='calls' AND target_symbol_id IN ({placeholders})"

            rows = self.conn.execute(sql, list(frontier)).fetchall()
            next_frontier = {int(row["next_id"]) for row in rows if int(row["next_id"]) not in visited}
            result.update(next_frontier)
            visited.update(next_frontier)
            frontier = next_frontier
            depth += 1

        return result


def filter_candidates_with_edges(
    candidates: List[Dict[str, Any]],
    relation_entries: List[Tuple[Optional[str], str, Optional[int]]],
    relation_kind: str,
    layer: Optional[int],
    db_path: str = DEFAULT_CODE_DB_PATH,
) -> Optional[Set[str]]:
    """
    Return candidate symbol ids that satisfy caller/callee constraints via code_edges.

    relation_kind:
      - caller: candidate is reachable callee from the anchor caller
      - callee: candidate is reachable caller to the anchor callee
    Return None when DB is unavailable or an anchor cannot be resolved, so callers
    can fall back to the existing LSP path.
    """
    store = RelationGraphStore.open_if_ready(db_path)
    if store is None:
        return None

    try:
        candidate_ids = {
            int(candidate["symbol_id"])
            for candidate in candidates
            if candidate.get("symbol_id") is not None
        }
        if not candidate_ids:
            return set()

        kept: Set[int] = set()
        for file_name, func_name, hop_count in relation_entries:
            anchors = store.resolve_symbols(file_name, func_name)
            if anchors is None:
                return None
            max_depth = hop_count if hop_count is not None else layer
            direction = "out" if relation_kind == "caller" else "in"
            reachable = store.reachable(anchors, direction=direction, max_depth=max_depth)
            kept.update(candidate_ids & reachable)
        return {_symbol_key(sid) for sid in kept}
    finally:
        store.close()
