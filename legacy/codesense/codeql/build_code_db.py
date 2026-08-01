#!/usr/bin/env python3
"""Build a CodeSearch-compatible SQLite database from CodeQL extraction."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from codesense.codeql.runner import CodeQLError, CodeQLRunner
from codesense.codeql.transform import transform_query_results
from codesense.indexing.codegraph.code_db import CodeDatabase
from codesense.indexing.codegraph.schema import ensure_symbol_json_schema, symbol_row_to_json


QUERY_DIR = Path(__file__).resolve().parent / "queries" / "java"


def build_database(
    project_path: str,
    output_dir: str,
    db_name: str = "codegraph.codeql.sqlite",
    codeql_database_path: Optional[str] = None,
    query_result_dir: Optional[str] = None,
    codeql_bin: Optional[str] = None,
    language: str = "java-kotlin",
    build_mode: str = "none",
    build_command: Optional[str] = None,
    reuse_codeql_database: bool = False,
    export_json: bool = True,
    keep_bqrs: bool = False,
    threads: int = 0,
    ram_mb: Optional[int] = None,
) -> Dict[str, Any]:
    """Extract, query and bulk-load one project without invoking Java LSP."""
    project_root = Path(project_path).expanduser().resolve()
    if not project_root.is_dir():
        raise ValueError(f"project path is not a directory: {project_root}")

    output_path = Path(output_dir).expanduser().resolve()
    output_path.mkdir(parents=True, exist_ok=True)
    db_path = output_path / db_name
    codeql_db = Path(
        codeql_database_path or output_path / "codeql-database-java"
    ).expanduser().resolve()
    result_dir = Path(
        query_result_dir or output_path / "codeql-query-results"
    ).expanduser().resolve()

    runner = CodeQLRunner(
        codeql_bin=codeql_bin,
        threads=threads,
        ram_mb=ram_mb,
    )
    started = time.perf_counter()
    codeql_version = runner.version()

    extraction_seconds = 0.0
    if not reuse_codeql_database:
        extraction_started = time.perf_counter()
        runner.create_database(
            project_root=project_root,
            database_path=codeql_db,
            language=language,
            build_mode=build_mode,
            build_command=build_command,
        )
        extraction_seconds = time.perf_counter() - extraction_started
    elif not codeql_db.is_dir():
        raise ValueError(f"CodeQL database does not exist: {codeql_db}")

    query_started = time.perf_counter()
    csv_paths = runner.run_queries(
        database_path=codeql_db,
        query_dir=QUERY_DIR,
        result_dir=result_dir,
        keep_bqrs=keep_bqrs,
    )
    query_seconds = time.perf_counter() - query_started

    created_at = datetime.now(timezone.utc).isoformat()
    project_id = project_root.name or str(project_root)
    transform_started = time.perf_counter()
    rows = transform_query_results(
        project_root=project_root,
        csv_paths=csv_paths,
        project_id=project_id,
        created_at=created_at,
    )
    transform_seconds = time.perf_counter() - transform_started

    load_started = time.perf_counter()
    db = CodeDatabase(str(db_path))
    try:
        db.initialize_schema()
        db.reset()
        db.insert_files(rows.files)
        db.insert_symbols(rows.symbols)
        db.insert_dependencies(rows.dependencies)
        db.insert_calls(rows.unresolved_calls)
        db.insert_edges(rows.edges)
        db.insert_implementations(rows.implementations)
        db.commit()

        if export_json:
            db.export_symbols_json(str(output_path / "symbols_index.codeql.json"))
            db.export_dependencies_json(
                str(output_path / "dependency_graph.codeql.json")
            )

        exported_symbols = [
            symbol_row_to_json(row)
            for row in db.conn.execute(
                "SELECT * FROM code_symbols ORDER BY symbol_id"
            ).fetchall()
        ]
        ensure_symbol_json_schema(exported_symbols)
        counts = {
            "files": db.count("code_files"),
            "symbols": db.count("code_symbols"),
            "dependencies": db.count("code_dependencies"),
            "unresolved_calls": db.count("unresolved_calls"),
            "edges": db.count("code_edges"),
            "implementations": db.count("code_implementations"),
        }
    finally:
        db.close()
    load_seconds = time.perf_counter() - load_started

    summary: Dict[str, Any] = {
        **counts,
        "skipped_calls": rows.skipped_calls,
        "skipped_implementations": rows.skipped_implementations,
        "codeql_version": codeql_version,
        "language": language,
        "build_mode": "manual" if build_command else build_mode,
        "reused_codeql_database": reuse_codeql_database,
        "extraction_seconds": round(extraction_seconds, 3),
        "query_seconds": round(query_seconds, 3),
        "transform_seconds": round(transform_seconds, 3),
        "sqlite_load_seconds": round(load_seconds, 3),
        "total_seconds": round(time.perf_counter() - started, 3),
        "db_path": str(db_path),
        "codeql_database_path": str(codeql_db),
        "query_result_dir": str(result_dir),
    }
    (output_path / "codeql_build_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return summary


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build codegraph.codeql.sqlite using CodeQL instead of parser + Java LSP."
        )
    )
    parser.add_argument("--project-path", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--db-name", default="codegraph.codeql.sqlite")
    parser.add_argument("--codeql-db")
    parser.add_argument("--query-result-dir")
    parser.add_argument("--codeql-bin")
    parser.add_argument("--language", default="java-kotlin")
    parser.add_argument(
        "--build-mode",
        choices=("none", "autobuild", "manual"),
        default="none",
    )
    parser.add_argument(
        "--build-command",
        help="Explicit build command; when set, CodeQL uses manual build tracing.",
    )
    parser.add_argument("--reuse-codeql-db", action="store_true")
    parser.add_argument("--no-export-json", action="store_true")
    parser.add_argument("--keep-bqrs", action="store_true")
    parser.add_argument(
        "--threads",
        type=int,
        default=0,
        help="CodeQL threads; 0 uses one thread per core.",
    )
    parser.add_argument("--ram-mb", type=int)
    return parser


def main() -> None:
    args = _parser().parse_args()
    if args.build_mode == "manual" and not args.build_command:
        raise SystemExit("--build-mode=manual requires --build-command")
    try:
        summary = build_database(
            project_path=args.project_path,
            output_dir=args.output_dir,
            db_name=args.db_name,
            codeql_database_path=args.codeql_db,
            query_result_dir=args.query_result_dir,
            codeql_bin=args.codeql_bin,
            language=args.language,
            build_mode=args.build_mode,
            build_command=args.build_command,
            reuse_codeql_database=args.reuse_codeql_db,
            export_json=not args.no_export_json,
            keep_bqrs=args.keep_bqrs,
            threads=args.threads,
            ram_mb=args.ram_mb,
        )
    except CodeQLError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
