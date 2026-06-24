import hashlib
import json
import os
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, MutableMapping, Optional, Tuple


SYMBOL_JSON_FIELDS = {
    "symbol_id",
    "name",
    "type",
    "file",
    "range",
    "name_pos",
    "signature",
    "language",
    "doc",
    "container",
}


def file_hash(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _value(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, MutableMapping):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _range_lines(symbol: Any) -> Tuple[int, int]:
    rng = _value(symbol, "range")
    if isinstance(rng, MutableMapping):
        start = rng.get("start_line")
        end = rng.get("end_line")
    else:
        start = getattr(rng, "start_line", None)
        end = getattr(rng, "end_line", None)

    start_line = int(start or 1)
    end_line = int(end or start_line)
    return start_line, end_line


def _read_lines_cached(path: str, cache: Dict[str, List[str]]) -> List[str]:
    if path not in cache:
        try:
            cache[path] = Path(path).read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            cache[path] = []
    return cache[path]


def infer_name_pos(
    file_path: str,
    name: str,
    start_line: int,
    source_cache: Optional[Dict[str, List[str]]] = None,
) -> List[int]:
    cache = source_cache if source_cache is not None else {}
    lines = _read_lines_cached(file_path, cache)
    candidate_lines = [start_line, start_line - 1, start_line + 1]

    for line_no in candidate_lines:
        if not (1 <= line_no <= len(lines)):
            continue
        col = lines[line_no - 1].find(name)
        if col >= 0:
            return [line_no, col]

    for idx, line in enumerate(lines, start=1):
        col = line.find(name)
        if col >= 0:
            return [idx, col]

    return [start_line, 0]


def normalize_symbol(
    symbol: Any,
    symbol_id: int,
    project_id: str,
    created_at: str,
    source_cache: Optional[Dict[str, List[str]]] = None,
) -> Dict[str, Any]:
    start_line, end_line = _range_lines(symbol)
    name = str(_value(symbol, "name", "") or "")
    file_path = str(_value(symbol, "file", "") or "")
    name_pos = _value(symbol, "name_pos")

    if not isinstance(name_pos, list) or len(name_pos) < 2:
        name_pos = infer_name_pos(file_path, name, start_line, source_cache)

    name_line = int(name_pos[0] or start_line)
    name_col = int(name_pos[1] or 0)
    container = str(_value(symbol, "container", "") or "")
    qualified_name = f"{container}.{name}" if container else name

    return {
        "symbol_id": int(symbol_id),
        "name": name,
        "type": str(_value(symbol, "type", "") or ""),
        "file": file_path,
        "start_line": start_line,
        "end_line": end_line,
        "name_line": name_line,
        "name_col": name_col,
        "signature": str(_value(symbol, "signature", "") or ""),
        "language": str(_value(symbol, "language", "") or ""),
        "doc": str(_value(symbol, "doc", "") or ""),
        "container": container,
        "qualified_name": qualified_name,
        "project_id": project_id,
        "created_at": created_at,
    }


def normalize_dependency(dep: Any) -> Dict[str, Any]:
    return {
        "source_file": str(_value(dep, "source_file", "") or ""),
        "target_file": str(_value(dep, "target_file", "") or ""),
        "type": str(_value(dep, "type", "") or ""),
    }


def normalize_call(call: Any, caller_file: str) -> Dict[str, Any]:
    if is_dataclass(call):
        call = asdict(call)

    if isinstance(call, MutableMapping):
        caller = call.get("caller", "")
        callee = call.get("callee", "")
        line = call.get("line") or (call.get("call_site") or {}).get("line")
        code = call.get("code") or (call.get("call_site") or {}).get("code", "")
        caller_file = call.get("caller_file") or caller_file
    else:
        caller, callee, line, code = (list(call) + ["", "", None, ""])[:4]

    return {
        "caller": str(caller or ""),
        "callee": str(callee or ""),
        "caller_file": caller_file,
        "line": int(line or 0),
        "code": str(code or ""),
    }


def normalize_edge(edge: Any) -> Dict[str, Any]:
    raw_lsp = edge.get("raw_lsp", "") if isinstance(edge, MutableMapping) else ""
    if raw_lsp and not isinstance(raw_lsp, str):
        raw_lsp = json.dumps(raw_lsp, ensure_ascii=False)
    return {
        "source_symbol_id": int(edge["source_symbol_id"]),
        "target_symbol_id": int(edge["target_symbol_id"]),
        "kind": str(edge.get("kind") or "calls"),
        "source_name": str(edge.get("source_name") or ""),
        "target_name": str(edge.get("target_name") or ""),
        "source_file": str(edge.get("source_file") or ""),
        "target_file": str(edge.get("target_file") or ""),
        "call_line": int(edge.get("call_line") or 0),
        "call_col": int(edge.get("call_col") or 0),
        "confidence": float(edge.get("confidence") or 1.0),
        "provenance": str(edge.get("provenance") or "java_lsp_call_hierarchy"),
        "raw_lsp": raw_lsp,
    }


def normalize_file(path: str, language: str) -> Dict[str, Any]:
    stat = os.stat(path)
    return {
        "path": os.path.abspath(path),
        "language": language,
        "size_bytes": int(stat.st_size),
        "mtime": float(stat.st_mtime),
        "content_hash": file_hash(path),
    }


def symbol_row_to_json(row: MutableMapping[str, Any]) -> Dict[str, Any]:
    return {
        "symbol_id": row["symbol_id"],
        "name": row["name"],
        "type": row["type"],
        "file": row["file"],
        "range": {
            "start_line": row["start_line"],
            "end_line": row["end_line"],
        },
        "name_pos": [row["name_line"], row["name_col"]],
        "signature": row["signature"] or "",
        "language": row["language"] or "",
        "doc": row["doc"] or "",
        "container": row["container"] or "",
    }


def dependency_row_to_json(row: MutableMapping[str, Any]) -> Dict[str, Any]:
    return {
        "source_file": row["source_file"],
        "target_file": row["target_file"],
        "type": row["type"],
    }


def ensure_symbol_json_schema(symbols: Iterable[Dict[str, Any]]) -> None:
    for symbol in symbols:
        missing = SYMBOL_JSON_FIELDS - set(symbol)
        if missing:
            raise ValueError(f"symbol {symbol.get('symbol_id')} missing fields: {sorted(missing)}")
