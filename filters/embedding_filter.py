"""
Embedding-based fine-grained filter for code search candidates.

定位：
- Cluster pipeline 负责粗粒度语义过滤/分层
- 本模块负责在 cluster 保留结果上做更细粒度的 term-level embedding 过滤

输入：
- candidates: 候选代码元素列表（通常来自 cluster priority_1/2/3）
- semql_query: SemQL 查询结构

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
from query_processing.semql_utils import extract_semql_text_terms
import numpy as np
import re


class EmbeddingFilter:
    """Use trained term embedding model as fine-grained candidate filter."""

    def __init__(
        self,
        embedding_threshold: float = 0.35,
        cluster_weight: float = 0.4,
        embedding_weight: float = 0.6,
        max_candidate_terms: int = 80,
    ):
        self.embedding_threshold = embedding_threshold
        self.cluster_weight = cluster_weight
        self.embedding_weight = embedding_weight
        self.max_candidate_terms = max_candidate_terms
        self._pair_score_cache: Dict[Tuple[str, str], Dict[str, Any]] = {}

    # ---------- term extraction ----------

    def extract_query_terms(self, semql_query: Dict[str, Any]) -> List[str]:
        """Extract query terms from structured SemQL fields.

        Prefer intent/keywords/filters because they are already distilled from the
        raw natural-language query. raw_query is only used as a fallback when noextrac
        structured terms are available, to avoid generic NL words polluting the
        term-level embedding filter.
        """
        terms: List[str] = []

        def add_text(text: Any):
            if text is None:
                return
            for token in tokenize_text(str(text)):
                terms.append(token)

        for condition_type, term_name in (
            ("surface", "keywords"),
            ("surface", "synonyms"),
            ("intention", "keywords"),
            ("intention", "intent"),
        ):
            for term in extract_semql_text_terms(
                semql_query,
                properties=("include",),
                condition_type=condition_type,
                term_name=term_name,
            ):
                add_text(term)

        if not terms:
            for term in extract_semql_text_terms(semql_query, term_name="raw_query"):
                add_text(term)

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

    def score_candidate(self, query_terms: List[str], candidate_terms: List[str]) -> Tuple[float, List[Dict[str, Any]]]:
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

        # Query term coverage style score: each query term contributes its best match.
        score = sum(item["score"] for item in evidence) / len(evidence)
        return float(score), evidence

    def run_filter(self, candidates: List[Dict[str, Any]], semql_query: Dict[str, Any]) -> Dict[str, Any]:
        """Run fine-grained embedding filter on candidate symbols."""
        if not candidates:
            return {"kept": [], "discarded": [], "tiers": self._empty_tiers(), "stats": {}}

        query_terms = self.extract_query_terms(semql_query)
        kept = []
        discarded = []
        tiers = self._empty_tiers()

        for symbol in candidates:
            candidate_terms = self.extract_candidate_terms(symbol)
            embedding_score, evidence = self.score_candidate(query_terms, candidate_terms)
            cluster_score = float(symbol.get("_cluster_score", 0.0) or 0.0)
            final_score = self.cluster_weight * cluster_score + self.embedding_weight * embedding_score

            symbol["_embedding_score"] = round(embedding_score, 6)
            symbol["_embedding_evidence"] = evidence
            symbol["_candidate_terms"] = candidate_terms
            symbol["_final_filter_score"] = round(final_score, 6)

            if embedding_score >= self.embedding_threshold:
                kept.append(symbol)
            else:
                symbol["_embedding_filter_reason"] = "embedding_score_below_threshold"
                discarded.append(symbol)

        kept.sort(key=lambda item: float(item.get("_final_filter_score", 0.0)), reverse=True)
        discarded.sort(key=lambda item: float(item.get("_final_filter_score", 0.0)), reverse=True)

        self._assign_priority_tiers(kept, tiers)
        tiers["priority_4_discarded"].extend(discarded)
        for rank, symbol in enumerate(tiers["priority_4_discarded"], start=1):
            symbol["_embedding_tier"] = "priority_4_discarded"
            symbol["_embedding_tier_rank"] = str(rank)

        stats = {
            "total_initial": len(candidates),
            "total_kept": len(kept),
            "total_discarded": len(discarded),
            "query_terms": query_terms,
            "tier_symbol_counts": {tier: len(items) for tier, items in tiers.items()},
        }
        return {
            "kept": kept,
            "discarded": discarded,
            "tiers": tiers,
            "stats": stats,
        }

    @staticmethod
    def _empty_tiers() -> Dict[str, List[Dict[str, Any]]]:
        return {
            "priority_1": [],
            "priority_2": [],
            "priority_3": [],
            "priority_4_discarded": [],
        }

    def _assign_priority_tiers(self, kept: List[Dict[str, Any]], tiers: Dict[str, List[Dict[str, Any]]]):
        total = len(kept)
        if total == 0:
            return

        p1_count = max(1, int(np.ceil(total * 0.10)))
        p2_count = int(np.ceil(total * 0.20))
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


def run_embedding_filter(candidates: List[Dict[str, Any]], semql_query: Dict[str, Any]) -> Dict[str, Any]:
    return EmbeddingFilter().run_filter(candidates, semql_query)


if __name__ == "__main__":
    result_file_path = Path(QUERY_OUTPUT_DIR) / "filtered_by_cluster.json"
    semql_path = Path(QUERY_OUTPUT_DIR) / "semQL.json"

    with open(result_file_path, "r", encoding="utf-8") as f:
        candidates = json.load(f)
    with open(semql_path, "r", encoding="utf-8") as f:
        semql = json.load(f)

    result = run_embedding_filter(candidates, semql)
    output_path = Path(QUERY_OUTPUT_DIR) / "filtered_by_embedding.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"Embedding filter result saved to: {output_path}")
