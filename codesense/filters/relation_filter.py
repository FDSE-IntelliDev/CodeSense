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
from typing import Dict, Any, Iterable, List, Optional, Set, Tuple

from codesense.config import load_config
from codesense.parsers.java_lsp_client import JavaLSPClient, JavaCallChainExtractor
from codesense.parsers.parallel_java_lsp_client import ParallelJavaLSPClient, ParallelJavaCallChainExtractor
from codesense.parsers.registry import parse_file_with_registry
from codesense.query.semql_utils import extract_semql_text_terms
from codesense.utils.file_utils import load_res
from codesense.filters.relation_graph_store import (
    RelationGraphStore,
    filter_candidates_with_store,
)

# 并发查询时的 worker 数量
DEFAULT_WORKER_COUNT = 4


# 下面两个以前是模块级常量：
#     from codesense.config import PROJECT_PATH, JDTLS_PATH
# 那种写法会在 import 时就触发一次配置读盘——PEP 562 的模块级 __getattr__
# 会被 `from ... import 常量` 唤醒。后果是违反「模块顶层不执行逻辑」，
# 而且没有 configs/default.yaml 连 import 都会失败。改成用到时才解析。


def _project_path() -> str:
    return str(load_config().project_path)


def _jdtls_path() -> str:
    return load_config().tools.jdtls_path


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
        symbols_path = load_config().project_output_dir / "symbols_index.json"
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
    return os.path.join(_project_path(), file_path)


def _find_file_in_project(file_name: str) -> List[str]:
    """
    Find all files matching file_name under the configured project path.
    Returns a list of absolute paths.
    """
    matches: List[str] = []
    for root, _dirs, files in os.walk(_project_path()):
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

    file_rel = os.path.relpath(file_path, _project_path())
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

class LspCallResolver:
    """用 LSP 解析 caller/callee 关系——图库解析不出来时的兜底。

    ``layer``、``worker_count`` 以及 LSP 的两个路径原来是**四个模块级函数
    逐个透传的参数**（规则 1 的 ❌ 写法）。它们是这个解析器自己的状态，
    构造时定一次，方法签名保持干净。

    注意：这条路径会 fork jdtls 子进程，需要目标项目在本地存在。
    """

    def __init__(
        self,
        layer: Optional[int] = 1,
        worker_count: int = DEFAULT_WORKER_COUNT,
        project_path: Optional[str] = None,
        jdtls_path: Optional[str] = None,
    ) -> None:
        self.layer = layer
        self.worker_count = worker_count
        self._project_path = project_path
        self._jdtls_path = jdtls_path

    @property
    def project_path(self) -> str:
        return self._project_path or str(load_config().project_path)

    @property
    def jdtls_path(self) -> str:
        return self._jdtls_path or load_config().tools.jdtls_path

    def _filter_by_caller_with_path(
            self,
        candidates: List[Dict[str, Any]],
        resolved_callers: List[Tuple[str, str]],
    ) -> Set[str]:
        """
        For each (abs_path, func_name), call get_callees once,
        then collect candidate symbol_ids whose name appears in the callee set.
        """
        kept: Set[str] = set()
        lsp_client = JavaLSPClient(project_root=self.project_path, jdtls_path=self.jdtls_path, verbose=False)
        lsp_client.start()
        try:
            extractor = JavaCallChainExtractor(lsp_client)
            for abs_path, func_name in resolved_callers:
                callees = extractor.get_callees(abs_path, func_name, layer=self.layer)
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

    def _filter_by_caller_without_path(
            self,
        candidates: List[Dict[str, Any]],
        unresolved_callers: List[str],
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
            client = ParallelJavaLSPClient(project_root=self.project_path, jdtls_path=self.jdtls_path)
            client.start()
            try:
                extractor = ParallelJavaCallChainExtractor(client)
                callers = extractor.get_callers(abs_path, sym_name, layer=self.layer)
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

        with ThreadPoolExecutor(max_workers=self.worker_count) as executor:
            futures = {executor.submit(_query_one, sym): sym for sym in candidates}
            for future in as_completed(futures):
                sid = future.result()
                if sid:
                    kept.add(sid)

        return kept

    def _filter_by_callee_with_path(
            self,
        candidates: List[Dict[str, Any]],
        resolved_callees: List[Tuple[str, str]],
    ) -> Set[str]:
        """
        For each (abs_path, func_name), call get_callers once,
        then collect candidate symbol_ids whose name appears in the caller set.
        """
        kept: Set[str] = set()
        lsp_client = JavaLSPClient(project_root=self.project_path, jdtls_path=self.jdtls_path, verbose=False)
        lsp_client.start()
        try:
            extractor = JavaCallChainExtractor(lsp_client)
            for abs_path, func_name in resolved_callees:
                callers = extractor.get_callers(abs_path, func_name, layer=self.layer)
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

    def _filter_by_callee_without_path(
            self,
        candidates: List[Dict[str, Any]],
        unresolved_callees: List[str],
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
            client = ParallelJavaLSPClient(project_root=self.project_path, jdtls_path=self.jdtls_path)
            client.start()
            try:
                extractor = ParallelJavaCallChainExtractor(client)
                callees = extractor.get_callees(abs_path, sym_name, layer=self.layer)
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

        with ThreadPoolExecutor(max_workers=self.worker_count) as executor:
            futures = {executor.submit(_query_one, sym): sym for sym in candidates}
            for future in as_completed(futures):
                sid = future.result()
                if sid:
                    kept.add(sid)

        return kept


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

VALID_GRAPH_ROLES = frozenset({"entry_point", "leaf", "isolate"})


def normalize_roles(roles: Iterable[Any]) -> Set[str]:
    """把 planner 给的 role 列表规范化，丢掉不认识的。"""
    return {
        str(role).strip().lower()
        for role in roles or ()
        if str(role).strip().lower() in VALID_GRAPH_ROLES
    }


def apply_graph_roles(
    candidates: List[Dict[str, Any]],
    normalized_roles: Set[str],
    graph_store: Optional[RelationGraphStore],
    preserve_non_applicable: bool = True,
) -> List[Dict[str, Any]]:
    """按调用图角色收窄候选集。**纯计算**：store 由调用方给，不自己去开。

    Non-callable symbols do not have a call-graph role. They remain valid while
    evaluating an include constraint, but must not be reported as matches for
    an exclude constraint.
    """
    if not candidates or not normalized_roles:
        return list(candidates)
    if graph_store is None:
        return list(candidates) if preserve_non_applicable else []

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

        in_degree, out_degree = graph_store.symbol_degrees(
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




def apply_call_entries(
    candidates: List[Dict[str, Any]],
    relation_entries: List[Tuple[Optional[str], str]],
    relation_kind: str,
    graph_store: Optional[RelationGraphStore],
    layer: Optional[int] = 1,
    worker_count: int = DEFAULT_WORKER_COUNT,
    preserve_on_empty_fallback: bool = True,
) -> List[Dict[str, Any]]:
    """按 caller/callee 约束收窄候选集。store 由调用方给，本函数不开也不关。

    优先查代码图库；图库解析不出来时退回 LSP（那条路径会起 jdtls 子进程，
    需要目标项目在本地存在）。
    """
    if not candidates or not relation_entries:
        return list(candidates)

    edge_kept_ids = None
    if graph_store is not None:
        edge_kept_ids = filter_candidates_with_store(
            store=graph_store,
            candidates=candidates,
            relation_entries=relation_entries,
            relation_kind=relation_kind,
            layer=layer,
        )
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

    if relation_kind not in ("caller", "callee"):
        raise ValueError(f"Unsupported relation kind: {relation_kind}")

    # layer / worker_count 是解析器自己的状态，构造时定一次；
    # 下面四个调用点的签名因此都干净了。
    resolver = LspCallResolver(layer=layer, worker_count=worker_count)
    with_path = getattr(resolver, f"_filter_by_{relation_kind}_with_path")
    without_path = getattr(resolver, f"_filter_by_{relation_kind}_without_path")

    kept_ids: Set[str] = set()
    if resolved_anchors:
        kept_ids |= with_path(candidates, resolved_anchors)
    if unresolved_anchors:
        kept_ids |= without_path(candidates, unresolved_anchors)

    if not kept_ids:
        return list(candidates) if preserve_on_empty_fallback else []
    return [symbol for symbol in candidates if symbol.get("symbol_id") in kept_ids]


# 这里原来还有一层过渡 shim：filter_candidates_by_roles / *_caller_entries /
# *_callee_entries、role_filter / caller_filter / callee_filter，以及一个
# __main__ demo。它们是搬包时为兼容旧调用点保留的，逐个透传
# layer / worker_count / graph_store（规则 1 的 ❌ 写法）。
# 生产链路已全部改走 relation_filters.py 的 GraphRoleFilter / CallerFilter /
# CalleeFilter，命令行入口在 scripts/run_relation.py，故一并删除。
