"""
Intention executor for the first semantic filtering stage.

The executor consumes SemQL and relation-filtered candidates, runs the existing
cluster pipeline, and writes the kept symbols for later filtering stages.
"""

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

from filters.cluster_pipeline import CodeEmbedder, FiltrationDispatcher, SymbolClusterer
from filters.embedding_filter import run_embedding_filter
from filters.llm_judge_filter import llm_judge_filter as run_llm_judge_file_filter
from definition import QUERY_OUTPUT_DIR
from utils.file_utils import load_res, save_res


def cluster_filter(
    semQL_path: str,
    relation_executor_result_path: str,
    output_path: str,
) -> List[Dict[str, Any]]:
    """
    Run intention-based cluster filtering.

    Args:
        semQL_path: path to the SemQL JSON file.
        relation_executor_result_path: path to relation executor candidate symbols.
        output_path: path used to save symbols kept by cluster filtering.

    Returns:
        Symbols kept by the cluster pipeline.
    """
    semql = load_res(semQL_path)
    relation_results = load_res(relation_executor_result_path)

    if not isinstance(semql, dict):
        raise ValueError(f"SemQL must be a JSON object: {semQL_path}")
    if not isinstance(relation_results, list):
        raise ValueError(
            "Relation executor result must be a JSON array: "
            f"{relation_executor_result_path}"
        )

    if not relation_results:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        save_res(output_path, [])
        return []

    dispatcher = FiltrationDispatcher(
        embedder=CodeEmbedder(),
        clusterer=SymbolClusterer(distance_threshold=0.3),
    )
    cluster_result = dispatcher.run_pipeline(relation_results, semql)
    kept_symbols = cluster_result.get("kept", [])

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    save_res(output_path, kept_symbols)
    return kept_symbols


def embedding_filter(
    semQL_path: str,
    cluster_filter_result_path: str,
    output_path: str,
) -> List[Dict[str, Any]]:
    """
    Run fine-grained embedding filtering after cluster filtering.

    Args:
        semQL_path: path to the SemQL JSON file.
        cluster_filter_result_path: path to symbols kept by cluster filtering.
        output_path: path used to save symbols kept by embedding filtering.

    Returns:
        Symbols kept by the embedding filter.
    """
    semql = load_res(semQL_path)
    cluster_results = load_res(cluster_filter_result_path)

    if not isinstance(semql, dict):
        raise ValueError(f"SemQL must be a JSON object: {semQL_path}")
    if not isinstance(cluster_results, list):
        raise ValueError(
            "Cluster filter result must be a JSON array: "
            f"{cluster_filter_result_path}"
        )

    if not cluster_results:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        save_res(output_path, [])
        return []

    embedding_result = run_embedding_filter(cluster_results, semql)
    kept_symbols = embedding_result.get("kept", [])

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    save_res(output_path, kept_symbols)
    return kept_symbols


def llm_judge_filter(
    semQL_path: str,
    embedding_filter_result_path: str,
    output_path: str,
) -> List[Dict[str, Any]]:
    """
    Run LLM-as-a-Judge filtering after embedding filtering.

    Args:
        semQL_path: path to the SemQL JSON file.
        embedding_filter_result_path: path to symbols kept by embedding filtering.
        output_path: path used to save symbols kept by LLM judge.

    Returns:
        Symbols kept by the LLM judge filter.
    """
    judge_output_path = str(Path(output_path).with_name("LLM_judge_result_debug.json"))
    return run_llm_judge_file_filter(
        semQL_path=semQL_path,
        candidate_path=embedding_filter_result_path,
        output_path=output_path,
        judge_output_path=judge_output_path,
    )


def executor(
    semQL_path: str,
    relation_executor_result_path: str,
    output_path: str,
) -> List[Dict[str, Any]]:
    """
    Run intention filters in order: cluster filter, embedding filter, then LLM judge.
    """
    output = Path(output_path)
    cluster_output_path = str(output.with_name("filtered_by_cluster.json"))
    embedding_output_path = str(output.with_name("filtered_by_embedding.json"))

    cluster_filter(
        semQL_path=semQL_path,
        relation_executor_result_path=relation_executor_result_path,
        output_path=cluster_output_path,
    )
    embedding_filter(
        semQL_path=semQL_path,
        cluster_filter_result_path=cluster_output_path,
        output_path=embedding_output_path,
    )
    result = llm_judge_filter(
        semQL_path=semQL_path,
        embedding_filter_result_path=embedding_output_path,
        output_path=output_path,
    )
    return result


def main() -> None:
    output_path = f"{QUERY_OUTPUT_DIR}/intention_executor_result.json"

    results = executor(
        semQL_path=f"{QUERY_OUTPUT_DIR}/semQL.json",
        relation_executor_result_path=f"{QUERY_OUTPUT_DIR}/filtered_by_relation.json",
        output_path=output_path,
    )
    print(
        json.dumps(
            {
                "kept": len(results),
                "output_path": str(Path(output_path).resolve()),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
