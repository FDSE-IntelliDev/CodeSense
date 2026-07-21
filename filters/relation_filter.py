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

from definition import PROJECT_PATH, JDTLS_PATH, PROJECT_OUTPUT_DIR, QUERY_OUTPUT_DIR
from parsers.java_lsp_client import JavaLSPClient, JavaCallChainExtractor
from parsers.parallel_java_lsp_client import ParallelJavaLSPClient, ParallelJavaCallChainExtractor
from parsers.registry import parse_file_with_registry
from query_processing.semql_utils import extract_semql_text_terms
from utils.file_utils import load_res
from filters.relation_graph_store import (
    RelationGraphStore,
    filter_candidates_with_store,
)

# 并发查询时的 worker 数量
DEFAULT_WORKER_COUNT = 4


def _parse_call_field(raw: str) -> Tuple[Optional[str], Optional[str]]:
    """Parse ``file_name:func_name`` or a bare function name."""
    raw = str(raw).strip()
    if not raw or raw.lower() == "none":
        return None, None

    def _none_if_empty(value: str) -> Optional[str]:
        value = value.strip()
        if not value or value.lower() == "none":
            return None
        return value

    parts = raw.split(":")
    if len(parts) == 2:
        file_name = _none_if_empty(parts[0])
        func_name = _none_if_empty(parts[1])
        return file_name, func_name
    if len(parts) == 1:
        return None, _none_if_empty(raw)
    return None, None


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


def _project_output_dir_from_candidate_path(candidate_path: str) -> Path:
    """Infer the project-level output dir from a candidate result path.

    Online result files now live in ``output/<project>/query_<id>/`` while
    project-level indexes/graphs remain in ``output/<project>/``. This helper
    keeps relation filters compatible with both the old flat layout and the new
    per-query layout.
    """
    parent = Path(candidate_path).resolve().parent
    if parent.name.startswith("query_"):
        return parent.parent
    return parent


def _codegraph_path_from_candidate_path(candidate_path: str) -> str:
    return str(_project_output_dir_from_candidate_path(candidate_path) / "codegraph.sqlite")


def _load_symbols_by_id(candidate_path: str) -> Dict[str, Dict[str, Any]]:
    """Load sibling symbols_index.json and index symbols by symbol_id."""
    symbols_path = _project_output_dir_from_candidate_path(candidate_path) / "symbols_index.json"
    if not symbols_path.exists():
        symbols_path = Path(PROJECT_OUTPUT_DIR) / "symbols_index.json"
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
) -> List[Tuple[Optional[str], str]]:
    """Extract all caller field values from relation conditions."""
    raw_values = extract_semql_text_terms(
        semQL,
        properties=(property_name,),
        condition_type="relation",
        term_name="caller",
    )
    result: List[Tuple[Optional[str], str]] = []
    for raw in raw_values:
        fn, func = _parse_call_field(raw)
        if func:
            result.append((fn, func))
    return result


def _extract_callees_from_semql(
    semQL: Dict[str, Any],
    property_name: str,
) -> List[Tuple[Optional[str], str]]:
    """Extract all callee field values from relation conditions."""
    raw_values = extract_semql_text_terms(
        semQL,
        properties=(property_name,),
        condition_type="relation",
        term_name="callee",
    )
    result: List[Tuple[Optional[str], str]] = []
    for raw in raw_values:
        fn, func = _parse_call_field(raw)
        if func:
            result.append((fn, func))
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
    resolved_callers: List[Tuple[str, str]],
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
        for abs_path, func_name in resolved_callers:
            callees = extractor.get_callees(abs_path, func_name, layer=layer)
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
    unresolved_callers: List[str],
    layer: Optional[int],
    worker_count: int = DEFAULT_WORKER_COUNT,
) -> Set[str]:
    """
    For each candidate symbol, check whether its callers contain the target name.
    Uses a thread pool of ParallelJavaLSPClient instances for concurrency.
    """
    kept: Set[str] = set()
    target_names = {_base_func_name(name) for name in unresolved_callers}

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
            callers = extractor.get_callers(abs_path, sym_name, layer=layer)
            caller_name_set: Set[str] = {
                _base_func_name(c.get("name"))
                for c in callers
                if c.get("name")
            }
            if caller_name_set & target_names:
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
    resolved_callees: List[Tuple[str, str]],
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
        for abs_path, func_name in resolved_callees:
            callers = extractor.get_callers(abs_path, func_name, layer=layer)
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
    unresolved_callees: List[str],
    layer: Optional[int],
    worker_count: int = DEFAULT_WORKER_COUNT,
) -> Set[str]:
    """
    For each candidate symbol, check whether its callees contain the target name.
    Uses a thread pool of ParallelJavaLSPClient instances for concurrency.
    """
    kept: Set[str] = set()
    target_names = {_base_func_name(name) for name in unresolved_callees}

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
            callees = extractor.get_callees(abs_path, sym_name, layer=layer)
            callee_name_set: Set[str] = {
                _base_func_name(c.get("name"))
                for c in callees
                if c.get("name")
            }
            if callee_name_set & target_names:
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

def filter_candidates_by_roles(
    candidates: List[Dict[str, Any]],
    roles: List[str],
    candidate_path: str,
    graph_store: Optional[RelationGraphStore] = None,
    preserve_non_applicable: bool = True,
) -> List[Dict[str, Any]]:
    """Apply graph roles while handling non-callable candidates explicitly.

    Non-callable symbols do not have a call-graph role. They remain valid while
    evaluating an include constraint, but must not be reported as matches for
    an exclude constraint.
    """
    normalized_roles = {
        str(role).strip().lower()
        for role in roles
        if str(role).strip().lower() in {"entry_point", "leaf", "isolate"}
    }
    if not candidates or not normalized_roles:
        return list(candidates)

    store = graph_store
    owns_store = store is None
    if store is None:
        store = RelationGraphStore.open_if_ready(
            _codegraph_path_from_candidate_path(candidate_path)
        )
    if store is None:
        return list(candidates) if preserve_non_applicable else []

    try:
        kept_candidates: List[Dict[str, Any]] = []
        for candidate in candidates:
            symbol_type = str(candidate.get("type") or "").strip().lower()
            if symbol_type not in {"function", "method"}:
                if preserve_non_applicable:
                    kept_candidates.append(candidate)
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
            if any(role_matches[role] for role in normalized_roles):
                kept_candidates.append(candidate)
        return kept_candidates
    finally:
        if owns_store:
            store.close()


def _filter_candidates_by_call_entries(
    candidates: List[Dict[str, Any]],
    relation_entries: List[Tuple[Optional[str], str]],
    relation_kind: str,
    candidate_path: str,
    layer: Optional[int],
    worker_count: int,
    graph_store: Optional[RelationGraphStore] = None,
    preserve_on_empty_fallback: bool = True,
) -> List[Dict[str, Any]]:
    """Apply normalized caller/callee entries with the existing LSP fallback."""
    if not candidates or not relation_entries:
        return list(candidates)

    store = graph_store
    owns_store = store is None
    if store is None:
        store = RelationGraphStore.open_if_ready(
            _codegraph_path_from_candidate_path(candidate_path)
        )

    edge_kept_ids = None
    if store is not None:
        try:
            edge_kept_ids = filter_candidates_with_store(
                store=store,
                candidates=candidates,
                relation_entries=relation_entries,
                relation_kind=relation_kind,
                layer=layer,
            )
        finally:
            if owns_store:
                store.close()
    if edge_kept_ids is not None:
        return [
            symbol
            for symbol in candidates
            if _symbol_key(symbol.get("symbol_id")) in edge_kept_ids
        ]

    resolved_anchors: List[Tuple[str, str]] = []
    unresolved_anchors: List[str] = []
    for file_name, func_name in relation_entries:
        if file_name:
            # RelationCon provides a file name, not an absolute path. Resolve
            # it against the project only for the LSP API that requires one.
            abs_path = _can_resolve_to_unique_file(file_name, func_name)
            if abs_path:
                resolved_anchors.append((abs_path, func_name))
                continue
        unresolved_anchors.append(func_name)

    kept_ids: Set[str] = set()
    if relation_kind == "caller":
        if resolved_anchors:
            kept_ids |= _filter_by_caller_with_path(
                candidates,
                resolved_anchors,
                layer,
            )
        if unresolved_anchors:
            kept_ids |= _filter_by_caller_without_path(
                candidates,
                unresolved_anchors,
                layer,
                worker_count=worker_count,
            )
    elif relation_kind == "callee":
        if resolved_anchors:
            kept_ids |= _filter_by_callee_with_path(
                candidates,
                resolved_anchors,
                layer,
            )
        if unresolved_anchors:
            kept_ids |= _filter_by_callee_without_path(
                candidates,
                unresolved_anchors,
                layer,
                worker_count=worker_count,
            )
    else:
        raise ValueError(f"Unsupported relation kind: {relation_kind}")

    if not kept_ids:
        return list(candidates) if preserve_on_empty_fallback else []
    return [symbol for symbol in candidates if symbol.get("symbol_id") in kept_ids]


def filter_candidates_by_caller_entries(
    candidates: List[Dict[str, Any]],
    caller_entries: List[Tuple[Optional[str], str]],
    candidate_path: str,
    layer: Optional[int] = 1,
    worker_count: int = DEFAULT_WORKER_COUNT,
    graph_store: Optional[RelationGraphStore] = None,
    preserve_on_empty_fallback: bool = True,
) -> List[Dict[str, Any]]:
    return _filter_candidates_by_call_entries(
        candidates=candidates,
        relation_entries=caller_entries,
        relation_kind="caller",
        candidate_path=candidate_path,
        layer=layer,
        worker_count=worker_count,
        graph_store=graph_store,
        preserve_on_empty_fallback=preserve_on_empty_fallback,
    )


def filter_candidates_by_callee_entries(
    candidates: List[Dict[str, Any]],
    callee_entries: List[Tuple[Optional[str], str]],
    candidate_path: str,
    layer: Optional[int] = 1,
    worker_count: int = DEFAULT_WORKER_COUNT,
    graph_store: Optional[RelationGraphStore] = None,
    preserve_on_empty_fallback: bool = True,
) -> List[Dict[str, Any]]:
    return _filter_candidates_by_call_entries(
        candidates=candidates,
        relation_entries=callee_entries,
        relation_kind="callee",
        candidate_path=candidate_path,
        layer=layer,
        worker_count=worker_count,
        graph_store=graph_store,
        preserve_on_empty_fallback=preserve_on_empty_fallback,
    )


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
    roles = _extract_roles_from_semql(semQL, property_name)
    filtered = filter_candidates_by_roles(
        candidates,
        roles,
        candidate_path,
        preserve_non_applicable=property_name == "include",
    )
    return _to_symbols_index_schema(filtered, candidate_path)


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
    candidates = load_res(candidate_path)
    filtered = filter_candidates_by_caller_entries(
        candidates=candidates,
        caller_entries=caller_entries,
        candidate_path=candidate_path,
        layer=layer,
        worker_count=worker_count,
        preserve_on_empty_fallback=property_name == "include",
    )
    return _to_symbols_index_schema(filtered, candidate_path)


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
    candidates = load_res(candidate_path)
    filtered = filter_candidates_by_callee_entries(
        candidates=candidates,
        callee_entries=callee_entries,
        candidate_path=candidate_path,
        layer=layer,
        worker_count=worker_count,
        preserve_on_empty_fallback=property_name == "include",
    )
    return _to_symbols_index_schema(filtered, candidate_path)


if __name__ == "__main__":
    import json

    print(json.dumps(role_filter(
        semQL_path=f"{QUERY_OUTPUT_DIR}/semQL.json",
        candidate_path=f"{QUERY_OUTPUT_DIR}/filtered_by_type.json",
        property_name="include",
        # layer=1,
        # worker_count=4,
    ),
    indent=4 ))
