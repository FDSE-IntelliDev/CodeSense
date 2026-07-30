"""Linear-time conversion from decoded CodeQL tuples to ``CodeDatabase`` rows."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Tuple

from codesense.codeql.runner import JAVA_QUERY_SPECS, QuerySpec
from codesense.indexing.codegraph.schema import normalize_file


PROVENANCE_CALL = "codeql_java_call"
PROVENANCE_IMPLEMENTATION = "codeql_java_implementation"


@dataclass
class CodeQLRows:
    files: List[Dict] = field(default_factory=list)
    symbols: List[Dict] = field(default_factory=list)
    dependencies: List[Dict] = field(default_factory=list)
    unresolved_calls: List[Dict] = field(default_factory=list)
    edges: List[Dict] = field(default_factory=list)
    implementations: List[Dict] = field(default_factory=list)
    skipped_calls: int = 0
    skipped_implementations: int = 0


def _int(value: object, default: int = 0) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return default


def _iter_csv(path: Path, columns: Sequence[str]) -> Iterator[Dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        expected = len(columns)
        for row_number, row in enumerate(reader, start=1):
            if len(row) != expected:
                raise ValueError(
                    f"{path}:{row_number}: expected {expected} columns, got {len(row)}"
                )
            yield dict(zip(columns, row))


def _spec_by_name() -> Dict[str, QuerySpec]:
    return {spec.name: spec for spec in JAVA_QUERY_SPECS}


def _inside_project(project_root: Path, path: Path) -> bool:
    try:
        path.relative_to(project_root)
        return True
    except ValueError:
        return False


def _source_path(
    project_root: Path,
    value: str,
    require_exists: bool = True,
) -> Optional[str]:
    raw = str(value or "").strip().replace("\\", "/")
    if not raw:
        return None
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = project_root / candidate
    candidate = candidate.resolve()
    if not _inside_project(project_root, candidate):
        return None
    if require_exists and not candidate.is_file():
        return None
    return str(candidate)


def _language_for_file(path: str, fallback: str = "java") -> str:
    suffix = Path(path).suffix.lower()
    if suffix in {".kt", ".kts"}:
        return "kotlin"
    return fallback or "java"


def _read_source_lines(file_path: str) -> List[str]:
    try:
        return Path(file_path).read_text(
            encoding="utf-8", errors="ignore"
        ).splitlines()
    except OSError:
        return []


def _name_position(
    lines: Sequence[str],
    name: str,
    start_line: int,
    fallback_col: int,
) -> Tuple[int, int]:
    first = max(start_line - 1, 0)
    last = min(first + 3, len(lines))
    for index in range(first, last):
        column = lines[index].find(name)
        if column >= 0:
            return index + 1, column
    return max(start_line, 1), max(fallback_col, 0)


def _normalize_import_target(value: str) -> str:
    target = str(value or "").strip().rstrip(";").strip()
    if target.startswith("import "):
        target = target[7:].strip()
    if target.startswith("static "):
        target = target[7:].strip()
    return target.replace(".", "/")


def _symbol_rows(
    project_root: Path,
    csv_path: Path,
    columns: Sequence[str],
    project_id: str,
    created_at: str,
) -> Tuple[List[Dict], Dict[str, int]]:
    unique: Dict[str, Dict[str, str]] = {}
    for raw in _iter_csv(csv_path, columns):
        key = raw["stable_key"].strip()
        file_path = _source_path(project_root, raw["file"])
        if key and file_path:
            raw["file"] = file_path
            unique.setdefault(key, raw)

    rows_by_file: Dict[str, List[Dict[str, str]]] = {}
    for raw in unique.values():
        rows_by_file.setdefault(raw["file"], []).append(raw)

    rows: List[Dict] = []
    id_by_key: Dict[str, int] = {}
    for file_path, file_symbols in rows_by_file.items():
        source_lines = _read_source_lines(file_path)
        for raw in file_symbols:
            symbol_id = len(rows) + 1
            start_line = max(_int(raw["start_line"], 1), 1)
            end_line = max(_int(raw["end_line"], start_line), start_line)
            codeql_col = max(_int(raw["start_col"], 1) - 1, 0)
            name_line, name_col = _name_position(
                source_lines,
                raw["name"],
                start_line,
                codeql_col,
            )
            id_by_key[raw["stable_key"]] = symbol_id
            rows.append(
                {
                    "symbol_id": symbol_id,
                    "name": raw["name"],
                    "type": raw["type"],
                    "file": raw["file"],
                    "start_line": start_line,
                    "end_line": end_line,
                    "name_line": name_line,
                    "name_col": name_col,
                    "signature": raw["signature"],
                    "language": _language_for_file(raw["file"], raw["language"]),
                    "doc": "",
                    "container": raw["container"],
                    "qualified_name": raw["qualified_name"],
                    "project_id": project_id,
                    "created_at": created_at,
                }
            )
    return rows, id_by_key


def _file_rows(
    project_root: Path,
    file_csv: Path,
    columns: Sequence[str],
    symbols: Sequence[Dict],
) -> List[Dict]:
    paths = {str(symbol["file"]): None for symbol in symbols}
    for raw in _iter_csv(file_csv, columns):
        path = _source_path(project_root, raw["file"])
        if path:
            paths.setdefault(path, None)
    return [
        normalize_file(path, _language_for_file(path))
        for path in paths
    ]


def _dependency_rows(
    project_root: Path,
    csv_path: Path,
    columns: Sequence[str],
) -> List[Dict]:
    rows = []
    for raw in _iter_csv(csv_path, columns):
        source = _source_path(project_root, raw["source_file"])
        target = _normalize_import_target(raw["target"])
        if not source or not target:
            continue
        rows.append(
            {
                "source_file": source,
                "target_file": target,
                "type": raw["type"] or "import",
            }
        )
    return rows


def _call_rows(
    project_root: Path,
    csv_path: Path,
    columns: Sequence[str],
    id_by_key: Mapping[str, int],
    symbol_by_id: Mapping[int, Dict],
) -> Tuple[List[Dict], List[Dict], int]:
    unresolved: List[Dict] = []
    edges: List[Dict] = []
    edge_keys = set()
    skipped = 0

    for raw in _iter_csv(csv_path, columns):
        source_file = _source_path(project_root, raw["source_file"])
        if not source_file:
            skipped += 1
            continue
        call_line = max(_int(raw["call_line"]), 0)
        call_col = max(_int(raw["call_col"], 1) - 1, 0)
        unresolved.append(
            {
                "caller": raw["source_name"],
                "callee": raw["target_name"],
                "caller_file": source_file,
                "line": call_line,
                "code": raw["code"],
            }
        )

        source_id = id_by_key.get(raw["source_key"])
        target_id = id_by_key.get(raw["target_key"])
        if source_id is None or target_id is None:
            continue
        edge_key = (source_id, target_id, "calls", call_line, call_col)
        if edge_key in edge_keys:
            continue
        edge_keys.add(edge_key)
        source_symbol = symbol_by_id[source_id]
        target_symbol = symbol_by_id[target_id]
        edges.append(
            {
                "source_symbol_id": source_id,
                "target_symbol_id": target_id,
                "kind": "calls",
                "source_name": source_symbol["name"],
                "target_name": target_symbol["name"],
                "source_file": source_symbol["file"],
                "target_file": target_symbol["file"],
                "call_line": call_line,
                "call_col": call_col,
                "confidence": 1.0,
                "provenance": PROVENANCE_CALL,
                "source_impl_symbol_id": None,
                "target_impl_symbol_id": None,
                "source_impl_owner_symbol_id": None,
                "target_impl_owner_symbol_id": None,
                "impl_resolution_status": "unresolved",
                "raw_lsp": json.dumps(
                    {
                        "source_key": raw["source_key"],
                        "target_key": raw["target_key"],
                        "codeql_text": raw["code"],
                    },
                    ensure_ascii=False,
                ),
            }
        )
    return unresolved, edges, skipped


def _implementation_rows(
    csv_path: Path,
    columns: Sequence[str],
    id_by_key: Mapping[str, int],
) -> Tuple[List[Dict], int]:
    rows: List[Dict] = []
    skipped = 0
    for raw in _iter_csv(csv_path, columns):
        abstract_id = id_by_key.get(raw["abstract_key"])
        implementation_id = id_by_key.get(raw["implementation_key"])
        if abstract_id is None or implementation_id is None:
            skipped += 1
            continue
        if abstract_id == implementation_id:
            continue
        rows.append(
            {
                "abstract_symbol_id": abstract_id,
                "implementation_symbol_id": implementation_id,
                "abstract_owner_symbol_id": id_by_key.get(
                    raw["abstract_owner_key"]
                ),
                "implementation_owner_symbol_id": id_by_key.get(
                    raw["implementation_owner_key"]
                ),
                "relation_kind": raw["relation_kind"] or "implements",
                "confidence": 1.0,
                "provenance": PROVENANCE_IMPLEMENTATION,
                "is_ambiguous": 0,
                "raw_lsp": json.dumps(raw, ensure_ascii=False),
            }
        )

    counts: Dict[int, int] = {}
    for row in rows:
        abstract_id = row["abstract_symbol_id"]
        counts[abstract_id] = counts.get(abstract_id, 0) + 1
    for row in rows:
        row["is_ambiguous"] = int(counts[row["abstract_symbol_id"]] > 1)
    return rows, skipped


def _resolve_edge_implementations(
    edges: Iterable[Dict],
    implementations: Sequence[Dict],
) -> None:
    by_abstract: Dict[int, List[Tuple[int, Optional[int]]]] = {}
    for row in implementations:
        by_abstract.setdefault(row["abstract_symbol_id"], []).append(
            (
                row["implementation_symbol_id"],
                row["implementation_owner_symbol_id"],
            )
        )

    for edge in edges:
        source_impls = by_abstract.get(edge["source_symbol_id"], ())
        target_impls = by_abstract.get(edge["target_symbol_id"], ())
        if len(source_impls) == 1:
            (
                edge["source_impl_symbol_id"],
                edge["source_impl_owner_symbol_id"],
            ) = source_impls[0]
        if len(target_impls) == 1:
            (
                edge["target_impl_symbol_id"],
                edge["target_impl_owner_symbol_id"],
            ) = target_impls[0]
            edge["impl_resolution_status"] = "resolved"
        elif len(target_impls) > 1:
            edge["impl_resolution_status"] = "ambiguous"
        else:
            edge["impl_resolution_status"] = "none"


def transform_query_results(
    project_root: Path,
    csv_paths: Mapping[str, Path],
    project_id: str,
    created_at: str,
) -> CodeQLRows:
    """Transform all query files with indexed joins and no nested row matching."""
    root = project_root.resolve()
    specs = _spec_by_name()
    required = set(specs)
    missing = required - set(csv_paths)
    if missing:
        raise ValueError(f"missing decoded CodeQL result sets: {sorted(missing)}")

    symbols, id_by_key = _symbol_rows(
        root,
        csv_paths["symbols"],
        specs["symbols"].columns,
        project_id,
        created_at,
    )
    symbol_by_id = {row["symbol_id"]: row for row in symbols}
    files = _file_rows(
        root,
        csv_paths["files"],
        specs["files"].columns,
        symbols,
    )
    dependencies = _dependency_rows(
        root,
        csv_paths["dependencies"],
        specs["dependencies"].columns,
    )
    unresolved, edges, skipped_calls = _call_rows(
        root,
        csv_paths["calls"],
        specs["calls"].columns,
        id_by_key,
        symbol_by_id,
    )
    implementations, skipped_implementations = _implementation_rows(
        csv_paths["implementations"],
        specs["implementations"].columns,
        id_by_key,
    )
    _resolve_edge_implementations(edges, implementations)
    return CodeQLRows(
        files=files,
        symbols=symbols,
        dependencies=dependencies,
        unresolved_calls=unresolved,
        edges=edges,
        implementations=implementations,
        skipped_calls=skipped_calls,
        skipped_implementations=skipped_implementations,
    )
