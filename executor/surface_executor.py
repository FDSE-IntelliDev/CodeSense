"""
Surface Executor — Stage 1 of the SemQL execution pipeline.

Responsible for fast, high-recall candidate generation using:
- inverted index / ngram / abbreviation search
- code element type filtering

This is the first and fastest execution stage. It consumes the semQL conditions
and produces an initial candidate set that subsequent stages can further filter.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from definition import OUTPUT_DIR, PROJECT_NAME
from filters.type_filter import filter_symbols_by_type
from search.invert_index_search import invert_index_search4symbol
from utils.file_utils import save_res


class SurfaceExecutor:
    """Stage 1 executor: inverted index search + type-based filtering."""

    def __init__(self, output_dir: str = f"{OUTPUT_DIR}/{PROJECT_NAME}"):
        self.output_dir = output_dir
        self.invert_index_path = f"{output_dir}/invert_index.json"
        self.ngramed_symbol_path = f"{output_dir}/ngramed_symbol.json"
        self.semQL_path = f"{output_dir}/semQL.json"
        self.search_result_path = f"{output_dir}/invert_index_search_result.json"
        self.filtered_result_path = f"{output_dir}/filtered_by_type.json"
        self.exclude_result_path = f"{output_dir}/exclude_by_type.json"

    def execute(self, property: str = "include") -> List[Dict[str, Any]]:
        """Run the full Stage 1 pipeline and return filtered candidates.

        property: "include" to search include conditions,
                  "exclude" to search exclude conditions.
        """
        properties = (property,) if property in ("include", "exclude") else ("include",)

        print(f"=== [Surface Executor] Stage 1: Candidate Generation (property={property}) ===")

        # Step 1 — inverted index / ngram search
        print("[Step 1] Running inverted index / ngram search ...")
        search_results = invert_index_search4symbol(
            invert_index_path=self.invert_index_path,
            ngramed_symbol_path=self.ngramed_symbol_path,
            query_dsl_result_path=self.semQL_path,
            properties=properties,
        )
        if property == "include":
            save_res(self.search_result_path, search_results)
        print(f"  -> {len(search_results)} matched elements (before type filter)")

        # Step 2 — type filtering from semQL surface & relation conditions
        print("[Step 2] Running code element type filter ...")
        if len(search_results)>0:
            filtered = filter_symbols_by_type(
                search_result_path=self.search_result_path,
                semQL_path=self.semQL_path,
                exclude_output_path=self.exclude_result_path,
            )
            print(f"  -> {len(filtered)} elements after type filter")
            print("=== [Surface Executor] Stage 1 complete ===\n")

            return filtered
        else:
            return []
#
# def run_surface_search_test(
#     output_dir: str = f"{OUTPUT_DIR}/{PROJECT_NAME}",
#     property: str = "include",
# ) -> List[Dict[str, Any]]:
#     """Convenience entry point."""
#     return SurfaceExecutor(output_dir=output_dir).execute(property)


def run_surface_search(
    output_dir: str = f"{OUTPUT_DIR}/{PROJECT_NAME}",
) -> List[Dict[str, Any]]:
    """
    Full surface search: include candidates minus exclude candidates.

    Steps:
    1. Run surface executor with property="include" to get include candidates.
    2. Run surface executor with property="exclude" to get exclude candidates.
    3. Remove exclude candidates from include candidates by symbol_id.
    4. Save the final result to <output_dir>/filtered_by_type.json.
    5. Return the final filtered result set.
    """
    executor = SurfaceExecutor(output_dir=output_dir)

    include_results = executor.execute(property="include")
    exclude_results = executor.execute(property="exclude")

    exclude_ids = {
        sym.get("symbol_id") for sym in exclude_results if sym.get("symbol_id")
    }

    final_results = [
        sym for sym in include_results if sym.get("symbol_id") not in exclude_ids
    ]

    print(f"=== [Surface Search] Final: {len(include_results)} include - "
          f"{len(exclude_results)} exclude = {len(final_results)} results ===")

    save_res(executor.filtered_result_path, final_results)

    return final_results


def main() -> None:

    results = run_surface_search(output_dir=f"{OUTPUT_DIR}/{PROJECT_NAME}")
    output_path = Path(f"{OUTPUT_DIR}/{PROJECT_NAME}") / "filtered_by_type.json"
    print(
        json.dumps(
            {
                "result_count": len(results),
                "output_path": str(output_path.resolve()),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
