"""
Relation filter — filter candidate symbols by caller / callee constraints
from SemQL relation conditions.

SemQL relation condition 中的 caller/callee 字段支持两种格式：
- "file_name:func_name" — 指定了 caller/callee 的文件名和函数名
- "func_name" — 仅指定函数名，无文件名

当文件名+函数名能唯一定位项目中的函数时，单次 LSP 查询即可；
当无法唯一定位时，对候选集分片，用多个 ParallelJavaLSPClient 并发查询。
"""

import os
import re
import sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Any, List, Optional, Set, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from definition import PROJECT_PATH, JDTLS_PATH,OUTPUT_DIR,PROJECT_NAME
from parsers.java_lsp_client import JavaLSPClient, JavaCallChainExtractor
from parsers.parallel_java_lsp_client import ParallelJavaLSPClient, ParallelJavaCallChainExtractor
from parsers.registry import parse_file_with_registry
from query_processing.semql_utils import extract_semql_text_terms
from utils.file_utils import load_res
from filters.relation_graph_store import RelationGraphStore, filter_candidates_with_edges

# 并发查询时的 worker 数量
DEFAULT_WORKER_COUNT = 4


def _parse_call_field(raw: str) -> Tuple[Optional[str], Optional[str], Optional[int]]:
    """
    Parse a caller/callee field value into (file_name, func_name, hop_count).

    Preferred format "file_name:func_name:hop_count" -> (file_name, func_name, hop_count)
    Missing components should be written as "None", e.g. "None:login:2".
    Legacy formats "file_name:func_name" and "func_name" are still accepted.
    """
    raw = str(raw).strip()
    if not raw or raw.lower() == "none":
        return None, None, None

    def _none_if_empty(value: str) -> Optional[str]:
        value = value.strip()
        if not value or value.lower() == "none":
            return None
        return value

    def _parse_hop_count(value: Optional[str]) -> Optional[int]:
        if value is None:
            return None
        value = value.strip()
        if not value or value.lower() == "none":
            return None
        try:
            return int(value)
        except ValueError:
            return None

    parts = raw.split(":")
    if len(parts) >= 3:
        file_name = _none_if_empty(parts[0])
        func_name = _none_if_empty(parts[1])
        hop_count = _parse_hop_count(parts[2])
        return file_name, func_name, hop_count
    if len(parts) == 2:
        file_name = _none_if_empty(parts[0])
        func_name = _none_if_empty(parts[1])
        return file_name, func_name, None
    return None, _none_if_empty(raw), None


def _base_func_name(name: Any) -> str:
    """Normalize LSP/candidate method names to the bare function name."""
    return str(name or "").strip().split("(", 1)[0].strip()


def _uri_to_path(uri: Any) -> str:
    uri = str(uri or "").strip()
    if uri.startswith("file://"):
        return uri[len("file://"):]
    return uri


def _symbol_key(symbol_id: Any) -> str:
    return str(symbol_id)


def _load_symbols_by_id(candidate_path: str) -> Dict[str, Dict[str, Any]]:
    """Load sibling symbols_index.json and index symbols by symbol_id."""
    symbols_path=Path(f"{OUTPUT_DIR}/{PROJECT_NAME}/symbols_index.json")
    if not symbols_path.exists():
        return {}

    symbols = load_res(str(symbols_path))
    if not isinstance(symbols, list):
        return {}

    return {
        _symbol_key(sym.get("symbol_id")): sym
        for sym in symbols
        if isinstance(sym, dict) and sym.get("symbol_id") is not None
    }


def _to_symbols_index_schema(
    candidates: List[Dict[str, Any]],
    candidate_path: str,
) -> List[Dict[str, Any]]:
    """
    Return symbols using the original symbols_index.json records.

    Candidate files may add transient fields such as matched_subtokens and may
    omit fields such as name_pos. The public filter output should match the
    project symbol index schema.
    """
    symbols_by_id = _load_symbols_by_id(candidate_path)
    if not symbols_by_id:
        return candidates

    normalized: List[Dict[str, Any]] = []
    for candidate in candidates:
        sid = candidate.get("symbol_id")
        symbol = symbols_by_id.get(_symbol_key(sid))
        normalized.append(symbol if symbol is not None else candidate)
    return normalized


def _extract_callers_from_semql(
    semQL: Dict[str, Any],
    property_name: str,
) -> List[Tuple[Optional[str], str, Optional[int]]]:
    """Extract all caller field values from relation conditions."""
    raw_values = extract_semql_text_terms(
        semQL,
        properties=(property_name,),
        condition_type="relation",
        term_name="caller",
    )
    result: List[Tuple[Optional[str], str, Optional[int]]] = []
    for raw in raw_values:
        fn, func, hop_count = _parse_call_field(raw)
        if func:
            result.append((fn, func, hop_count))
    return result


def _extract_callees_from_semql(
    semQL: Dict[str, Any],
    property_name: str,
) -> List[Tuple[Optional[str], str, Optional[int]]]:
    """Extract all callee field values from relation conditions."""
    raw_values = extract_semql_text_terms(
        semQL,
        properties=(property_name,),
        condition_type="relation",
        term_name="callee",
    )
    result: List[Tuple[Optional[str], str, Optional[int]]] = []
    for raw in raw_values:
        fn, func, hop_count = _parse_call_field(raw)
        if func:
            result.append((fn, func, hop_count))
    return result


def _extract_roles_from_semql(
    semQL: Dict[str, Any],
    property_name: str,
) -> List[str]:
    """Extract supported graph_constraint.role values from relation conditions."""
    conditions = semQL.get("conditions")
    if not isinstance(conditions, dict):
        return []

    relation_conditions = conditions.get("relation")
    if not isinstance(relation_conditions, dict):
        return []

    property_conditions = relation_conditions.get(property_name)
    if not isinstance(property_conditions, list):
        return []

    roles: List[str] = []
    for condition in property_conditions:
        if not isinstance(condition, dict):
            continue
        graph_constraint = condition.get("graph_constraint")
        if not isinstance(graph_constraint, dict):
            continue
        role = str(graph_constraint.get("role") or "").strip().lower()
        if role in {"entry_point", "leaf", "isolate"} and role not in roles:
            roles.append(role)
    return roles


def _resolve_abs_path(file_path: str) -> str:
    if os.path.isabs(file_path):
        return file_path
    return os.path.join(PROJECT_PATH, file_path)


def _find_file_in_project(file_name: str) -> List[str]:
    """
    Find all files matching file_name under PROJECT_PATH.
    Returns a list of absolute paths.
    """
    matches: List[str] = []
    for root, _dirs, files in os.walk(PROJECT_PATH):
        for f in files:
            if f == file_name:
                matches.append(os.path.abspath(os.path.join(root, f)))
    return matches


def _file_contains_func(file_path: str, func_name: str) -> bool:
    """
    Check whether a file contains a function with the exact name func_name.
    Uses the project's parser (tree-sitter based) for accurate symbol extraction.
    """
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            source = f.read()
    except Exception:
        return False

    file_rel = os.path.relpath(file_path, PROJECT_PATH)
    symbols, _calls, _deps = parse_file_with_registry(file_rel, file_path, source)
    for sym in symbols:
        if sym.name == func_name:
            return True

    method_pattern = re.compile(r"\b" + re.escape(func_name) + r"\s*\(")
    return method_pattern.search(source) is not None


def _can_resolve_to_unique_file(
    file_name: str, func_name: str
) -> Optional[str]:
    """
    Try to uniquely resolve a file_name + func_name pair to a single file.

    Returns the absolute path if exactly one file with that name exists AND
    contains func_name. If multiple files share the same name, tries to
    narrow down by checking which ones actually contain func_name (via
    tree-sitter parsing). If still ambiguous, returns None.
    """
    matches = _find_file_in_project(file_name)
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0] if _file_contains_func(matches[0], func_name) else None

    # Multiple files with same name — narrow by func_name
    narrowed = [
        path for path in matches if _file_contains_func(path, func_name)
    ]
    if len(narrowed) == 1:
        return narrowed[0]
    return None


# ---------------------------------------------------------------------------
# Case 1: caller can be uniquely resolved — single LSP query
# ---------------------------------------------------------------------------

def _filter_by_caller_with_path(
    candidates: List[Dict[str, Any]],
    callers_with_path: List[Tuple[str, str, Optional[int]]],
    layer: Optional[int],
) -> Set[str]:
    """
    For each (abs_path, func_name), call get_callees once,
    then collect candidate symbol_ids whose name appears in the callee set.
    """
    kept: Set[str] = set()
    lsp_client = JavaLSPClient(project_root=PROJECT_PATH, jdtls_path=JDTLS_PATH, verbose=False)
    lsp_client.start()
    try:
        extractor = JavaCallChainExtractor(lsp_client)
        for abs_path, func_name, hop_count in callers_with_path:
            query_layer = hop_count if hop_count is not None else layer
            callees = extractor.get_callees(abs_path, func_name, layer=query_layer)
            callee_keys: Set[Tuple[str, str]] = {
                (_base_func_name(c.get("name")), _uri_to_path(c.get("uri")))
                for c in callees
                if c.get("name") and c.get("uri")
            }
            for sym in candidates:
                sid = sym.get("symbol_id")
                if not sid:
                    continue
                sym_key = (
                    _base_func_name(sym.get("name")),
                    str(sym.get("file", "")).strip(),
                )
                if sym_key in callee_keys:
                    kept.add(sid)
    finally:
        lsp_client.stop()
    return kept


# ---------------------------------------------------------------------------
# Case 2: caller cannot be uniquely resolved — concurrent LSP queries
# ---------------------------------------------------------------------------

def _filter_by_caller_without_path(
    candidates: List[Dict[str, Any]],
    caller_entries: List[Tuple[str, Optional[int]]],
    layer: Optional[int],
    worker_count: int = DEFAULT_WORKER_COUNT,
) -> Set[str]:
    """
    For each candidate symbol, check whether its callers contain the target name.
    Uses a thread pool of ParallelJavaLSPClient instances for concurrency.
    """
    kept: Set[str] = set()
    targets_by_layer: Dict[Optional[int], Set[str]] = {}
    for name, hop_count in caller_entries:
        query_layer = hop_count if hop_count is not None else layer
        targets_by_layer.setdefault(query_layer, set()).add(_base_func_name(name))

    def _query_one(sym: Dict[str, Any]) -> Optional[str]:
        sym_file = str(sym.get("file", "")).strip()
        sym_name = str(sym.get("name", "")).strip()
        sid = sym.get("symbol_id")
        if not sym_file or not sym_name or not sid:
            return None

        abs_path = _resolve_abs_path(sym_file)
        client = ParallelJavaLSPClient(project_root=PROJECT_PATH, jdtls_path=JDTLS_PATH)
        client.start()
        try:
            extractor = ParallelJavaCallChainExtractor(client)
            for query_layer, target_set in targets_by_layer.items():
                callers = extractor.get_callers(abs_path, sym_name, layer=query_layer)
                caller_name_set: Set[str] = {
                    _base_func_name(c.get("name"))
                    for c in callers
                    if c.get("name")
                }
                if caller_name_set & target_set:
                    return sid
        finally:
            client.stop()
        return None

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = {executor.submit(_query_one, sym): sym for sym in candidates}
        for future in as_completed(futures):
            sid = future.result()
            if sid:
                kept.add(sid)

    return kept


# ---------------------------------------------------------------------------
# Case 1: callee can be uniquely resolved — single LSP query
# ---------------------------------------------------------------------------

def _filter_by_callee_with_path(
    candidates: List[Dict[str, Any]],
    callees_with_path: List[Tuple[str, str, Optional[int]]],
    layer: Optional[int],
) -> Set[str]:
    """
    For each (abs_path, func_name), call get_callers once,
    then collect candidate symbol_ids whose name appears in the caller set.
    """
    kept: Set[str] = set()
    lsp_client = JavaLSPClient(project_root=PROJECT_PATH, jdtls_path=JDTLS_PATH, verbose=False)
    lsp_client.start()
    try:
        extractor = JavaCallChainExtractor(lsp_client)
        for abs_path, func_name, hop_count in callees_with_path:
            query_layer = hop_count if hop_count is not None else layer
            callers = extractor.get_callers(abs_path, func_name, layer=query_layer)
            caller_keys: Set[Tuple[str, str]] = {
                (_base_func_name(c.get("name")), _uri_to_path(c.get("uri")))
                for c in callers
                if c.get("name") and c.get("uri")
            }
            for sym in candidates:
                sid = sym.get("symbol_id")
                if not sid:
                    continue
                sym_key = (
                    _base_func_name(sym.get("name")),
                    str(sym.get("file", "")).strip(),
                )
                if sym_key in caller_keys:
                    kept.add(sid)
    finally:
        lsp_client.stop()
    return kept


# ---------------------------------------------------------------------------
# Case 2: callee cannot be uniquely resolved — concurrent LSP queries
# ---------------------------------------------------------------------------

def _filter_by_callee_without_path(
    candidates: List[Dict[str, Any]],
    callee_entries: List[Tuple[str, Optional[int]]],
    layer: Optional[int],
    worker_count: int = DEFAULT_WORKER_COUNT,
) -> Set[str]:
    """
    For each candidate symbol, check whether its callees contain the target name.
    Uses a thread pool of ParallelJavaLSPClient instances for concurrency.
    """
    kept: Set[str] = set()
    targets_by_layer: Dict[Optional[int], Set[str]] = {}
    for name, hop_count in callee_entries:
        query_layer = hop_count if hop_count is not None else layer
        targets_by_layer.setdefault(query_layer, set()).add(_base_func_name(name))

    def _query_one(sym: Dict[str, Any]) -> Optional[str]:
        sym_file = str(sym.get("file", "")).strip()
        sym_name = str(sym.get("name", "")).strip()
        sid = sym.get("symbol_id")
        if not sym_file or not sym_name or not sid:
            return None

        abs_path = _resolve_abs_path(sym_file)
        client = ParallelJavaLSPClient(project_root=PROJECT_PATH, jdtls_path=JDTLS_PATH)
        client.start()
        try:
            extractor = ParallelJavaCallChainExtractor(client)
            for query_layer, target_set in targets_by_layer.items():
                callees = extractor.get_callees(abs_path, sym_name, layer=query_layer)
                callee_name_set: Set[str] = {
                    _base_func_name(c.get("name"))
                    for c in callees
                    if c.get("name")
                }
                if callee_name_set & target_set:
                    return sid
        finally:
            client.stop()
        return None

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = {executor.submit(_query_one, sym): sym for sym in candidates}
        for future in as_completed(futures):
            sid = future.result()
            if sid:
                kept.add(sid)

    return kept


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def role_filter(
    semQL_path: str,
    candidate_path: str,
    property_name: str = "include",
) -> List[Dict[str, Any]]:
    """
    Filter function candidates by graph_constraint.role using direct call edges.

    Roles:
      - entry_point: in_degree == 0 and out_degree > 0
      - leaf: out_degree == 0 and in_degree > 0
      - isolate: in_degree == 0 and out_degree == 0

    Non-function candidates are left unchanged because call-graph roles do not
    apply to them. Interface and implementation symbols are treated as one
    logical function when calculating degrees.
    """
    semQL = load_res(semQL_path)
    candidates = load_res(candidate_path)
    if not candidates:
        return []

    roles = _extract_roles_from_semql(semQL, property_name)
    if not roles:
        return _to_symbols_index_schema(candidates, candidate_path)

    store = RelationGraphStore.open_if_ready()
    if store is None:
        return _to_symbols_index_schema(candidates, candidate_path)

    try:
        filtered: List[Dict[str, Any]] = []
        for candidate in candidates:
            symbol_type = str(candidate.get("type") or "").strip().lower()
            if symbol_type not in {"function", "method"}:
                filtered.append(candidate)
                continue

            symbol_id = candidate.get("symbol_id")
            if symbol_id is None:
                continue

            in_degree, out_degree = store.symbol_degrees(
                int(symbol_id),
                func_name=candidate.get("name") or candidate.get("signature"),
            )
            role_matches = {
                "entry_point": in_degree == 0 and out_degree > 0,
                "leaf": out_degree == 0 and in_degree > 0,
                "isolate": in_degree == 0 and out_degree == 0,
            }
            if any(role_matches[role] for role in roles):
                filtered.append(candidate)

        return _to_symbols_index_schema(filtered, candidate_path)
    finally:
        store.close()


def caller_filter(
    semQL_path: str,
    candidate_path: str,
    property_name: str = "include",
    layer: Optional[int] = 1,
    worker_count: int = DEFAULT_WORKER_COUNT,
) -> List[Dict[str, Any]]:
    """
    Filter candidates by caller constraints from SemQL relation conditions.

    Steps:
    1. Load semQL from file, extract caller field values from relation conditions.
    2. If caller has file_name → try to resolve to unique file + single LSP get_callees.
    3. If caller has NO file_name OR cannot be uniquely resolved → concurrent LSP get_callers per candidate.

    Args:
        semQL_path: path to the semQL.json file.
        candidate_path: path to the JSON file containing candidate symbols.
        property_name: "include" or "exclude".
        layer: max call hierarchy depth (None = unlimited).
        worker_count: concurrent workers for no-file_path queries.

    Returns:
        Filtered list of candidate symbols that satisfy the caller constraints.
    """
    semQL = load_res(semQL_path)
    caller_entries = _extract_callers_from_semql(semQL, property_name)
    if not caller_entries:
        candidates = load_res(candidate_path)
        return _to_symbols_index_schema(candidates, candidate_path)

    candidates = load_res(candidate_path)
    if not candidates:
        return []

    edge_kept_ids = filter_candidates_with_edges(
        candidates=candidates,
        relation_entries=caller_entries,
        relation_kind="caller",
        layer=layer,
    )
    if edge_kept_ids is not None:
        filtered = [sym for sym in candidates if _symbol_key(sym.get("symbol_id")) in edge_kept_ids]
        return _to_symbols_index_schema(filtered, candidate_path)

    # Separate entries that can be uniquely resolved from those that cannot
    callers_with_path: List[Tuple[str, str, Optional[int]]] = []
    callers_without_path: List[Tuple[str, Optional[int]]] = []

    for file_name, func_name, hop_count in caller_entries:
        if file_name:
            abs_path = _can_resolve_to_unique_file(file_name, func_name)
            if abs_path:
                callers_with_path.append((abs_path, func_name, hop_count))
                continue
        callers_without_path.append((func_name, hop_count))

    kept_ids: Set[str] = set()

    # Case 1: single LSP query per caller
    if callers_with_path:
        kept_ids |= _filter_by_caller_with_path(candidates, callers_with_path, layer)

    # Case 2: concurrent queries per candidate
    if callers_without_path:
        kept_ids |= _filter_by_caller_without_path(
            candidates, callers_without_path, layer, worker_count=worker_count
        )

    if kept_ids:
        filtered = [sym for sym in candidates if sym.get("symbol_id") in kept_ids]
        return _to_symbols_index_schema(filtered, candidate_path)
    return _to_symbols_index_schema(candidates, candidate_path)


def callee_filter(
    semQL_path: str,
    candidate_path: str,
    property_name: str = "include",
    layer: Optional[int] = 1,
    worker_count: int = DEFAULT_WORKER_COUNT,
) -> List[Dict[str, Any]]:
    """
    Filter candidates by callee constraints from SemQL relation conditions.

    Steps:
    1. Load semQL from file, extract callee field values from relation conditions.
    2. If callee has file_name → try to resolve to unique file + single LSP get_callers.
    3. If callee has NO file_name OR cannot be uniquely resolved → concurrent LSP get_callees per candidate.

    Args:
        semQL_path: path to the semQL.json file.
        candidate_path: path to the JSON file containing candidate symbols.
        property_name: "include" or "exclude".
        layer: max call hierarchy depth (None = unlimited).
        worker_count: concurrent workers for no-file_path queries.

    Returns:
        Filtered list of candidate symbols that satisfy the callee constraints,
        normalized to the symbols_index.json schema.
    """
    semQL = load_res(semQL_path)
    callee_entries = _extract_callees_from_semql(semQL, property_name)
    if not callee_entries:
        candidates = load_res(candidate_path)
        return _to_symbols_index_schema(candidates, candidate_path)

    candidates = load_res(candidate_path)
    if not candidates:
        return []

    edge_kept_ids = filter_candidates_with_edges(
        candidates=candidates,
        relation_entries=callee_entries,
        relation_kind="callee",
        layer=layer,
    )
    if edge_kept_ids is not None:
        filtered = [sym for sym in candidates if _symbol_key(sym.get("symbol_id")) in edge_kept_ids]
        return _to_symbols_index_schema(filtered, candidate_path)

    callees_with_path: List[Tuple[str, str, Optional[int]]] = []
    callees_without_path: List[Tuple[str, Optional[int]]] = []

    for file_name, func_name, hop_count in callee_entries:
        if file_name:
            abs_path = _can_resolve_to_unique_file(file_name, func_name)
            if abs_path:
                callees_with_path.append((abs_path, func_name, hop_count))
                continue
        callees_without_path.append((func_name, hop_count))

    kept_ids: Set[str] = set()

    # Case 1: single LSP query per callee
    if callees_with_path:
        kept_ids |= _filter_by_callee_with_path(candidates, callees_with_path, layer)

    # Case 2: concurrent queries per candidate
    if callees_without_path:
        kept_ids |= _filter_by_callee_without_path(
            candidates, callees_without_path, layer, worker_count=worker_count
        )

    if kept_ids:
        filtered = [sym for sym in candidates if sym.get("symbol_id") in kept_ids]
        return _to_symbols_index_schema(filtered, candidate_path)
    return _to_symbols_index_schema(candidates, candidate_path)


if __name__ == "__main__":
    import json

    print(json.dumps(role_filter(
        semQL_path="/Users/huangzhuochen/PycharmProjects/CodeSearch/output/youlai-boot-master/semQL_test.json",
        candidate_path="/Users/huangzhuochen/PycharmProjects/CodeSearch/output/youlai-boot-master/filtered_by_type.json",
        property_name="include",
        # layer=1,
        # worker_count=4,
    ),
    indent=4 ))
