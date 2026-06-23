#!/usr/bin/env python3
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from code_parser import list_source_files, read_text
from init.code_db import CodeDatabase
from init.schema import (
    ensure_symbol_json_schema,
    normalize_call,
    normalize_dependency,
    normalize_file,
    normalize_symbol,
    symbol_row_to_json,
)
from parsers.registry import get_language_for_file, parse_file_with_registry


def _dedupe_dependencies(rows: List[Dict]) -> List[Dict]:
    seen = set()
    result = []
    for row in rows:
        key = (row["source_file"], row["target_file"], row["type"])
        if key in seen:
            continue
        seen.add(key)
        result.append(row)
    return result


def build_database(project_path: str, output_dir: str, db_name: str, export_json: bool = False) -> Dict[str, int]:
    project_root = os.path.abspath(project_path)
    output_path = Path(output_dir).resolve()
    output_path.mkdir(parents=True, exist_ok=True)
    db_path = output_path / db_name

    created_at = datetime.now(timezone.utc).isoformat()
    project_id = os.path.basename(project_root.rstrip(os.sep)) or project_root
    source_cache: Dict[str, List[str]] = {}

    files = list_source_files(project_root)
    file_rows = []
    symbol_rows = []
    dependency_rows = []
    call_rows = []
    symbol_id = 1

    for file_path in files:
        file_abs = os.path.abspath(file_path)
        source = read_text(file_abs)
        if source is None:
            continue

        language = get_language_for_file(file_abs)
        file_rows.append(normalize_file(file_abs, language))

        symbols, calls, deps = parse_file_with_registry(file_abs, file_abs, source)

        for symbol in symbols:
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

        for dep in deps:
            dependency_rows.append(normalize_dependency(dep))

        for call in calls:
            call_rows.append(normalize_call(call, caller_file=file_abs))

    dependency_rows = _dedupe_dependencies(dependency_rows)

    db = CodeDatabase(str(db_path))
    try:
        db.initialize_schema()
        db.reset()
        db.insert_files(file_rows)
        db.insert_symbols(symbol_rows)
        db.insert_dependencies(dependency_rows)
        db.insert_calls(call_rows)
        db.commit()

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
            "db_path": str(db_path),
        }
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a SQLite code database for a project.")
    parser.add_argument("--project_path", required=True, help="Project root directory to parse.")
    parser.add_argument("--output", required=True, help="Output directory for the SQLite database.")
    parser.add_argument("--db_name", default="codegraph.sqlite", help="SQLite database filename.")
    parser.add_argument("--export-json", action="store_true", help="Export JSON files from the database for validation.")
    args = parser.parse_args()

    summary = build_database(
        project_path=args.project_path,
        output_dir=args.output,
        db_name=args.db_name,
        export_json=args.export_json,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
