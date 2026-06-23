"""
Relation Executor — apply relation filters to surface-search results.

This executor combines caller/callee relation filters with include/exclude
SemQL properties:
- include relation results are unioned into the current result set
- exclude relation results are removed from the current result set
"""

from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from definition import OUTPUT_DIR, PROJECT_NAME
from filters.relation_filter import (
    _extract_callees_from_semql,
    _extract_callers_from_semql,
    callee_filter,
    caller_filter,
)
from utils.file_utils import load_res, save_res


DEFAULT_RELATION_RESULT_PATH = f"{OUTPUT_DIR}/{PROJECT_NAME}/filtered_by_relation.json"


def _symbol_key(symbol: Dict[str, Any]) -> str:
    symbol_id = symbol.get("symbol_id")
    if symbol_id is not None:
        return str(symbol_id)
    return "|".join([
        str(symbol.get("file", "")),
        str(symbol.get("name", "")),
        str(symbol.get("range", "")),
    ])


def _merge_symbols(
    old_results: List[Dict[str, Any]],
    include_results: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    merged = list(old_results)
    seen = {_symbol_key(symbol) for symbol in merged}
    for symbol in include_results:
        key = _symbol_key(symbol)
        if key in seen:
            continue
        seen.add(key)
        merged.append(symbol)
    return merged


def _remove_symbols(
    old_results: List[Dict[str, Any]],
    exclude_results: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    exclude_keys = {_symbol_key(symbol) for symbol in exclude_results}
    return [
        symbol for symbol in old_results
        if _symbol_key(symbol) not in exclude_keys
    ]


def _has_caller_condition(semQL: Dict[str, Any], property_name: str) -> bool:
    return bool(_extract_callers_from_semql(semQL, property_name))


def _has_callee_condition(semQL: Dict[str, Any], property_name: str) -> bool:
    return bool(_extract_callees_from_semql(semQL, property_name))


def relation_execute(
    semQL_path: str,
    surface_search_result_path: str,
    output_path: Optional[str] = DEFAULT_RELATION_RESULT_PATH,
    layer: Optional[int] = 1,
    worker_count: int = 4,
) -> List[Dict[str, Any]]:
    """
    Apply caller/callee relation conditions to surface-search results.

    Args:
        semQL_path: path to SemQL JSON.
        surface_search_result_path: path to old surface-search result JSON.
        output_path: where to save updated results. Use None to skip saving.
        layer: fallback call-chain depth when caller/callee fields do not carry
            hop_count in `file_name:func_name:hop_count` format.
        worker_count: concurrent workers used by relation filters when a
            caller/callee cannot be uniquely resolved to a file.

    Returns:
        Updated symbol list after include union and exclude subtraction.
    """
    semQL = load_res(semQL_path)
    current_results = load_res(surface_search_result_path)

    include_results: List[Dict[str, Any]] = []
    exclude_results: List[Dict[str, Any]] = []

    if _has_caller_condition(semQL, "include"):
        include_results = _merge_symbols(
            include_results,
            caller_filter(
                semQL_path=semQL_path,
                candidate_path=surface_search_result_path,
                property_name="include",
                layer=layer,
                worker_count=worker_count,
            ),
        )

    if _has_callee_condition(semQL, "include"):
        include_results = _merge_symbols(
            include_results,
            callee_filter(
                semQL_path=semQL_path,
                candidate_path=surface_search_result_path,
                property_name="include",
                layer=layer,
                worker_count=worker_count,
            ),
        )

    if include_results:
        current_results = _merge_symbols(current_results, include_results)

    if _has_caller_condition(semQL, "exclude"):
        exclude_results = _merge_symbols(
            exclude_results,
            caller_filter(
                semQL_path=semQL_path,
                candidate_path=surface_search_result_path,
                property_name="exclude",
                layer=layer,
                worker_count=worker_count,
            ),
        )

    if _has_callee_condition(semQL, "exclude"):
        exclude_results = _merge_symbols(
            exclude_results,
            callee_filter(
                semQL_path=semQL_path,
                candidate_path=surface_search_result_path,
                property_name="exclude",
                layer=layer,
                worker_count=worker_count,
            ),
        )

    if exclude_results:
        current_results = _remove_symbols(current_results, exclude_results)

    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        save_res(output_path, current_results)

    return current_results


def run_relation_executor(
    semQL_path: str,
    surface_search_result_path: str,
    output_path: Optional[str] = DEFAULT_RELATION_RESULT_PATH,
    layer: Optional[int] = 1,
    worker_count: int = 4,
) -> List[Dict[str, Any]]:
    """Convenience wrapper around relation_execute."""
    return relation_execute(
        semQL_path=semQL_path,
        surface_search_result_path=surface_search_result_path,
        output_path=output_path,
        layer=layer,
        worker_count=worker_count,
    )


if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Run relation executor.")
    parser.add_argument("semQL_path")
    parser.add_argument("surface_search_result_path")
    parser.add_argument("--output", dest="output_path", default=DEFAULT_RELATION_RESULT_PATH)
    parser.add_argument("--layer", dest="layer", type=int, default=1)
    parser.add_argument("--worker-count", dest="worker_count", type=int, default=4)
    args = parser.parse_args()

    print(json.dumps(
        relation_execute(
            semQL_path=args.semQL_path,
            surface_search_result_path=args.surface_search_result_path,
            output_path=args.output_path,
            layer=args.layer,
            worker_count=args.worker_count,
        ),
        ensure_ascii=False,
        indent=2,
    ))
