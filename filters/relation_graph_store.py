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
        self._implementation_pairs: Optional[List[Tuple[int, int, str, str, int]]] = None

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

    def _implementation_relation_pairs(self) -> List[Tuple[int, int, str, str, int]]:
        if self._implementation_pairs is not None:
            return self._implementation_pairs

        table = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='code_implementations'"
        ).fetchone()
        if not table:
            self._implementation_pairs = []
            return self._implementation_pairs

        rows = self.conn.execute(
            """
            SELECT
              i.abstract_symbol_id,
              i.implementation_symbol_id,
              abs.name AS abstract_name,
              impl.name AS implementation_name,
              CASE WHEN unique_impls.abstract_symbol_id IS NOT NULL THEN 1 ELSE 0 END AS is_unique
            FROM code_implementations i
            JOIN code_symbols abs ON abs.symbol_id = i.abstract_symbol_id
            JOIN code_symbols impl ON impl.symbol_id = i.implementation_symbol_id
            LEFT JOIN (
                SELECT abstract_symbol_id
                FROM code_implementations
                GROUP BY abstract_symbol_id
                HAVING COUNT(DISTINCT implementation_symbol_id) = 1
            ) unique_impls ON unique_impls.abstract_symbol_id = i.abstract_symbol_id
            """
        ).fetchall()
        self._implementation_pairs = [
            (
                int(row["abstract_symbol_id"]),
                int(row["implementation_symbol_id"]),
                str(row["abstract_name"] or ""),
                str(row["implementation_name"] or ""),
                int(row["is_unique"] or 0),
            )
            for row in rows
        ]
        return self._implementation_pairs

    def equivalent_symbol_ids(self, symbol_ids: Iterable[int], func_name: Optional[str] = None) -> Set[int]:
        """
        Expand symbols across LSP-proven implementation relations.

        Unique implementations are always equivalent. Ambiguous implementations
        are also expanded when the abstract and implementation function names
        match; when a filter function name is available, the pair must match
        that name too. This keeps multi-impl matching recall-friendly without
        inventing relationships from strings.
        """
        expanded = {int(sid) for sid in symbol_ids}
        if not expanded:
            return set()

        filter_name = _base_name(func_name) if func_name else ""
        changed = True
        while changed:
            changed = False
            for abstract_id, implementation_id, abstract_name, implementation_name, is_unique in (
                self._implementation_relation_pairs()
            ):
                if abstract_id not in expanded and implementation_id not in expanded:
                    continue
                same_name = _base_name(abstract_name) == _base_name(implementation_name)
                matches_filter = not filter_name or _base_name(implementation_name) == filter_name
                if not is_unique and not (same_name and matches_filter):
                    continue
                before = len(expanded)
                expanded.add(abstract_id)
                expanded.add(implementation_id)
                changed = changed or len(expanded) > before
        return expanded

    def reachable(
        self,
        start_ids: Iterable[int],
        direction: str,
        max_depth: Optional[int],
        func_name: Optional[str] = None,
        exact_depth: bool = False,
        expand_start: bool = True,
    ) -> Set[int]:
        frontier = (
            self.equivalent_symbol_ids(start_ids, func_name=func_name)
            if expand_start
            else {int(sid) for sid in start_ids}
        )
        if not frontier:
            return set()
        visited = set(frontier)
        result: Set[int] = set()
        depth = 0

        while frontier and (max_depth is None or depth < max_depth):
            placeholders = ",".join("?" for _ in frontier)
            if direction == "out":
                sql = f"""
                    SELECT target_symbol_id AS next_id
                    FROM code_edges
                    WHERE kind='calls' AND source_symbol_id IN ({placeholders})
                    UNION
                    SELECT target_impl_symbol_id AS next_id
                    FROM code_edges
                    WHERE kind='calls'
                      AND target_impl_symbol_id IS NOT NULL
                      AND source_symbol_id IN ({placeholders})
                """
                params = list(frontier) + list(frontier)
            else:
                should_expand_query_frontier = expand_start or depth > 0
                expanded_frontier = (
                    self.equivalent_symbol_ids(frontier, func_name=func_name)
                    if should_expand_query_frontier
                    else set(frontier)
                )
                placeholders = ",".join("?" for _ in expanded_frontier)
                sql = f"""
                    SELECT source_symbol_id AS next_id
                    FROM code_edges
                    WHERE kind='calls'
                      AND (
                        target_symbol_id IN ({placeholders})
                        OR target_impl_symbol_id IN ({placeholders})
                      )
                """
                params = list(expanded_frontier) + list(expanded_frontier)

            rows = self.conn.execute(sql, params).fetchall()
            next_ids = {int(row["next_id"]) for row in rows if row["next_id"] is not None}
            expanded_next = self.equivalent_symbol_ids(next_ids)
            next_depth = depth + 1
            if not exact_depth or max_depth is None or next_depth == max_depth:
                result.update(expanded_next)
            next_frontier = expanded_next - visited
            visited.update(next_frontier)
            frontier = next_frontier
            depth = next_depth

        return result

    def symbol_degrees(
        self,
        symbol_id: int,
        func_name: Optional[str] = None,
    ) -> Tuple[int, int]:
        """Return implementation-aware direct (in_degree, out_degree)."""
        equivalent_ids = self.equivalent_symbol_ids(
            [symbol_id],
            func_name=func_name,
        )
        if not equivalent_ids:
            return 0, 0

        placeholders = ",".join("?" for _ in equivalent_ids)
        params = list(equivalent_ids) + list(equivalent_ids)

        out_row = self.conn.execute(
            f"""
            SELECT COUNT(
                DISTINCT COALESCE(target_impl_symbol_id, target_symbol_id)
            ) AS degree
            FROM code_edges
            WHERE kind='calls'
              AND (
                source_symbol_id IN ({placeholders})
                OR source_impl_symbol_id IN ({placeholders})
              )
            """,
            params,
        ).fetchone()

        in_row = self.conn.execute(
            f"""
            SELECT COUNT(
                DISTINCT COALESCE(source_impl_symbol_id, source_symbol_id)
            ) AS degree
            FROM code_edges
            WHERE kind='calls'
              AND (
                target_symbol_id IN ({placeholders})
                OR target_impl_symbol_id IN ({placeholders})
              )
            """,
            params,
        ).fetchone()

        return int(in_row["degree"] or 0), int(out_row["degree"] or 0)


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
            reachable = store.reachable(
                anchors,
                direction=direction,
                max_depth=max_depth,
                func_name=func_name,
                exact_depth=hop_count is not None,
                expand_start=file_name is None,
            )
            for candidate in candidates:
                if candidate.get("symbol_id") is None:
                    continue
                candidate_id = int(candidate["symbol_id"])
                candidate_func_name = candidate.get("name") or candidate.get("signature")
                if store.equivalent_symbol_ids([candidate_id], func_name=candidate_func_name) & reachable:
                    kept.add(candidate_id)
        return {_symbol_key(sid) for sid in kept}
    finally:
        store.close()
