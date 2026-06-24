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
from init.edge_builder import build_java_lsp_edges
from init.schema import (
    ensure_symbol_json_schema,
    normalize_call,
    normalize_dependency,
    normalize_edge,
    normalize_file,
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
    edge_workers: int = 4,
    edge_warmup_seconds: float = 8.0,
    edge_timeout: float = 10.0,
    jdtls_path: str = JDTLS_PATH,
) -> Dict[str, int]:
    project_root = os.path.abspath(project_path)
    output_path = Path(output_dir).resolve()
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
            "edge_methods_processed": edge_processed,
            "edge_errors": len(edge_errors),
            "edge_error_samples": edge_errors[:20],
            "db_path": str(db_path),
        }
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a SQLite code database for a project.")
    parser.add_argument("--project_path", default=PROJECT_PATH, help="Project root directory to parse.")
    parser.add_argument(
        "--output",
        default=str(Path(OUTPUT_DIR) / PROJECT_NAME),
        help="Output directory for the SQLite database.",
    )
    parser.add_argument("--db_name", default="codegraph.sqlite", help="SQLite database filename.")
    parser.add_argument("--export-json", action="store_true", help="Export JSON files from the database for validation.")
    parser.add_argument("--skip-edges", action="store_true", help="Skip Java LSP edge construction.")
    parser.add_argument("--edge-workers", type=int, default=4, help="Parallel Java LSP workers for edge construction.")
    parser.add_argument("--edge-warmup-seconds", type=float, default=8.0, help="Seconds to wait after each LSP worker starts.")
    parser.add_argument("--edge-timeout", type=float, default=10.0, help="LSP request timeout in seconds.")
    parser.add_argument("--jdtls-path", default=JDTLS_PATH, help="Path to the jdtls executable.")
    args = parser.parse_args()

    summary = build_database(
        project_path=args.project_path,
        output_dir=args.output,
        db_name=args.db_name,
        export_json=args.export_json,
        build_edges=not args.skip_edges,
        edge_workers=args.edge_workers,
        edge_warmup_seconds=args.edge_warmup_seconds,
        edge_timeout=args.edge_timeout,
        jdtls_path=args.jdtls_path,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
