import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, List

from .schema import dependency_row_to_json, symbol_row_to_json


class CodeDatabase:
    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row

    def close(self) -> None:
        self.conn.close()

    def initialize_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS code_files (
              file_id INTEGER PRIMARY KEY AUTOINCREMENT,
              path TEXT NOT NULL UNIQUE,
              language TEXT,
              size_bytes INTEGER,
              mtime REAL,
              content_hash TEXT
            );

            CREATE TABLE IF NOT EXISTS code_symbols (
              symbol_id INTEGER PRIMARY KEY,
              name TEXT NOT NULL,
              type TEXT NOT NULL,
              file TEXT NOT NULL,
              start_line INTEGER NOT NULL,
              end_line INTEGER NOT NULL,
              name_line INTEGER,
              name_col INTEGER,
              signature TEXT,
              language TEXT,
              doc TEXT,
              container TEXT,
              qualified_name TEXT,
              project_id TEXT,
              created_at TEXT
            );

            CREATE TABLE IF NOT EXISTS code_dependencies (
              dependency_id INTEGER PRIMARY KEY AUTOINCREMENT,
              source_file TEXT NOT NULL,
              target_file TEXT NOT NULL,
              type TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS unresolved_calls (
              call_id INTEGER PRIMARY KEY AUTOINCREMENT,
              caller TEXT,
              callee TEXT,
              caller_file TEXT,
              line INTEGER,
              code TEXT
            );

            CREATE TABLE IF NOT EXISTS code_edges (
              edge_id INTEGER PRIMARY KEY AUTOINCREMENT,
              source_symbol_id INTEGER NOT NULL,
              target_symbol_id INTEGER NOT NULL,
              kind TEXT NOT NULL,
              source_name TEXT,
              target_name TEXT,
              source_file TEXT,
              target_file TEXT,
              call_line INTEGER,
              call_col INTEGER,
              confidence REAL,
              provenance TEXT,
              source_impl_symbol_id INTEGER,
              target_impl_symbol_id INTEGER,
              source_impl_owner_symbol_id INTEGER,
              target_impl_owner_symbol_id INTEGER,
              impl_resolution_status TEXT,
              raw_lsp TEXT
            );

            CREATE TABLE IF NOT EXISTS code_implementations (
              impl_id INTEGER PRIMARY KEY AUTOINCREMENT,
              abstract_symbol_id INTEGER NOT NULL,
              implementation_symbol_id INTEGER NOT NULL,
              abstract_owner_symbol_id INTEGER,
              implementation_owner_symbol_id INTEGER,
              relation_kind TEXT NOT NULL,
              confidence REAL,
              provenance TEXT,
              is_ambiguous INTEGER,
              raw_lsp TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_symbols_name ON code_symbols(name);
            CREATE INDEX IF NOT EXISTS idx_symbols_type ON code_symbols(type);
            CREATE INDEX IF NOT EXISTS idx_symbols_file ON code_symbols(file);
            CREATE INDEX IF NOT EXISTS idx_symbols_language ON code_symbols(language);
            CREATE INDEX IF NOT EXISTS idx_symbols_container ON code_symbols(container);
            CREATE INDEX IF NOT EXISTS idx_deps_source ON code_dependencies(source_file);
            CREATE INDEX IF NOT EXISTS idx_calls_caller ON unresolved_calls(caller);
            CREATE INDEX IF NOT EXISTS idx_calls_callee ON unresolved_calls(callee);
            CREATE INDEX IF NOT EXISTS idx_edges_source_kind ON code_edges(source_symbol_id, kind);
            CREATE INDEX IF NOT EXISTS idx_edges_target_kind ON code_edges(target_symbol_id, kind);
            CREATE INDEX IF NOT EXISTS idx_edges_source_target ON code_edges(source_symbol_id, target_symbol_id);
            CREATE INDEX IF NOT EXISTS idx_impls_abstract ON code_implementations(abstract_symbol_id);
            CREATE INDEX IF NOT EXISTS idx_impls_implementation ON code_implementations(implementation_symbol_id);
            """
        )
        self._ensure_column("code_edges", "source_impl_symbol_id", "INTEGER")
        self._ensure_column("code_edges", "target_impl_symbol_id", "INTEGER")
        self._ensure_column("code_edges", "source_impl_owner_symbol_id", "INTEGER")
        self._ensure_column("code_edges", "target_impl_owner_symbol_id", "INTEGER")
        self._ensure_column("code_edges", "impl_resolution_status", "TEXT")
        self.conn.commit()

    def _ensure_column(self, table: str, column: str, definition: str) -> None:
        columns = {row["name"] for row in self.conn.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def reset(self) -> None:
        self.conn.executescript(
            """
            DELETE FROM unresolved_calls;
            DELETE FROM code_implementations;
            DELETE FROM code_edges;
            DELETE FROM code_dependencies;
            DELETE FROM code_symbols;
            DELETE FROM code_files;
            DELETE FROM sqlite_sequence WHERE name IN ('code_files', 'code_dependencies', 'unresolved_calls', 'code_edges', 'code_implementations');
            """
        )
        self.conn.commit()

    def insert_files(self, rows: Iterable[Dict[str, Any]]) -> None:
        self.conn.executemany(
            """
            INSERT INTO code_files(path, language, size_bytes, mtime, content_hash)
            VALUES (:path, :language, :size_bytes, :mtime, :content_hash)
            """,
            list(rows),
        )

    def insert_symbols(self, rows: Iterable[Dict[str, Any]]) -> None:
        self.conn.executemany(
            """
            INSERT INTO code_symbols(
              symbol_id, name, type, file, start_line, end_line, name_line, name_col,
              signature, language, doc, container, qualified_name, project_id, created_at
            )
            VALUES (
              :symbol_id, :name, :type, :file, :start_line, :end_line, :name_line, :name_col,
              :signature, :language, :doc, :container, :qualified_name, :project_id, :created_at
            )
            """,
            list(rows),
        )

    def insert_dependencies(self, rows: Iterable[Dict[str, Any]]) -> None:
        self.conn.executemany(
            """
            INSERT INTO code_dependencies(source_file, target_file, type)
            VALUES (:source_file, :target_file, :type)
            """,
            list(rows),
        )

    def insert_calls(self, rows: Iterable[Dict[str, Any]]) -> None:
        self.conn.executemany(
            """
            INSERT INTO unresolved_calls(caller, callee, caller_file, line, code)
            VALUES (:caller, :callee, :caller_file, :line, :code)
            """,
            list(rows),
        )

    def insert_edges(self, rows: Iterable[Dict[str, Any]]) -> None:
        self.conn.executemany(
            """
            INSERT INTO code_edges(
              source_symbol_id, target_symbol_id, kind, source_name, target_name,
              source_file, target_file, call_line, call_col, confidence, provenance,
              source_impl_symbol_id, target_impl_symbol_id,
              source_impl_owner_symbol_id, target_impl_owner_symbol_id,
              impl_resolution_status, raw_lsp
            )
            VALUES (
              :source_symbol_id, :target_symbol_id, :kind, :source_name, :target_name,
              :source_file, :target_file, :call_line, :call_col, :confidence, :provenance,
              :source_impl_symbol_id, :target_impl_symbol_id,
              :source_impl_owner_symbol_id, :target_impl_owner_symbol_id,
              :impl_resolution_status, :raw_lsp
            )
            """,
            list(rows),
        )

    def insert_implementations(self, rows: Iterable[Dict[str, Any]]) -> None:
        self.conn.executemany(
            """
            INSERT INTO code_implementations(
              abstract_symbol_id, implementation_symbol_id,
              abstract_owner_symbol_id, implementation_owner_symbol_id,
              relation_kind, confidence, provenance, is_ambiguous, raw_lsp
            )
            VALUES (
              :abstract_symbol_id, :implementation_symbol_id,
              :abstract_owner_symbol_id, :implementation_owner_symbol_id,
              :relation_kind, :confidence, :provenance, :is_ambiguous, :raw_lsp
            )
            """,
            list(rows),
        )

    def update_edge_implementation_resolution(self, rows: Iterable[Dict[str, Any]]) -> None:
        self.conn.executemany(
            """
            UPDATE code_edges
            SET source_impl_symbol_id = :source_impl_symbol_id,
                target_impl_symbol_id = :target_impl_symbol_id,
                source_impl_owner_symbol_id = :source_impl_owner_symbol_id,
                target_impl_owner_symbol_id = :target_impl_owner_symbol_id,
                impl_resolution_status = :impl_resolution_status
            WHERE edge_id = :edge_id
            """,
            list(rows),
        )

    def commit(self) -> None:
        self.conn.commit()

    def count(self, table: str) -> int:
        allowed = {
            "code_files",
            "code_symbols",
            "code_dependencies",
            "unresolved_calls",
            "code_edges",
            "code_implementations",
        }
        if table not in allowed:
            raise ValueError(f"unsupported table: {table}")
        row = self.conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
        return int(row["n"])

    def export_symbols_json(self, path: str) -> None:
        rows = self.conn.execute("SELECT * FROM code_symbols ORDER BY symbol_id").fetchall()
        payload = [symbol_row_to_json(row) for row in rows]
        Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def export_dependencies_json(self, path: str) -> None:
        rows = self.conn.execute(
            "SELECT source_file, target_file, type FROM code_dependencies ORDER BY dependency_id"
        ).fetchall()
        payload = [dependency_row_to_json(row) for row in rows]
        Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
