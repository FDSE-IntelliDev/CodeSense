import json
from typing import Any, Dict, Iterable, Optional

from codesense.search.full_term_matcher import FullTermMatcher
from codesense.config import PROJECT_OUTPUT_DIR, QUERY_OUTPUT_DIR
def _has_requested_conditions(semql: dict, properties: tuple) -> bool:
    """Return whether grouped SemQL contains any requested property entries."""
    conditions = semql.get("conditions")
    if not isinstance(conditions, dict):
        return True

    for condition_group in conditions.values():
        if not isinstance(condition_group, dict):
            continue
        for property_name in properties:
            property_conditions = condition_group.get(property_name)
            if isinstance(property_conditions, (list, dict)) and property_conditions:
                return True
    return False


def _map_subtokens_to_symbols(
    ngramed_symbols: Dict[str, Any],
    matched_subtokens: Any,
) -> Dict[str, Any]:
    """Map matched index subtokens to copied symbol records and match evidence."""
    matched_names = set()
    if isinstance(matched_subtokens, dict):
        for names in matched_subtokens.values():
            matched_names.update(names)
    elif isinstance(matched_subtokens, list):
        matched_names.update(matched_subtokens)

    records_by_id: Dict[str, Dict[str, Any]] = {}
    matches_by_id: Dict[str, list] = {}
    order: list = []
    for ngramed_token, symbol in ngramed_symbols.items():
        normalized_token = ngramed_token.lower().strip()
        if normalized_token not in matched_names:
            continue

        symbol_list = symbol if isinstance(symbol, list) else [symbol]
        for item in symbol_list:
            if not isinstance(item, dict) or item.get("symbol_id") is None:
                continue
            item_id = str(item["symbol_id"])
            if item_id not in records_by_id:
                record = dict(item)
                # Preserve the legacy transient field for existing consumers.
                record["matched_subtokens"] = ngramed_token
                records_by_id[item_id] = record
                matches_by_id[item_id] = []
                order.append(item_id)
            if ngramed_token not in matches_by_id[item_id]:
                matches_by_id[item_id].append(ngramed_token)

    return {
        "symbols": [records_by_id[item_id] for item_id in order],
        "matched_subtokens_by_symbol_id": matches_by_id,
    }


def search_symbols_by_terms(
    invert_index_path: str,
    ngramed_symbol_path: str,
    terms: Iterable[str],
    matcher: Optional[FullTermMatcher] = None,
    ngramed_symbols: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Planner-facing term-list search that keeps keyword-to-subtoken evidence."""
    term_matcher = matcher or FullTermMatcher(
        invert_index_path=invert_index_path,
        ngramed_symbol_path=ngramed_symbol_path,
    )
    match_result = term_matcher.match_terms(terms)
    if ngramed_symbols is None:
        with open(ngramed_symbol_path, "r", encoding="utf-8") as f:
            ngramed_symbols = json.load(f)

    mapped = _map_subtokens_to_symbols(
        ngramed_symbols if isinstance(ngramed_symbols, dict) else {},
        match_result.get("matched_subtokens", []),
    )
    return {
        **mapped,
        "detail": match_result.get("detail", []),
    }




# 原来这里有一个 __main__ 块，装着完整的工作流（读文件、拼对象、
# 跑一遍、写产物）。那是胶水，已搬到 scripts/run_invert_index_search.py。
# 核心模块只留功能逻辑。
