"""Execute the physical intention plan produced by IntentionPlanner.

The planner owns every stage policy. This executor only evaluates the planned
runtime predicates and executes Cluster -> Embedding -> gray-zone LLM Judge.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from time import perf_counter
from typing import Any, Callable, Dict, List, Optional

from definition import BASE_MODEL, QUERY_OUTPUT_DIR
from filters.cluster_pipeline import (
    CodeEmbedder,
    FiltrationDispatcher,
    SymbolClusterer,
)
from filters.embedding_filter import run_embedding_filter
from filters.llm_judge_filter import LLMJudgeConfig, LLMJudgeFilter
from utils.file_utils import load_res, save_res


ClusterFactory = Callable[[Dict[str, Any]], FiltrationDispatcher]
EmbeddingRunner = Callable[
    [List[Dict[str, Any]], Dict[str, Any], Dict[str, Any]],
    Dict[str, Any],
]
JudgeFactory = Callable[[Dict[str, Any]], LLMJudgeFilter]


class IntentionExecutor:
    """Interpret one intention-only physical plan against relation candidates."""

    def __init__(
        self,
        *,
        cluster_factory: Optional[ClusterFactory] = None,
        embedding_runner: Optional[EmbeddingRunner] = None,
        judge_factory: Optional[JudgeFactory] = None,
    ) -> None:
        self._cluster_factory = cluster_factory or self._build_cluster_dispatcher
        self._embedding_runner = embedding_runner or run_embedding_filter
        self._judge_factory = judge_factory or self._build_judge
        self.cluster_result: Dict[str, Any] = {}
        self.embedding_result: Dict[str, Any] = {}
        self.judge_result: Dict[str, Any] = {}
        self.execution_report: Dict[str, Any] = {}

    def execute(
        self,
        intention_plan: Dict[str, Any],
        candidates: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        self._validate_inputs(intention_plan, candidates)
        # Filters annotate only top-level fields. A shallow copy prevents those
        # annotations from mutating the persisted Relation Executor result.
        working_candidates = [dict(candidate) for candidate in candidates]
        execution_plan = intention_plan.get("execution_plan", {})
        query_profile = intention_plan.get("query_profile", {})

        started = perf_counter()
        self.cluster_result, cluster_report = self._execute_cluster(
            working_candidates,
            query_profile,
            execution_plan.get("cluster", {}),
        )
        self.embedding_result, embedding_report = self._execute_embedding(
            self.cluster_result.get(
                "forwarded",
                self.cluster_result.get("kept", []),
            ),
            query_profile,
            execution_plan.get("embedding", {}),
            execution_plan.get("llm_judge", {}),
        )
        final_results, self.judge_result, judge_report = self._execute_judge(
            intention_plan,
            self.embedding_result,
            execution_plan.get("llm_judge", {}),
        )

        self.execution_report = {
            "kind": "intention_execution_report",
            "input_candidate_count": len(candidates),
            "final_candidate_count": len(final_results),
            "stages": {
                "cluster": cluster_report,
                "embedding": embedding_report,
                "llm_judge": judge_report,
            },
            "total_duration_ms": round((perf_counter() - started) * 1000, 3),
        }
        return final_results

    def _execute_cluster(
        self,
        candidates: List[Dict[str, Any]],
        query_profile: Dict[str, Any],
        policy: Dict[str, Any],
    ) -> tuple[Dict[str, Any], Dict[str, Any]]:
        started = perf_counter()
        enabled = bool(policy.get("enabled", False))
        min_candidate_count = max(1, int(policy.get("min_candidate_count", 1)))
        query_text = str(query_profile.get("semantic_text") or "").strip()

        skip_reason = None
        if not candidates:
            skip_reason = "empty_candidates"
        elif not enabled:
            skip_reason = "disabled_by_plan"
        elif len(candidates) < min_candidate_count:
            skip_reason = "candidate_count_below_plan_minimum"
        elif not query_text:
            skip_reason = "empty_semantic_text"

        if skip_reason is not None:
            result = self._identity_cluster_result(candidates, skip_reason)
            return result, self._stage_report(
                executed=False,
                reason=skip_reason,
                input_count=len(candidates),
                output_count=len(candidates),
                started=started,
                policy=policy,
                stats=result["stats"],
            )

        dispatcher = self._cluster_factory(policy)
        result = dispatcher.run_pipeline(candidates, query_text, policy)
        return result, self._stage_report(
            executed=True,
            reason=result.get("stats", {}).get("guard_reason"),
            input_count=len(candidates),
            output_count=len(
                result.get("forwarded", result.get("kept", []))
            ),
            started=started,
            policy=policy,
            stats=result.get("stats", {}),
        )

    def _execute_embedding(
        self,
        candidates: List[Dict[str, Any]],
        query_profile: Dict[str, Any],
        policy: Dict[str, Any],
        judge_policy: Dict[str, Any],
    ) -> tuple[Dict[str, Any], Dict[str, Any]]:
        started = perf_counter()
        enabled = bool(policy.get("enabled", False))
        if not candidates:
            result = self._identity_embedding_result([], gray=False)
            reason = "empty_candidates"
            executed = False
        elif not enabled:
            gray_without_terms = (
                policy.get("no_term_policy") == "gray_for_llm"
                and bool(judge_policy.get("enabled", False))
            )
            result = self._identity_embedding_result(
                candidates,
                gray=gray_without_terms,
            )
            reason = (
                "disabled_by_plan_all_candidates_gray"
                if gray_without_terms
                else "disabled_by_plan"
            )
            executed = False
        else:
            result = self._embedding_runner(candidates, query_profile, policy)
            reason = None
            executed = True

        return result, self._stage_report(
            executed=executed,
            reason=reason,
            input_count=len(candidates),
            output_count=len(result.get("kept", [])),
            started=started,
            policy=policy,
            stats=result.get("stats", {}),
        )

    def _execute_judge(
        self,
        intention_plan: Dict[str, Any],
        embedding_result: Dict[str, Any],
        policy: Dict[str, Any],
    ) -> tuple[List[Dict[str, Any]], Dict[str, Any], Dict[str, Any]]:
        started = perf_counter()
        ranked_candidates = embedding_result.get("kept", [])
        auto_kept = embedding_result.get("auto_kept", [])
        gray = embedding_result.get("gray", [])
        enabled = bool(policy.get("enabled", False))

        if not gray:
            judge_result = self._empty_judge_result("empty_gray_zone")
            accepted_gray: List[Dict[str, Any]] = []
            executed = False
            reason = "empty_gray_zone"
        elif not enabled:
            keep_gray = policy.get("disabled_gray_policy", "keep") == "keep"
            accepted_gray = gray if keep_gray else []
            judge_result = self._empty_judge_result("disabled_by_plan")
            judge_result["accepted_without_judge"] = accepted_gray
            executed = False
            reason = "disabled_by_plan"
        else:
            executed = True
            reason = None
            try:
                judge_result = self._judge_factory(policy).run_filter(
                    gray,
                    intention_plan,
                )
                accepted_gray = list(judge_result.get("kept", []))
                if policy.get("failure_policy") == "keep_uncertain":
                    uncertain_ids = {
                        int(item["symbol_id"])
                        for item in judge_result.get("uncertain", [])
                        if _is_int_like(item.get("symbol_id"))
                    }
                    accepted_gray.extend(
                        candidate
                        for candidate in gray
                        if _candidate_symbol_id(candidate) in uncertain_ids
                    )
            except Exception as exc:  # LLM failure follows the explicit plan policy.
                reason = "llm_judge_failed"
                judge_result = self._empty_judge_result(reason)
                judge_result["error"] = f"{type(exc).__name__}: {exc}"
                accepted_gray = (
                    gray
                    if policy.get("failure_policy") == "keep_uncertain"
                    else []
                )

        accepted_keys = {
            _candidate_identity(candidate)
            for candidate in auto_kept + accepted_gray
        }
        final_results = [
            candidate
            for candidate in ranked_candidates
            if _candidate_identity(candidate) in accepted_keys
        ]
        judge_result["accepted_gray"] = accepted_gray

        report = self._stage_report(
            executed=executed,
            reason=reason,
            input_count=len(gray),
            output_count=len(accepted_gray),
            started=started,
            policy=policy,
            stats=judge_result.get("stats", {}),
        )
        report["auto_kept_count"] = len(auto_kept)
        report["gray_candidate_count"] = len(gray)
        return final_results, judge_result, report

    @staticmethod
    def _build_cluster_dispatcher(policy: Dict[str, Any]) -> FiltrationDispatcher:
        return FiltrationDispatcher(
            embedder=CodeEmbedder(),
            clusterer=SymbolClusterer(
                distance_threshold=float(policy.get("distance_threshold", 0.3))
            ),
        )

    @staticmethod
    def _build_judge(policy: Dict[str, Any]) -> LLMJudgeFilter:
        return LLMJudgeFilter(
            config=LLMJudgeConfig(
                model=str(policy.get("model") or BASE_MODEL),
                batch_size=max(1, int(policy.get("batch_size", 5))),
                max_code_chars=max(0, int(policy.get("max_code_chars", 3000))),
            )
        )

    @staticmethod
    def _identity_cluster_result(
        candidates: List[Dict[str, Any]],
        reason: str,
    ) -> Dict[str, Any]:
        return {
            "kept": candidates,
            "rescue_candidates": [],
            "forwarded": candidates,
            "discarded": [],
            "tiers": {},
            "stats": {
                "total_initial": len(candidates),
                "total_kept": len(candidates),
                "total_rescue": 0,
                "total_forwarded": len(candidates),
                "total_discarded": 0,
                "filter_applied": False,
                "guard_reason": reason,
            },
        }

    @staticmethod
    def _identity_embedding_result(
        candidates: List[Dict[str, Any]],
        *,
        gray: bool,
    ) -> Dict[str, Any]:
        for candidate in candidates:
            candidate["_embedding_decision"] = "gray" if gray else "auto_keep"
            candidate["_embedding_decision_reasons"] = [
                "embedding_stage_disabled_by_plan"
            ]
        auto_kept = [] if gray else candidates
        gray_candidates = candidates if gray else []
        return {
            "kept": candidates,
            "auto_kept": auto_kept,
            "gray": gray_candidates,
            "discarded": [],
            "tiers": {},
            "stats": {
                "total_initial": len(candidates),
                "total_kept": len(candidates),
                "total_auto_kept": len(auto_kept),
                "total_gray": len(gray_candidates),
                "total_discarded": 0,
            },
        }

    @staticmethod
    def _empty_judge_result(reason: str) -> Dict[str, Any]:
        return {
            "kept": [],
            "discarded": [],
            "uncertain": [],
            "judgments": [],
            "skip_reason": reason,
            "stats": {
                "total_initial": 0,
                "total_kept": 0,
                "total_discarded": 0,
                "total_uncertain": 0,
                "batch_count": 0,
            },
        }

    @staticmethod
    def _stage_report(
        *,
        executed: bool,
        reason: Optional[str],
        input_count: int,
        output_count: int,
        started: float,
        policy: Dict[str, Any],
        stats: Dict[str, Any],
    ) -> Dict[str, Any]:
        return {
            "executed": executed,
            "reason": reason,
            "input_count": input_count,
            "output_count": output_count,
            "duration_ms": round((perf_counter() - started) * 1000, 3),
            "policy": policy,
            "stats": stats,
        }

    @staticmethod
    def _validate_inputs(
        intention_plan: Dict[str, Any],
        candidates: List[Dict[str, Any]],
    ) -> None:
        if (
            not isinstance(intention_plan, dict)
            or intention_plan.get("kind") != "intention"
        ):
            raise ValueError(
                "Intention plan must be a JSON object with kind='intention'."
            )
        if not isinstance(intention_plan.get("execution_plan"), dict):
            raise ValueError("Intention plan is missing execution_plan.")
        execution_plan = intention_plan["execution_plan"]
        for stage_name in ("cluster", "embedding", "llm_judge"):
            if not isinstance(execution_plan.get(stage_name), dict):
                raise ValueError(
                    f"Intention execution_plan is missing {stage_name}."
                )
        result_logic = intention_plan.get("result_logic")
        if not isinstance(result_logic, dict) or (
            result_logic.get("include_operator") != "all"
            or result_logic.get("exclude_operator") != "any"
        ):
            raise ValueError(
                "Intention result_logic must declare include='all' and exclude='any'."
            )
        judge_policy = execution_plan["llm_judge"]
        if judge_policy.get("candidate_source") != "embedding_gray_zone":
            raise ValueError(
                "Intention llm_judge candidate_source must be embedding_gray_zone."
            )
        if not isinstance(candidates, list):
            raise ValueError("Relation executor result must be a JSON array.")


def run_intention_executor(
    intention_plan_path: str,
    relation_executor_result_path: str,
    output_path: str,
    *,
    intention_executor: Optional[IntentionExecutor] = None,
) -> List[Dict[str, Any]]:
    """Load one plan/candidate set, execute it, and persist stage evidence."""
    intention_plan = load_res(intention_plan_path)
    candidates = load_res(relation_executor_result_path)
    executor = intention_executor or IntentionExecutor()
    results = executor.execute(intention_plan, candidates)

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    save_res(output, results)
    save_res(
        output.with_name("filtered_by_cluster.json"),
        executor.cluster_result.get("kept", []),
    )
    save_res(
        output.with_name("discarded_by_cluster.json"),
        executor.cluster_result.get("discarded", []),
    )
    save_res(
        output.with_name("cluster_rescue_candidates.json"),
        executor.cluster_result.get("rescue_candidates", []),
    )
    save_res(
        output.with_name("filtered_by_embedding.json"),
        executor.embedding_result.get("kept", []),
    )
    save_res(
        output.with_name("embedding_gray_zone.json"),
        executor.embedding_result.get("gray", []),
    )
    save_res(
        output.with_name("discarded_by_embedding.json"),
        executor.embedding_result.get("discarded", []),
    )
    save_res(
        output.with_name("LLM_judge_result_debug.json"),
        {
            key: value
            for key, value in executor.judge_result.items()
            if key not in {"kept", "accepted_gray"}
        },
    )
    save_res(
        output.with_name("intention_execution_report.json"),
        executor.execution_report,
    )
    return results


def _candidate_symbol_id(candidate: Dict[str, Any]) -> Optional[int]:
    value = candidate.get("symbol_id")
    return int(value) if _is_int_like(value) else None


def _candidate_identity(candidate: Dict[str, Any]) -> tuple[str, Any]:
    symbol_id = _candidate_symbol_id(candidate)
    return (
        ("symbol_id", symbol_id)
        if symbol_id is not None
        else ("object", id(candidate))
    )


def _is_int_like(value: Any) -> bool:
    try:
        int(value)
        return True
    except (TypeError, ValueError):
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Execute an intention physical plan")
    parser.add_argument(
        "--intention-plan",
        default=f"{QUERY_OUTPUT_DIR}/intention_semql.json",
    )
    parser.add_argument(
        "--candidates",
        default=f"{QUERY_OUTPUT_DIR}/filtered_by_relation.json",
    )
    parser.add_argument(
        "--output",
        default=f"{QUERY_OUTPUT_DIR}/intention_executor_result.json",
    )
    args = parser.parse_args()
    results = run_intention_executor(
        intention_plan_path=args.intention_plan,
        relation_executor_result_path=args.candidates,
        output_path=args.output,
    )
    print(
        json.dumps(
            {"kept": len(results), "output_path": args.output},
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
