import json
import os
import time
from multiprocessing import Pool
from typing import Any, Dict, Iterable, List, Optional, Tuple

from definition import JDTLS_PATH
from parsers.parallel_java_lsp_client import ParallelJavaLSPClient


PROVENANCE = "java_lsp_call_hierarchy"


def _base_name(name: Any) -> str:
    return str(name or "").strip().split("(", 1)[0].strip()


def _uri_to_path(uri: Any) -> str:
    uri = str(uri or "").strip()
    if uri.startswith("file://"):
        return os.path.abspath(uri[len("file://"):])
    return os.path.abspath(uri) if uri else ""


def _item_start_line(item: Dict[str, Any]) -> int:
    rng = item.get("range") or {}
    start = rng.get("start") or {}
    return int(start.get("line", -1)) + 1


def _call_site(call: Dict[str, Any]) -> Tuple[int, int]:
    ranges = call.get("fromRanges") or []
    if not ranges:
        return 0, 0
    start = (ranges[0] or {}).get("start") or {}
    return int(start.get("line", -1)) + 1, int(start.get("character", 0))


class SymbolMatcher:
    def __init__(self, symbols: Iterable[Dict[str, Any]]):
        self.by_file_name: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
        for sym in symbols:
            file_path = os.path.abspath(str(sym.get("file") or ""))
            name = _base_name(sym.get("name"))
            if not file_path or not name:
                continue
            self.by_file_name.setdefault((file_path, name), []).append(sym)

    def match_lsp_item(self, item: Dict[str, Any]) -> Optional[Tuple[Dict[str, Any], float]]:
        file_path = _uri_to_path(item.get("uri"))
        name = _base_name(item.get("name"))
        if not file_path or not name:
            return None

        candidates = self.by_file_name.get((file_path, name), [])
        if not candidates:
            return None

        start_line = _item_start_line(item)
        ranged = [
            sym for sym in candidates
            if int(sym.get("start_line") or 0) <= start_line <= int(sym.get("end_line") or 0)
        ]
        if len(ranged) == 1:
            return ranged[0], 1.0
        if len(candidates) == 1:
            return candidates[0], 0.95
        return None


def _prepare_call_hierarchy_item(
    client: ParallelJavaLSPClient,
    symbol: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    file_path = os.path.abspath(str(symbol.get("file") or ""))
    line = max(int(symbol.get("name_line") or symbol.get("start_line") or 1) - 1, 0)
    character = max(int(symbol.get("name_col") or 0), 0)
    result = client._send_request(
        "textDocument/prepareCallHierarchy",
        {
            "textDocument": {"uri": f"file://{file_path}"},
            "position": {"line": line, "character": character},
        },
    )
    items = result.get("result") or []
    if not items:
        return None
    return items[0]


def _outgoing_calls(
    client: ParallelJavaLSPClient,
    item: Dict[str, Any],
) -> List[Dict[str, Any]]:
    result = client._send_request("callHierarchy/outgoingCalls", {"item": item})
    calls = result.get("result") or []
    return calls if isinstance(calls, list) else []


def _edge_key(edge: Dict[str, Any]) -> Tuple[int, int, str, int, int]:
    return (
        int(edge["source_symbol_id"]),
        int(edge["target_symbol_id"]),
        str(edge["kind"]),
        int(edge.get("call_line") or 0),
        int(edge.get("call_col") or 0),
    )


def _build_edges_for_chunk(
    worker_id: int,
    project_root: str,
    methods_chunk: List[Dict[str, Any]],
    all_symbols: List[Dict[str, Any]],
    request_timeout: float,
    warmup_seconds: float,
    jdtls_path: str,
) -> Dict[str, Any]:
    client = ParallelJavaLSPClient(
        project_root=project_root,
        jdtls_path=jdtls_path,
        request_timeout=request_timeout,
        verbose=False,
        worker_id=str(worker_id),
    )
    matcher = SymbolMatcher(all_symbols)
    edges: List[Dict[str, Any]] = []
    errors: List[str] = []
    processed = 0

    try:
        client.start()
        if warmup_seconds > 0:
            time.sleep(warmup_seconds)

        for sym in methods_chunk:
            processed += 1
            try:
                item = _prepare_call_hierarchy_item(client, sym)
                if not item:
                    errors.append(f"prepare-empty:{sym.get('symbol_id')}:{sym.get('name')}")
                    continue
                for call in _outgoing_calls(client, item):
                    target_item = call.get("to") or {}
                    matched = matcher.match_lsp_item(target_item)
                    if not matched:
                        continue
                    target, confidence = matched
                    call_line, call_col = _call_site(call)
                    edges.append(
                        {
                            "source_symbol_id": sym["symbol_id"],
                            "target_symbol_id": target["symbol_id"],
                            "kind": "calls",
                            "source_name": sym.get("name", ""),
                            "target_name": target.get("name", ""),
                            "source_file": sym.get("file", ""),
                            "target_file": target.get("file", ""),
                            "call_line": call_line,
                            "call_col": call_col,
                            "confidence": confidence,
                            "provenance": PROVENANCE,
                            "raw_lsp": json.dumps(call, ensure_ascii=False),
                        }
                    )
            except Exception as exc:
                errors.append(f"method-error:{sym.get('symbol_id')}:{sym.get('name')}:{exc}")
    except Exception as exc:
        errors.append(f"worker-start-error:{worker_id}:{exc}")
    finally:
        client.stop()

    return {"worker_id": worker_id, "processed": processed, "edges": edges, "errors": errors}


def build_java_lsp_edges(
    project_root: str,
    symbols: List[Dict[str, Any]],
    workers: int = 4,
    request_timeout: float = 10.0,
    warmup_seconds: float = 8.0,
    jdtls_path: str = JDTLS_PATH,
) -> Dict[str, Any]:
    java_methods = [
        sym for sym in symbols
        if sym.get("language") == "java"
        and sym.get("type") in {"method", "function"}
        and str(sym.get("file", "")).startswith(os.path.abspath(project_root))
    ]
    if not java_methods:
        return {"edges": [], "errors": ["no-java-methods"], "processed": 0}

    worker_count = max(1, min(int(workers or 1), len(java_methods)))
    chunks: List[List[Dict[str, Any]]] = [[] for _ in range(worker_count)]
    for idx, sym in enumerate(java_methods):
        chunks[idx % worker_count].append(sym)

    args = [
        (idx, os.path.abspath(project_root), chunk, symbols, request_timeout, warmup_seconds, jdtls_path)
        for idx, chunk in enumerate(chunks)
        if chunk
    ]

    if len(args) == 1:
        results = [_build_edges_for_chunk(*args[0])]
    else:
        with Pool(processes=len(args)) as pool:
            results = pool.starmap(_build_edges_for_chunk, args)

    edges_by_key: Dict[Tuple[int, int, str, int, int], Dict[str, Any]] = {}
    errors: List[str] = []
    processed = 0
    for result in results:
        processed += int(result.get("processed") or 0)
        errors.extend(result.get("errors") or [])
        for edge in result.get("edges") or []:
            edges_by_key.setdefault(_edge_key(edge), edge)

    return {"edges": list(edges_by_key.values()), "errors": errors, "processed": processed}
