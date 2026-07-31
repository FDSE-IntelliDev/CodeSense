import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from codesense.config import load_config


def default_code_db_path() -> str:
    """默认代码图库位置。

    做成函数而不是模块级常量：常量在 import 时求值，会让
    ``import codesense.filters.relation_graph_store`` 顺带读一次 YAML，
    违反「模块顶层不执行逻辑」。改成调用时才解析。
    """
    return str(load_config().project_output_dir / "codegraph.sqlite")


def _base_name(name: Any) -> str:
    return str(name or "").strip().split("(", 1)[0].strip()


def _symbol_key(value: Any) -> str:
    return str(value)


class RelationGraphStore:
    def __init__(self, db_path: Optional[str] = None):
        db_path = db_path or default_code_db_path()
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self._implementation_pairs: Optional[List[Tuple[int, int, str, str, int]]] = None

    def close(self) -> None:
        self.conn.close()

    @classmethod
    def open_if_ready(cls, db_path: Optional[str] = None) -> Optional["RelationGraphStore"]:
        db_path = db_path or default_code_db_path()
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

    def undirected_call_pairs(
        self,
        start_ids: Iterable[int],
        target_ids: Iterable[int],
        max_depth: int,
    ) -> List[Tuple[int, int, int]]:
        """Return all start/target pairs within an undirected call distance.

        Unlike :meth:`undirected_call_neighborhood`, this method retains every
        reachable origin. That distinction is required when several Surface
        pair rules are joined: keeping only one nearest origin can discard a
        different, equally near origin that is needed by another rule.

        The traversal expands one side only. ``target_ids`` remain direct
        retrieval hits, so an intermediate call-chain node can never become a
        pair endpoint merely because both sides can reach it.
        """
        max_depth = max(int(max_depth), 0)
        starts = {int(symbol_id) for symbol_id in start_ids}
        targets = {int(symbol_id) for symbol_id in target_ids}
        if not starts or not targets:
            return []

        equivalent_cache: Dict[int, Set[int]] = {}

        def equivalents(symbol_id: int) -> Set[int]:
            cached = equivalent_cache.get(symbol_id)
            if cached is None:
                cached = self.equivalent_symbol_ids([symbol_id]) or {symbol_id}
                equivalent_cache[symbol_id] = cached
            return cached

        # node -> origins first reaching that node at the current BFS depth.
        frontier_origins: Dict[int, Set[int]] = {}
        # node -> all origins that have already reached that node.
        visited_origins: Dict[int, Set[int]] = {}
        matched_distances: Dict[Tuple[int, int], int] = {}

        for start_id in sorted(starts):
            for equivalent_id in equivalents(start_id):
                visited_origins.setdefault(equivalent_id, set()).add(start_id)
                frontier_origins.setdefault(equivalent_id, set()).add(start_id)
                if equivalent_id in targets:
                    matched_distances[(start_id, equivalent_id)] = 0

        depth = 0
        expected_pair_count = len(starts) * len(targets)
        while (
            frontier_origins
            and depth < max_depth
            and len(matched_distances) < expected_pair_count
        ):
            frontier = set(frontier_origins)
            placeholders = ",".join("?" for _ in frontier)
            frontier_values = list(frontier)
            rows = self.conn.execute(
                f"""
                SELECT
                  source_symbol_id,
                  source_impl_symbol_id,
                  target_symbol_id,
                  target_impl_symbol_id
                FROM code_edges
                WHERE kind='calls'
                  AND (
                    source_symbol_id IN ({placeholders})
                    OR source_impl_symbol_id IN ({placeholders})
                    OR target_symbol_id IN ({placeholders})
                    OR target_impl_symbol_id IN ({placeholders})
                  )
                """,
                frontier_values * 4,
            ).fetchall()

            next_depth = depth + 1
            next_frontier_origins: Dict[int, Set[int]] = {}
            for row in rows:
                source_ids = {
                    int(value)
                    for value in (
                        row["source_symbol_id"],
                        row["source_impl_symbol_id"],
                    )
                    if value is not None
                }
                target_edge_ids = {
                    int(value)
                    for value in (
                        row["target_symbol_id"],
                        row["target_impl_symbol_id"],
                    )
                    if value is not None
                }
                transitions = (
                    (source_ids & frontier, target_edge_ids),
                    (target_edge_ids & frontier, source_ids),
                )
                for from_ids, candidate_next_ids in transitions:
                    for from_id in from_ids:
                        origins = frontier_origins.get(from_id)
                        if not origins:
                            continue
                        for next_id in candidate_next_ids:
                            for equivalent_id in equivalents(next_id):
                                visited = visited_origins.setdefault(
                                    equivalent_id,
                                    set(),
                                )
                                new_origins = origins - visited
                                if not new_origins:
                                    continue
                                visited.update(new_origins)
                                next_frontier_origins.setdefault(
                                    equivalent_id,
                                    set(),
                                ).update(new_origins)
                                if equivalent_id not in targets:
                                    continue
                                for origin_id in new_origins:
                                    matched_distances.setdefault(
                                        (origin_id, equivalent_id),
                                        next_depth,
                                    )

            frontier_origins = next_frontier_origins
            depth = next_depth

        return [
            (start_id, target_id, distance)
            for (start_id, target_id), distance in sorted(
                matched_distances.items()
            )
        ]

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


def filter_candidates_with_store(
    store: RelationGraphStore,
    candidates: List[Dict[str, Any]],
    relation_entries: List[Tuple[Optional[str], str]],
    relation_kind: str,
    layer: Optional[int],
) -> Optional[Set[str]]:
    """Return candidate ids satisfying normalized caller/callee entries.

    relation_kind:
      - caller: candidate is reachable callee from the anchor caller
      - callee: candidate is reachable caller to the anchor callee

    Return ``None`` when an anchor cannot be resolved so the caller can use the
    existing LSP fallback. Candidate implementation-equivalence is cached for
    the whole constraint execution instead of recomputed for every anchor.
    """
    if relation_kind not in {"caller", "callee"}:
        raise ValueError(f"Unsupported relation kind: {relation_kind}")

    candidates_with_ids = [
        (int(candidate["symbol_id"]), candidate)
        for candidate in candidates
        if candidate.get("symbol_id") is not None
    ]
    if not candidates_with_ids:
        return set()

    candidate_equivalents = {
        candidate_id: store.equivalent_symbol_ids(
            [candidate_id],
            func_name=candidate.get("name") or candidate.get("signature"),
        )
        for candidate_id, candidate in candidates_with_ids
    }
    kept: Set[int] = set()
    direction = "out" if relation_kind == "caller" else "in"
    for file_name, func_name in relation_entries:
        anchors = store.resolve_symbols(file_name, func_name)
        if anchors is None:
            return None
        reachable = store.reachable(
            anchors,
            direction=direction,
            max_depth=layer,
            func_name=func_name,
            exact_depth=False,
            expand_start=file_name is None,
        )
        for candidate_id, _candidate in candidates_with_ids:
            if candidate_equivalents[candidate_id] & reachable:
                kept.add(candidate_id)
    return {_symbol_key(sid) for sid in kept}
