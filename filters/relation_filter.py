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
import sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Any, List, Optional, Set, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from definition import PROJECT_PATH, JDTLS_PATH
from parsers.java_lsp_client import JavaLSPClient, JavaCallChainExtractor
from parsers.parallel_java_lsp_client import ParallelJavaLSPClient, ParallelJavaCallChainExtractor
from parsers.registry import parse_file_with_registry
from query_processing.semql_utils import extract_semql_text_terms
from utils.file_utils import load_res

# 并发查询时的 worker 数量
DEFAULT_WORKER_COUNT = 4


def _parse_caller_field(raw: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Parse a caller/callee field value into (file_name, func_name).

    Format "file_name:func_name"  → (file_name, func_name)
    Format "func_name"            → (None, func_name)
    Format "None" / ""            → (None, None)
    """
    raw = str(raw).strip()
    if not raw or raw.lower() == "none":
        return None, None
    if ":" in raw:
        file_name, func_name = raw.rsplit(":", 1)
        return file_name.strip(), func_name.strip()
    return None, raw


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
        fn, func = _parse_caller_field(raw)
        if func:
            result.append((fn, func))
    return result


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
    return False


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
    callers_with_path: List[Tuple[str, str]],
    layer: Optional[int],
) -> Set[str]:
    """
    For each (abs_path, func_name), call get_callees once,
    then collect candidate symbol_ids whose name appears in the callee set.
    """
    kept: Set[str] = set()
    lsp_client = JavaLSPClient(project_root=PROJECT_PATH, jdtls_path=JDTLS_PATH)
    lsp_client.start()
    try:
        extractor = JavaCallChainExtractor(lsp_client)
        for abs_path, func_name in callers_with_path:
            callees = extractor.get_callees(abs_path, func_name, layer=layer)
            callee_names: Set[str] = {
                str(c.get("name", "")).strip()
                for c in callees
                if c.get("name")
            }
            for sym in candidates:
                sid = sym.get("symbol_id")
                if not sid:
                    continue
                if str(sym.get("name", "")).strip() in callee_names:
                    kept.add(sid)
    finally:
        lsp_client.stop()
    return kept


# ---------------------------------------------------------------------------
# Case 2: caller cannot be uniquely resolved — concurrent LSP queries
# ---------------------------------------------------------------------------

def _filter_by_caller_without_path(
    candidates: List[Dict[str, Any]],
    caller_names: List[str],
    layer: Optional[int],
    worker_count: int = DEFAULT_WORKER_COUNT,
) -> Set[str]:
    """
    For each candidate symbol, check whether its callers contain the target name.
    Uses a thread pool of ParallelJavaLSPClient instances for concurrency.
    """
    kept: Set[str] = set()
    target_set = set(caller_names)

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
                str(c.get("name", "")).strip()
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
# Public API
# ---------------------------------------------------------------------------

def caller_filter(
    semQL_path: str,
    candidate_path: str,
    property_name: str = "include",
    layer: Optional[int] = None,
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
        return load_res(candidate_path)

    candidates = load_res(candidate_path)
    if not candidates:
        return []

    # Separate entries that can be uniquely resolved from those that cannot
    callers_with_path: List[Tuple[str, str]] = []
    callers_without_path: List[str] = []

    for file_name, func_name in caller_entries:
        if file_name:
            abs_path = _can_resolve_to_unique_file(file_name, func_name)
            if abs_path:
                callers_with_path.append((abs_path, func_name))
                continue
        callers_without_path.append(func_name)

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
        return [sym for sym in candidates if sym.get("symbol_id") in kept_ids]
    return candidates


import json 
print(json.dumps(caller_filter(
    semQL_path="/Users/bytedance/old6ma/CodeSearch/output/youlai-boot-master/semQL_test.json",
    candidate_path="/Users/bytedance/old6ma/CodeSearch/output/youlai-boot-master/filtered_by_type.json",
    property_name="include",
    layer=1,
    worker_count=4,
)))