#!/usr/bin/env python3
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from code_parser import parse_project
from init.code_db import CodeDatabase
from init.edge_builder import build_java_lsp_edges, build_java_lsp_implementations
from init.schema import (
    ensure_symbol_json_schema,
    normalize_call,
    normalize_dependency,
    normalize_edge,
    normalize_file,
    normalize_implementation,
    normalize_symbol,
    symbol_row_to_json,
)
from definition import JDTLS_PATH, OUTPUT_DIR, PROJECT_NAME, PROJECT_PATH
from parsers.registry import get_language_for_file


def build_database(
    project_path: str,
    output_dir: str,
    db_name: str,
    export_json: bool = True,
    build_edges: bool = True,
    build_implementations: bool = True,
    edge_workers: int = 4,
    edge_warmup_seconds: float = 8.0,
    edge_timeout: float = 10.0,
    jdtls_path: str = JDTLS_PATH,
) -> Dict[str, int]:
    project_root = os.path.abspath(project_path)
    output_path = Path(output_dir+'/'+PROJECT_NAME).resolve()
    output_path.mkdir(parents=True, exist_ok=True)
    db_path = output_path / db_name

    created_at = datetime.now(timezone.utc).isoformat()
    project_id = os.path.basename(project_root.rstrip(os.sep)) or project_root
    source_cache: Dict[str, List[str]] = {}

    parsed = parse_project(project_root)
    file_rows = []
    symbol_rows = []
    dependency_rows = []
    call_rows = []
    symbol_id = 1

    for file_path in parsed.files:
        file_abs = os.path.abspath(file_path)
        if not os.path.exists(file_abs):
            continue

        language = get_language_for_file(file_abs)
        file_rows.append(normalize_file(file_abs, language))

    for symbol in parsed.symbols:
        symbol_rows.append(
            normalize_symbol(
                symbol=symbol,
                symbol_id=symbol_id,
                project_id=project_id,
                created_at=created_at,
                source_cache=source_cache,
            )
        )
        symbol_id += 1

    for dep in parsed.dependencies:
        dependency_rows.append(normalize_dependency(dep))

    for file_abs, calls in parsed.calls_by_file:
        for call in calls:
            call_rows.append(normalize_call(call, caller_file=file_abs))

    db = CodeDatabase(str(db_path))
    try:
        db.initialize_schema()
        db.reset()
        db.insert_files(file_rows)
        db.insert_symbols(symbol_rows)
        db.insert_dependencies(dependency_rows)
        db.insert_calls(call_rows)
        db.commit()

        edge_errors: List[str] = []
        edge_processed = 0
        implementation_errors: List[str] = []
        implementation_processed = 0
        if build_edges:
            edge_result = build_java_lsp_edges(
                project_root=project_root,
                symbols=symbol_rows,
                workers=edge_workers,
                request_timeout=edge_timeout,
                warmup_seconds=edge_warmup_seconds,
                jdtls_path=jdtls_path,
            )
            edge_rows = [normalize_edge(edge) for edge in edge_result.get("edges", [])]
            db.insert_edges(edge_rows)
            db.commit()
            edge_errors = list(edge_result.get("errors", []))
            edge_processed = int(edge_result.get("processed") or 0)

        if build_implementations:
            implementation_result = build_java_lsp_implementations(
                project_root=project_root,
                symbols=symbol_rows,
                workers=edge_workers,
                request_timeout=edge_timeout,
                warmup_seconds=edge_warmup_seconds,
                jdtls_path=jdtls_path,
            )
            implementation_rows = [
                normalize_implementation(implementation)
                for implementation in implementation_result.get("implementations", [])
            ]
            db.insert_implementations(implementation_rows)
            db.commit()
            _resolve_edge_implementations(db)
            db.commit()
            implementation_errors = list(implementation_result.get("errors", []))
            implementation_processed = int(implementation_result.get("processed") or 0)

        if export_json:
            db.export_symbols_json(str(output_path / "symbols_index.from_db.json"))
            db.export_dependencies_json(str(output_path / "dependency_graph.from_db.json"))

        exported_symbols = [
            symbol_row_to_json(row)
            for row in db.conn.execute("SELECT * FROM code_symbols ORDER BY symbol_id").fetchall()
        ]
        ensure_symbol_json_schema(exported_symbols)

        return {
            "files": db.count("code_files"),
            "symbols": db.count("code_symbols"),
            "dependencies": db.count("code_dependencies"),
            "unresolved_calls": db.count("unresolved_calls"),
            "edges": db.count("code_edges"),
            "implementations": db.count("code_implementations"),
            "edge_methods_processed": edge_processed,
            "edge_errors": len(edge_errors),
            "edge_error_samples": edge_errors[:20],
            "implementation_symbols_processed": implementation_processed,
            "implementation_errors": len(implementation_errors),
            "implementation_error_samples": implementation_errors[:20],
            "db_path": str(db_path),
        }
    finally:
        db.close()


def _resolve_edge_implementations(db: CodeDatabase) -> None:
    rows = db.conn.execute(
        """
        SELECT abstract_symbol_id, implementation_symbol_id, implementation_owner_symbol_id
        FROM code_implementations
        ORDER BY abstract_symbol_id, implementation_symbol_id
        """
    ).fetchall()
    by_abstract: Dict[int, List[Dict[str, int]]] = {}
    for row in rows:
        by_abstract.setdefault(int(row["abstract_symbol_id"]), []).append(
            {
                "symbol_id": int(row["implementation_symbol_id"]),
                "owner_symbol_id": row["implementation_owner_symbol_id"],
            }
        )

    updates = []
    edges = db.conn.execute(
        """
        SELECT edge_id, source_symbol_id, target_symbol_id
        FROM code_edges
        ORDER BY edge_id
        """
    ).fetchall()
    for edge in edges:
        source_impls = by_abstract.get(int(edge["source_symbol_id"]), [])
        target_impls = by_abstract.get(int(edge["target_symbol_id"]), [])

        source_impl_symbol_id = None
        source_impl_owner_symbol_id = None
        if len(source_impls) == 1:
            source_impl_symbol_id = source_impls[0]["symbol_id"]
            source_impl_owner_symbol_id = source_impls[0]["owner_symbol_id"]

        target_impl_symbol_id = None
        target_impl_owner_symbol_id = None
        if len(target_impls) == 1:
            target_impl_symbol_id = target_impls[0]["symbol_id"]
            target_impl_owner_symbol_id = target_impls[0]["owner_symbol_id"]
            status = "resolved"
        elif len(target_impls) > 1:
            status = "ambiguous"
        else:
            status = "none"

        updates.append(
            {
                "edge_id": edge["edge_id"],
                "source_impl_symbol_id": source_impl_symbol_id,
                "target_impl_symbol_id": target_impl_symbol_id,
                "source_impl_owner_symbol_id": source_impl_owner_symbol_id,
                "target_impl_owner_symbol_id": target_impl_owner_symbol_id,
                "impl_resolution_status": status,
            }
        )

    db.update_edge_implementation_resolution(updates)


def main() -> None:
    # parser = argparse.ArgumentParser(description="Build a SQLite code database for a project.")
    # parser.add_argument("--project_path", default=PROJECT_PATH, help="Project root directory to parse.")
    # parser.add_argument(
    #     "--output",
    #     default=str(Path(OUTPUT_DIR) / PROJECT_NAME),
    #     help="Output directory for the SQLite database.",
    # )
    # parser.add_argument("--db_name", default="codegraph.sqlite", help="SQLite database filename.")
    # parser.add_argument("--export-json", action="store_true", help="Export JSON files from the database for validation.")
    # parser.add_argument("--skip-edges", action="store_true", help="Skip Java LSP edge construction.")
    # parser.add_argument(
    #     "--skip-implementations",
    #     action="store_true",
    #     help="Skip Java LSP implementation relation construction.",
    # )
    # parser.add_argument("--edge-workers", type=int, default=4, help="Parallel Java LSP workers for edge construction.")
    # parser.add_argument("--edge-warmup-seconds", type=float, default=8.0, help="Seconds to wait after each LSP worker starts.")
    # parser.add_argument("--edge-timeout", type=float, default=10.0, help="LSP request timeout in seconds.")
    # parser.add_argument("--jdtls-path", default=JDTLS_PATH, help="Path to the jdtls executable.")
    # args = parser.parse_args()

    summary = build_database(
        project_path=PROJECT_PATH,
        output_dir=OUTPUT_DIR,
        db_name="codegraph.sqlite",
        export_json=True,
        build_edges=True,
        build_implementations=True,
        edge_workers=4,
        edge_warmup_seconds=10,
        edge_timeout=20,
        jdtls_path=JDTLS_PATH,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
