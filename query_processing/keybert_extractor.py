"""Extract ranked search keywords from natural-language queries using KeyBERT."""
from typing import Dict, List, Tuple
from collections import defaultdict

class KeyBertExtractor:
    """Keyword extractor based on KeyBERT only."""

    def __init__(
        self,
        top_n: int = 8,
        keybert_model_name: str = "all-MiniLM-L6-v2",
    ) -> None:
        self.top_n = top_n
        self.keybert_model_name = keybert_model_name
        self._kb = None

    def extract_keywords(self, query: str) -> List[Dict[str, float]]:
        query = (query or "").strip()
        if not query:
            return []

        pairs = self._extract_with_keybert(query)
        # merged = self._merge_and_rerank(pairs, top_n=self.top_n)
        # return [{"keyword": k, "score": round(s, 4)} for k, s in merged]
        reranked = self._rerank_by_frequency_and_rank(pairs, top_n=self.top_n)
        return [{"keyword": k, "score": round(s, 4)} for k, s in reranked]

    def _extract_with_keybert(self, query: str) -> List[Tuple[str, float]]:
        try:
            if self._kb is None:
                from keybert import KeyBERT
                # If you want to force a specific embedding model, switch to:
                # self._kb = KeyBERT(model=self.keybert_model_name)
                self._kb = KeyBERT()

            raw = self._kb.extract_keywords(
                query,
                keyphrase_ngram_range=(1, 3),
                stop_words=None,
                top_n=self.top_n * 2,
                use_maxsum=True,
                nr_candidates=max(20, self.top_n * 4),
            )
            return [(k, float(v)) for k, v in raw]
        except Exception as exc:
            raise RuntimeError(f"KeyBERT extraction failed: {exc}") from exc

    def _merge_and_rerank(
        self, pairs: List[Tuple[str, float]], top_n: int
    ) -> List[Tuple[str, float]]:
        merged: Dict[str, float] = {}
        for key, score in pairs:
            if not key:
                continue
            merged[key] = max(merged.get(key, 0.0), float(score))

        reranked: List[Tuple[str, float]] = []
        for key, score in merged.items():
            # Slightly prefer phrases over single tokens.
            boost = 1.08 if len(key.split()) >= 2 else 1.0
            reranked.append((key, min(1.0, score * boost)))

        reranked.sort(key=lambda x: x[1], reverse=True)
        return reranked[:top_n]


    def _rerank_by_frequency_and_rank(
        self, pairs: List[Tuple[str, float]], top_n: int
    ) -> List[Tuple[str, float]]:
        """
        Build token/phrase candidates from keybert phrases, then rerank by:
        - base score (from keybert)
        - weighted frequency across all phrases
        - position (appearing in higher-ranked phrases)
        - phrase length preference (1~2 grams preferred for retrieval)
        """
        if not pairs:
            return []

        # 1) collect candidate units (unigram + bigram + original phrase)
        base_score = defaultdict(float)
        freq_weighted = defaultdict(float)
        rank_weighted = defaultdict(float)
        occurrence_count = defaultdict(int)

        for rank_idx, (phrase, score) in enumerate(pairs, start=1):
            toks = [t for t in phrase.split() if t]
            if not toks:
                continue
            # position weight: higher-ranked source phrase contributes more
            pos_w = 1.0 / rank_idx

            # candidate units from this phrase
            units = set()
            units.add(" ".join(toks))  # original phrase
            for t in toks:
                units.add(t)  # unigram
            for i in range(len(toks) - 1):
                units.add(f"{toks[i]} {toks[i+1]}")  # bigram

            for u in units:
                # keep strongest base score seen for this unit
                if score > base_score[u]:
                    base_score[u] = score

                # frequency and rank signals aggregated across source phrases
                freq_weighted[u] += score
                rank_weighted[u] += pos_w * score
                occurrence_count[u] += 1

        candidates = list(base_score.keys())

        # 2) normalize helpers
        def _normalize(value: float, max_value: float) -> float:
            if max_value <= 0:
                return 0.0
            return value / max_value

        max_base = max(base_score.values()) if base_score else 1.0
        max_freq = max(freq_weighted.values()) if freq_weighted else 1.0
        max_rank = max(rank_weighted.values()) if rank_weighted else 1.0

        # 3) final score fusion
        scored: List[Tuple[str, float]] = []
        for c in candidates:
            ngram_len = len(c.split())

            # Prefer concise retrieval units (1~2 grams), penalize long phrases.
            if ngram_len == 1:
                length_bonus = 1.0
            elif ngram_len == 2:
                length_bonus = 0.92
            elif ngram_len == 3:
                length_bonus = 0.75
            else:
                length_bonus = 0.55

            s_base = _normalize(base_score[c], max_base)
            s_freq = _normalize(freq_weighted[c], max_freq)
            s_rank = _normalize(rank_weighted[c], max_rank)

            final = (0.45 * s_base + 0.30 * s_freq + 0.20 * s_rank + 0.05 * length_bonus)

            # Small lift for units that appear in multiple source phrases
            if occurrence_count[c] >= 2:
                final *= 1.05

            scored.append((c, min(final, 1.0)))

        # 4) dedup + sort
        scored.sort(key=lambda x: x[1], reverse=True)

        # Optional light cleanup: remove exact duplicates already handled; keep top_n
        return scored[:top_n]