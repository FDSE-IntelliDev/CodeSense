"""
CodeQL execution helper.

This module runs a complete CodeQL query string against an existing CodeQL
database and returns decoded query results as Python dictionaries.
"""

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from definition import OUTPUT_DIR, PROJECT_NAME


DEFAULT_CODEQL_DATABASE_CANDIDATES = (
    Path(OUTPUT_DIR) / PROJECT_NAME / "codeql-db",
    Path(OUTPUT_DIR) / PROJECT_NAME / "codeql_database",
    Path(OUTPUT_DIR) / PROJECT_NAME / "database",
)


class CodeQLError(RuntimeError):
    """Raised when CodeQL execution fails."""


def _resolve_codeql_database(database_path: Optional[str]) -> Path:
    if database_path:
        path = Path(database_path).expanduser().resolve()
        if path.exists():
            return path
        raise FileNotFoundError(f"CodeQL database not found: {path}")

    env_path = os.environ.get("CODEQL_DATABASE")
    if env_path:
        path = Path(env_path).expanduser().resolve()
        if path.exists():
            return path
        raise FileNotFoundError(f"CODEQL_DATABASE does not exist: {path}")

    for candidate in DEFAULT_CODEQL_DATABASE_CANDIDATES:
        if candidate.exists():
            return candidate.resolve()

    candidates = ", ".join(str(path) for path in DEFAULT_CODEQL_DATABASE_CANDIDATES)
    raise FileNotFoundError(
        "CodeQL database not found. Pass database_path explicitly, set "
        f"CODEQL_DATABASE, or create one at one of: {candidates}"
    )


def _run_command(cmd: List[str]) -> None:
    try:
        completed = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            f"CodeQL CLI not found: {cmd[0]}. Install CodeQL or pass codeql_path."
        ) from exc

    if completed.returncode != 0:
        raise CodeQLError(
            "CodeQL command failed\n"
            f"command: {' '.join(cmd)}\n"
            f"stdout:\n{completed.stdout}\n"
            f"stderr:\n{completed.stderr}"
        )


def _decode_cell(value: Any) -> Any:
    """Normalize common BQRS JSON cell shapes into plain Python values."""
    if isinstance(value, dict):
        for key in ("label", "url", "string", "value", "name"):
            if key in value:
                return value[key]
        return value
    return value


def _decode_bqrs_json(decoded: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Convert `codeql bqrs decode --format=json` output to row dictionaries.

    The JSON normally contains a `#select` result set with `columns` and
    `tuples`. This parser is intentionally defensive because CodeQL versions
    differ slightly in how they encode column metadata and entity cells.
    """
    result_set = decoded.get("#select")
    if not isinstance(result_set, dict):
        return []

    columns = result_set.get("columns", [])
    tuples = result_set.get("tuples", [])
    if not isinstance(tuples, list):
        return []

    column_names: List[str] = []
    for index, column in enumerate(columns):
        if isinstance(column, dict):
            name = column.get("name") or column.get("label") or f"col_{index}"
        else:
            name = str(column or f"col_{index}")
        column_names.append(str(name))

    rows: List[Dict[str, Any]] = []
    for row in tuples:
        if not isinstance(row, list):
            continue
        result: Dict[str, Any] = {}
        for index, value in enumerate(row):
            column = column_names[index] if index < len(column_names) else f"col_{index}"
            result[column] = _decode_cell(value)
        rows.append(result)
    return rows


def run_codeql_query(
    query: str,
    database_path: Optional[str] = None,
    codeql_path: str = "codeql",
    timeout: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """
    Run a CodeQL query string against a CodeQL database.

    Args:
        query: Complete CodeQL query text, including imports and select clause.
        database_path: Existing CodeQL database directory. If omitted, this
            function checks CODEQL_DATABASE and a few output/<project> defaults.
        codeql_path: CodeQL CLI executable path. Defaults to `codeql`.
        timeout: Optional query timeout in seconds.

    Returns:
        Decoded CodeQL result rows. Each row maps selected column names to
        decoded cell values.
    """
    if not str(query or "").strip():
        raise ValueError("query must be a non-empty CodeQL query string")

    database = _resolve_codeql_database(database_path)

    with tempfile.TemporaryDirectory(prefix="codesearch_codeql_") as tmp_dir:
        tmp_path = Path(tmp_dir)
        query_path = tmp_path / "query.ql"
        bqrs_path = tmp_path / "result.bqrs"
        json_path = tmp_path / "result.json"

        query_path.write_text(query, encoding="utf-8")

        run_cmd = [
            codeql_path,
            "query",
            "run",
            str(query_path),
            f"--database={database}",
            f"--output={bqrs_path}",
        ]
        if timeout is not None:
            run_cmd.append(f"--timeout={int(timeout)}")
        _run_command(run_cmd)

        _run_command([
            codeql_path,
            "bqrs",
            "decode",
            str(bqrs_path),
            "--format=json",
            f"--output={json_path}",
        ])

        decoded = json.loads(json_path.read_text(encoding="utf-8"))
        return _decode_bqrs_json(decoded)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run a CodeQL query file.")
    parser.add_argument("query_file", help="Path to a .ql query file")
    parser.add_argument("--database", dest="database_path", default=None)
    parser.add_argument("--codeql", dest="codeql_path", default="codeql")
    parser.add_argument("--timeout", dest="timeout", type=int, default=None)
    args = parser.parse_args()

    query_text = Path(args.query_file).read_text(encoding="utf-8")
    print(json.dumps(
        run_codeql_query(
            query_text,
            database_path=args.database_path,
            codeql_path=args.codeql_path,
            timeout=args.timeout,
        ),
        ensure_ascii=False,
        indent=2,
    ))
