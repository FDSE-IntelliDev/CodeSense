"""
Embedding-based fine-grained filter for code search candidates.

定位：
- Cluster pipeline 负责粗粒度语义过滤/分层
- 本模块负责在 cluster 保留结果上做更细粒度的 term-level embedding 过滤

输入：
- candidates: 候选代码元素列表（通常来自 cluster priority_1/2/3）
- query_profile: IntentionPlanner 生成的正向/负向项目术语
- policy: IntentionPlanner 生成的阈值、权重和灰区规则

输出：
- kept / discarded
- tiers: priority_1 / priority_2 / priority_3 / priority_4_discarded
- 每个 symbol 附带 _embedding_score / _embedding_evidence / _final_filter_score
"""

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

sys.path.append(str(Path(__file__).parent.parent))

from definition import QUERY_OUTPUT_DIR
from embedding.embedding_main import score_pair_by_average_vector as score_pair
from embedding.project_term_vocab import tokenize_text
import numpy as np
import re


class EmbeddingFilter:
    """Use trained term embedding model as fine-grained candidate filter."""

    SUPPORTED_DECISION_ORDER = [
        "exclude_hard_veto",
        "absolute_positive",
        "absolute_weak_negative",
        "collect_unresolved",
        "distribution_guard",
        "adaptive_distribution",
    ]
    GRAY_PRIORITY = {
        "gray_conflict": 0,
        "gray_high": 1,
        "gray_middle": 2,
        "gray_distribution_guard": 3,
        "gray_low": 4,
    }

    def __init__(
        self,
        include_hard_discard_threshold: float = 0.2,
        include_accept_threshold: float = 0.55,
        exclude_gray_threshold: float = 0.35,
        exclude_discard_threshold: float = 0.6,
        cluster_weight: float = 0.4,
        include_weight: float = 0.6,
        exclude_penalty_weight: float = 0.4,
        max_candidate_terms: int = 80,
        priority_1_ratio: float = 0.1,
        priority_2_ratio: float = 0.2,
        decision_order: Any = None,
        conflict_policy: Any = None,
        adaptive_distribution: Any = None,
    ):
        self.include_hard_discard_threshold = include_hard_discard_threshold
        self.include_accept_threshold = include_accept_threshold
        self.exclude_gray_threshold = exclude_gray_threshold
        self.exclude_discard_threshold = exclude_discard_threshold
        self.cluster_weight = cluster_weight
        self.include_weight = include_weight
        self.exclude_penalty_weight = exclude_penalty_weight
        self.max_candidate_terms = max_candidate_terms
        self.priority_1_ratio = priority_1_ratio
        self.priority_2_ratio = priority_2_ratio
        self.decision_order = (
            list(decision_order)
            if isinstance(decision_order, list)
            else list(self.SUPPORTED_DECISION_ORDER)
        )
        self.conflict_policy = (
            dict(conflict_policy) if isinstance(conflict_policy, dict) else {}
        )
        strong_tiers = self.conflict_policy.get(
            "cluster_support_tiers",
            ["priority_1", "priority_2"],
        )
        self.cluster_support_tiers = frozenset(
            str(tier) for tier in strong_tiers
        )
        self.cluster_support_include_ceiling = float(
            self.conflict_policy.get(
                "cluster_support_include_ceiling",
                0.35,
            )
        )
        self.cluster_support_min_gap = float(
            self.conflict_policy.get("cluster_support_min_gap", 0.25)
        )
        self.polarity_include_floor = float(
            self.conflict_policy.get("polarity_include_floor", 0.45)
        )
        self.adaptive_distribution = (
            dict(adaptive_distribution)
            if isinstance(adaptive_distribution, dict)
            else {}
        )
        self._pair_score_cache: Dict[Tuple[str, str], Dict[str, Any]] = {}

    # ---------- term extraction ----------

    @staticmethod
    def extract_query_terms(values: Any) -> List[str]:
        """Tokenize one Planner-provided term list without reparsing SemCon."""
        terms: List[str] = []
        if not isinstance(values, list):
            return terms
        for value in values:
            if value is not None:
                terms.extend(tokenize_text(str(value)))

        # Preserve order while deduplicating.
        seen = set()
        out = []
        for term in terms:
            if term in seen:
                continue
            seen.add(term)
            out.append(term)
        return out

    def extract_candidate_terms(self, symbol: Dict[str, Any]) -> List[str]:
        """Extract candidate terms from stable symbol metadata.

        Only use signature/container/name for filtering. Do not use type/doc/code body
        here to keep this fine-grained filter stable and avoid noisy code tokens.
        """
        terms: List[str] = []

        def add_text(text: Any):
            if text is None:
                return
            cleaned = re.sub(r"[^A-Za-z0-9_]+", " ", str(text))
            for fragment in cleaned.split():
                for token in tokenize_text(fragment):
                    terms.append(token)

        add_text(symbol.get("signature", ""))
        add_text(symbol.get("container", ""))
        add_text(symbol.get("name", ""))

        seen = set()
        out = []
        for term in terms:
            if term in seen:
                continue
            seen.add(term)
            out.append(term)
            if len(out) >= self.max_candidate_terms:
                break
        return out

    # ---------- scoring ----------

    def _score_pair_cached(self, query_term: str, candidate_term: str) -> Dict[str, Any]:
        key = (query_term, candidate_term)
        if key not in self._pair_score_cache:
            self._pair_score_cache[key] = score_pair(query_term, candidate_term)
        return self._pair_score_cache[key]

    def score_candidate(
        self,
        query_terms: List[str],
        candidate_terms: List[str],
        *,
        aggregation: str = "average",
    ) -> Tuple[float, List[Dict[str, Any]]]:
        """Score one candidate by best candidate-term match per query-term."""
        if not query_terms or not candidate_terms:
            return 0.0, []

        evidence = []
        for q in query_terms:
            best = None
            for c in candidate_terms:
                result = self._score_pair_cached(q, c)
                score = float(result.get("final_score", 0.0))
                if best is None or score > best["score"]:
                    best = {
                        "query_term": q,
                        "candidate_term": c,
                        "score": round(score, 6),
                        "co_score": result.get("co_score", 0.0),
                        "sem_score": result.get("sem_score", 0.0),
                        "rank_source": result.get("rank_source", ""),
                    }
            if best is not None:
                evidence.append(best)

        if not evidence:
            return 0.0, []

        # Include terms use coverage-style average; exclude=any uses maximum.
        if aggregation == "max":
            score = max(item["score"] for item in evidence)
        else:
            score = sum(item["score"] for item in evidence) / len(evidence)
        return float(score), evidence

    def run_filter(
        self,
        candidates: List[Dict[str, Any]],
        query_profile: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Run fine-grained embedding filter on candidate symbols."""
        if not candidates:
            return {
                "kept": [],
                "discarded": [],
                "tiers": self._empty_tiers(),
                "stats": {},
            }

        include_terms = self.extract_query_terms(
            query_profile.get("include_terms", [])
        )
        exclude_terms = self.extract_query_terms(
            query_profile.get("exclude_terms", [])
        )
        if self.decision_order != self.SUPPORTED_DECISION_ORDER:
            raise ValueError(
                "Unsupported intention embedding decision_order: "
                f"{self.decision_order}"
            )

        auto_kept: List[Dict[str, Any]] = []
        gray: List[Dict[str, Any]] = []
        discarded: List[Dict[str, Any]] = []
        unresolved: List[Dict[str, Any]] = []
        tiers = self._empty_tiers()

        # Score every candidate once. Cluster priority_4 candidates are included
        # here when the Planner enables rescue, so a strong candidate-level
        # include score can override a weak cluster-level signal.
        for symbol in candidates:
            candidate_terms = self.extract_candidate_terms(symbol)
            include_score, include_evidence = self.score_candidate(
                include_terms,
                candidate_terms,
            )
            exclude_score, exclude_evidence = self.score_candidate(
                exclude_terms,
                candidate_terms,
                aggregation="max",
            )
            if not include_terms:
                include_score = 1.0

            has_cluster_score = "_cluster_score" in symbol
            raw_cluster_score = (
                float(symbol.get("_cluster_score", 0.0) or 0.0)
                if has_cluster_score
                else 0.0
            )
            cluster_score = min(1.0, max(0.0, raw_cluster_score))
            positive_weight = self.include_weight
            positive_total = self.include_weight * include_score
            if has_cluster_score:
                positive_weight += self.cluster_weight
                positive_total += self.cluster_weight * cluster_score
            positive_score = (
                positive_total / positive_weight if positive_weight > 0 else include_score
            )
            final_score = positive_score - self.exclude_penalty_weight * exclude_score

            symbol["_embedding_score"] = round(include_score, 6)
            symbol["_include_embedding_score"] = round(include_score, 6)
            symbol["_exclude_embedding_score"] = round(exclude_score, 6)
            symbol["_normalized_cluster_score"] = round(cluster_score, 6)
            symbol["_embedding_evidence"] = include_evidence
            symbol["_include_embedding_evidence"] = include_evidence
            symbol["_exclude_embedding_evidence"] = exclude_evidence
            symbol["_candidate_terms"] = candidate_terms
            symbol["_final_filter_score"] = round(final_score, 6)

            conflicts = self._detect_conflicts(
                include_score=include_score,
                exclude_score=exclude_score,
                cluster_score=cluster_score,
                has_cluster_score=has_cluster_score,
                has_include_terms=bool(include_terms),
                has_exclude_terms=bool(exclude_terms),
                cluster_tier=str(symbol.get("_cluster_tier") or ""),
            )
            symbol["_embedding_conflicts"] = conflicts

            decision, reason = self._absolute_decision(
                include_score=include_score,
                exclude_score=exclude_score,
                has_include_terms=bool(include_terms),
                has_exclude_terms=bool(exclude_terms),
                conflicts=conflicts,
            )
            if decision == "auto_keep":
                self._set_decision(symbol, decision, reason)
                auto_kept.append(symbol)
            elif decision == "discard":
                self._set_decision(symbol, decision, reason)
                symbol["_embedding_filter_reason"] = reason
                discarded.append(symbol)
            elif decision == "gray":
                self._set_decision(
                    symbol,
                    decision,
                    reason,
                    gray_zone="gray_conflict",
                )
                gray.append(symbol)
            else:
                unresolved.append(symbol)

        distribution_stats = self._classify_unresolved(
            unresolved=unresolved,
            total_candidate_count=len(candidates),
            auto_kept=auto_kept,
            gray=gray,
            discarded=discarded,
        )

        kept = auto_kept + gray
        kept.sort(
            key=lambda item: float(item.get("_final_filter_score", 0.0)),
            reverse=True,
        )
        auto_kept.sort(
            key=lambda item: float(item.get("_final_filter_score", 0.0)),
            reverse=True,
        )
        gray.sort(
            key=lambda item: (
                self.GRAY_PRIORITY.get(
                    str(item.get("_embedding_gray_zone") or ""),
                    len(self.GRAY_PRIORITY),
                ),
                -float(item.get("_final_filter_score", 0.0)),
            ),
        )
        discarded.sort(
            key=lambda item: float(item.get("_final_filter_score", 0.0)),
            reverse=True,
        )

        self._assign_priority_tiers(kept, tiers)
        tiers["priority_4_discarded"].extend(discarded)
        for rank, symbol in enumerate(tiers["priority_4_discarded"], start=1):
            symbol["_embedding_tier"] = "priority_4_discarded"
            symbol["_embedding_tier_rank"] = str(rank)

        stats = {
            "total_initial": len(candidates),
            "total_kept": len(kept),
            "total_discarded": len(discarded),
            "total_auto_kept": len(auto_kept),
            "total_gray": len(gray),
            "include_terms": include_terms,
            "exclude_terms": exclude_terms,
            "decision_order": self.decision_order,
            "distribution": distribution_stats,
            "gray_zone_counts": self._count_by_field(
                gray,
                "_embedding_gray_zone",
            ),
            "decision_reason_counts": self._count_by_field(
                auto_kept + gray + discarded,
                "_embedding_decision_reason",
            ),
            "tier_symbol_counts": {tier: len(items) for tier, items in tiers.items()},
        }
        return {
            "kept": kept,
            "auto_kept": auto_kept,
            "gray": gray,
            "discarded": discarded,
            "tiers": tiers,
            "stats": stats,
        }

    def _detect_conflicts(
        self,
        *,
        include_score: float,
        exclude_score: float,
        cluster_score: float,
        has_cluster_score: bool,
        has_include_terms: bool,
        has_exclude_terms: bool,
        cluster_tier: str,
    ) -> List[str]:
        """Return typed, directional conflicts that require LLM judgment."""
        conflicts = []
        cluster_support_conflict = (
            has_cluster_score
            and has_include_terms
            and cluster_tier in self.cluster_support_tiers
            and include_score < self.cluster_support_include_ceiling
            and cluster_score - include_score >= self.cluster_support_min_gap
        )
        if cluster_support_conflict:
            conflicts.append("cluster_support_conflict")

        polarity_conflict = (
            has_exclude_terms
            and include_score >= self.polarity_include_floor
            and self.exclude_gray_threshold <= exclude_score
            < self.exclude_discard_threshold
        )
        if polarity_conflict:
            conflicts.append("intention_polarity_conflict")
        return conflicts

    def _absolute_decision(
        self,
        *,
        include_score: float,
        exclude_score: float,
        has_include_terms: bool,
        has_exclude_terms: bool,
        conflicts: List[str],
    ) -> Tuple[str, str]:
        """Execute the Planner-declared hard rules before query-local ranking."""
        if has_exclude_terms and exclude_score >= self.exclude_discard_threshold:
            return "discard", "exclude_hard_veto"

        include_is_confident = (
            not has_include_terms
            or include_score >= self.include_accept_threshold
        )
        exclude_is_clear = (
            not has_exclude_terms
            or exclude_score < self.exclude_gray_threshold
        )
        if include_is_confident and exclude_is_clear:
            return "auto_keep", "absolute_positive"

        if (
            has_include_terms
            and include_score < self.include_hard_discard_threshold
            and "cluster_support_conflict" not in conflicts
        ):
            return "discard", "absolute_weak_negative"

        if conflicts:
            return "gray", conflicts[0]
        return "unresolved", "collect_unresolved"

    def _classify_unresolved(
        self,
        *,
        unresolved: List[Dict[str, Any]],
        total_candidate_count: int,
        auto_kept: List[Dict[str, Any]],
        gray: List[Dict[str, Any]],
        discarded: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Apply the guarded Q40/Q80 policy to candidates left by hard rules."""
        policy = self.adaptive_distribution
        enabled = bool(policy.get("enabled", True))
        min_total = max(1, int(policy.get("min_candidate_count", 10)))
        min_unresolved = max(
            1,
            int(policy.get("min_unresolved_candidate_count", 8)),
        )
        robust_low_q = self._bounded_quantile(
            policy.get("robust_spread_lower_quantile", 0.1)
        )
        robust_high_q = self._bounded_quantile(
            policy.get("robust_spread_upper_quantile", 0.9)
        )
        min_spread = max(
            0.0,
            float(policy.get("min_robust_score_spread", 0.1)),
        )
        sorted_scores = sorted(
            float(symbol.get("_final_filter_score", 0.0))
            for symbol in unresolved
        )
        robust_low = self._quantile(sorted_scores, robust_low_q)
        robust_high = self._quantile(sorted_scores, robust_high_q)
        robust_spread = robust_high - robust_low

        guard_reasons = []
        if not enabled:
            guard_reasons.append("adaptive_distribution_disabled")
        if total_candidate_count < min_total:
            guard_reasons.append("candidate_count_below_distribution_minimum")
        if len(unresolved) < min_unresolved:
            guard_reasons.append("unresolved_count_below_distribution_minimum")
        if sorted_scores and robust_spread < min_spread:
            guard_reasons.append("robust_score_spread_below_minimum")

        stats = {
            "enabled": enabled,
            "applied": not guard_reasons and bool(unresolved),
            "unresolved_count": len(unresolved),
            "guard_reasons": guard_reasons,
            "robust_low_score": round(robust_low, 6),
            "robust_high_score": round(robust_high, 6),
            "robust_score_spread": round(robust_spread, 6),
            "lower_boundary": None,
            "upper_boundary": None,
        }
        if not unresolved:
            return stats

        if guard_reasons:
            for symbol in unresolved:
                self._set_decision(
                    symbol,
                    "gray",
                    guard_reasons[0],
                    gray_zone="gray_distribution_guard",
                )
                gray.append(symbol)
            return stats

        lower_q = self._bounded_quantile(policy.get("lower_quantile", 0.4))
        upper_q = self._bounded_quantile(policy.get("upper_quantile", 0.8))
        lower_boundary = self._quantile(sorted_scores, lower_q)
        upper_boundary = self._quantile(sorted_scores, upper_q)
        tie_tolerance = max(0.0, float(policy.get("tie_tolerance", 0.01)))
        accept_include_floor = float(
            policy.get("adaptive_accept_include_floor", 0.45)
        )
        accept_exclude_ceiling = float(
            policy.get("adaptive_accept_exclude_ceiling", 0.25)
        )
        discard_include_ceiling = float(
            policy.get("adaptive_discard_include_ceiling", 0.35)
        )
        stats["lower_boundary"] = round(lower_boundary, 6)
        stats["upper_boundary"] = round(upper_boundary, 6)

        for symbol in unresolved:
            score = float(symbol.get("_final_filter_score", 0.0))
            include_score = float(
                symbol.get("_include_embedding_score", 0.0)
            )
            exclude_score = float(
                symbol.get("_exclude_embedding_score", 0.0)
            )
            conflicts = symbol.get("_embedding_conflicts", [])

            if score >= upper_boundary - tie_tolerance:
                if (
                    score > upper_boundary + tie_tolerance
                    and include_score >= accept_include_floor
                    and exclude_score < accept_exclude_ceiling
                    and not conflicts
                ):
                    self._set_decision(
                        symbol,
                        "auto_keep",
                        "adaptive_positive",
                    )
                    auto_kept.append(symbol)
                else:
                    self._set_decision(
                        symbol,
                        "gray",
                        "adaptive_high_unresolved",
                        gray_zone="gray_high",
                    )
                    gray.append(symbol)
            elif score < lower_boundary - tie_tolerance:
                if include_score < discard_include_ceiling and not conflicts:
                    self._set_decision(
                        symbol,
                        "discard",
                        "adaptive_weak_negative",
                    )
                    symbol["_embedding_filter_reason"] = (
                        "adaptive_weak_negative"
                    )
                    discarded.append(symbol)
                else:
                    self._set_decision(
                        symbol,
                        "gray",
                        "adaptive_low_unresolved",
                        gray_zone="gray_low",
                    )
                    gray.append(symbol)
            else:
                self._set_decision(
                    symbol,
                    "gray",
                    "adaptive_middle_unresolved",
                    gray_zone="gray_middle",
                )
                gray.append(symbol)
        return stats

    @staticmethod
    def _set_decision(
        symbol: Dict[str, Any],
        decision: str,
        reason: str,
        *,
        gray_zone: Any = None,
    ) -> None:
        symbol["_embedding_decision"] = decision
        symbol["_embedding_decision_reason"] = reason
        symbol["_embedding_decision_reasons"] = [reason]
        if gray_zone is not None:
            symbol["_embedding_gray_zone"] = str(gray_zone)

    @staticmethod
    def _bounded_quantile(value: Any) -> float:
        return min(1.0, max(0.0, float(value)))

    @staticmethod
    def _quantile(sorted_values: List[float], quantile: float) -> float:
        """Linear quantile over one already-sorted score list."""
        if not sorted_values:
            return 0.0
        position = (len(sorted_values) - 1) * quantile
        lower = int(np.floor(position))
        upper = int(np.ceil(position))
        if lower == upper:
            return float(sorted_values[lower])
        fraction = position - lower
        return float(
            sorted_values[lower]
            + (sorted_values[upper] - sorted_values[lower]) * fraction
        )

    @staticmethod
    def _count_by_field(
        symbols: List[Dict[str, Any]],
        field_name: str,
    ) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for symbol in symbols:
            value = str(symbol.get(field_name) or "unknown")
            counts[value] = counts.get(value, 0) + 1
        return counts

    @staticmethod
    def _empty_tiers() -> Dict[str, List[Dict[str, Any]]]:
        return {
            "priority_1": [],
            "priority_2": [],
            "priority_3": [],
            "priority_4_discarded": [],
        }

    def _assign_priority_tiers(
        self,
        kept: List[Dict[str, Any]],
        tiers: Dict[str, List[Dict[str, Any]]],
    ):
        total = len(kept)
        if total == 0:
            return

        p1_count = max(1, int(np.ceil(total * self.priority_1_ratio)))
        p2_count = int(np.ceil(total * self.priority_2_ratio))
        p3_count = total - p1_count - p2_count
        if p3_count < 0:
            p3_count = 0

        boundaries = [
            ("priority_1", 0, p1_count),
            ("priority_2", p1_count, min(total, p1_count + p2_count)),
            ("priority_3", min(total, p1_count + p2_count), total),
        ]

        for tier, start, end in boundaries:
            for rank, symbol in enumerate(kept[start:end], start=1):
                symbol["_embedding_tier"] = tier
                symbol["_embedding_tier_rank"] = str(rank)
                tiers[tier].append(symbol)


def run_embedding_filter(
    candidates: List[Dict[str, Any]],
    query_profile: Dict[str, Any],
    policy: Dict[str, Any],
) -> Dict[str, Any]:
    return EmbeddingFilter(
        include_hard_discard_threshold=float(
            policy.get("include_hard_discard_threshold", 0.2)
        ),
        include_accept_threshold=float(
            policy.get("include_accept_threshold", 0.55)
        ),
        exclude_gray_threshold=float(policy.get("exclude_gray_threshold", 0.35)),
        exclude_discard_threshold=float(
            policy.get("exclude_discard_threshold", 0.6)
        ),
        cluster_weight=float(policy.get("cluster_weight", 0.4)),
        include_weight=float(policy.get("include_weight", 0.6)),
        exclude_penalty_weight=float(policy.get("exclude_penalty_weight", 0.4)),
        max_candidate_terms=max(1, int(policy.get("max_candidate_terms", 80))),
        priority_1_ratio=min(
            1.0,
            max(0.0, float(policy.get("priority_1_ratio", 0.1))),
        ),
        priority_2_ratio=min(
            1.0,
            max(0.0, float(policy.get("priority_2_ratio", 0.2))),
        ),
        decision_order=policy.get("decision_order"),
        conflict_policy=policy.get("conflict_policy"),
        adaptive_distribution=policy.get("adaptive_distribution"),
    ).run_filter(candidates, query_profile)


if __name__ == "__main__":
    result_file_path = Path(QUERY_OUTPUT_DIR) / "filtered_by_cluster.json"
    semql_path = Path(QUERY_OUTPUT_DIR) / "intention_semql.json"

    with open(result_file_path, "r", encoding="utf-8") as f:
        candidates = json.load(f)
    with open(semql_path, "r", encoding="utf-8") as f:
        intention_plan = json.load(f)

    result = run_embedding_filter(
        candidates,
        intention_plan.get("query_profile", {}),
        intention_plan.get("execution_plan", {}).get("embedding", {}),
    )
    output_path = Path(QUERY_OUTPUT_DIR) / "filtered_by_embedding.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"Embedding filter result saved to: {output_path}")
