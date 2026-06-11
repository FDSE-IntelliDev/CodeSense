"""
Fuzzy matching utilities for exact-code search.

当前主要支持 code_element 场景：
- 先做完全匹配
- 再做 token 长度差过滤
- 再做 Jaccard 相似度过滤
- 最后用 token 级加权编辑距离计算相似度

TODO:
- 对 code_line / code_snippet 场景，tokenization 需要保留符号信息；
  当前 code_element 会去除符号，仅保留字母数字和下划线片段。
"""

import re
from typing import List, Tuple

from embedding.project_term_vocab import tokenize_text


class FuzzyMatcher:
    def __init__(
        self,
        max_token_len_diff_ratio: float = 0.4,
        min_jaccard: float = 0.5,
        min_edit_similarity: float = 0.65,
    ):
        self.max_token_len_diff_ratio = max_token_len_diff_ratio
        self.min_jaccard = min_jaccard
        self.min_edit_similarity = min_edit_similarity
        self._token_cache = {}

    def match(self, query: str, candidate: str, kind: str = "code_element") -> bool:
        score, passed = self.score(query, candidate, kind=kind)
        return passed

    def score(self, query: str, candidate: str, kind: str = "code_element") -> Tuple[float, bool]:
        query = str(query or "").strip()
        candidate = str(candidate or "").strip()
        if not query or not candidate:
            return 0.0, False

        if query == candidate:
            return 1.0, True
        if query.lower() == candidate.lower():
            return 0.98, True

        query_tokens = self._tokenize(query, kind)
        candidate_tokens = self._tokenize(candidate, kind)
        if not query_tokens or not candidate_tokens:
            return 0.0, False

        if not self._token_length_compatible(query_tokens, candidate_tokens):
            return 0.0, False

        jaccard = self._jaccard(query_tokens, candidate_tokens)
        if jaccard < self.min_jaccard:
            return jaccard, False

        edit_sim = self._weighted_edit_similarity(query_tokens, candidate_tokens)
        return edit_sim, edit_sim >= self.min_edit_similarity

    def _tokenize(self, text: str, kind: str) -> List[str]:
        cache_key = (kind, str(text or ""))
        if cache_key in self._token_cache:
            return self._token_cache[cache_key]

        if kind == "code_element":
            # code_element 场景去掉符号，只保留 identifier-like 片段。
            fragments = re.findall(r"[A-Za-z0-9_]+", text)
            tokens: List[str] = []
            for fragment in fragments:
                tokens.extend(tokenize_text(fragment))
            result = [t.lower() for t in tokens if t]
            self._token_cache[cache_key] = result
            return result

        # TODO: code_line/code_snippet fuzzy match should preserve operators,
        # punctuation, literals, and token order more carefully.
        fragments = re.findall(r"[A-Za-z0-9_]+", text)
        tokens = []
        for fragment in fragments:
            tokens.extend(tokenize_text(fragment))
        result = [t.lower() for t in tokens if t]
        self._token_cache[cache_key] = result
        return result

    def _token_length_compatible(self, query_tokens: List[str], candidate_tokens: List[str]) -> bool:
        q_len = len(query_tokens)
        c_len = len(candidate_tokens)
        max_len = max(q_len, c_len)
        if max_len == 0:
            return False
        diff_ratio = abs(q_len - c_len) / max_len
        return diff_ratio <= self.max_token_len_diff_ratio

    @staticmethod
    def _jaccard(query_tokens: List[str], candidate_tokens: List[str]) -> float:
        q_set = set(query_tokens)
        c_set = set(candidate_tokens)
        union = q_set | c_set
        if not union:
            return 0.0
        return len(q_set & c_set) / len(union)

    def _weighted_edit_similarity(self, query_tokens: List[str], candidate_tokens: List[str]) -> float:
        distance = self._weighted_edit_distance(query_tokens, candidate_tokens)
        max_cost = max(len(query_tokens), len(candidate_tokens))
        if max_cost <= 0:
            return 0.0
        return max(0.0, 1.0 - distance / max_cost)

    @staticmethod
    def _substitution_cost(a: str, b: str) -> float:
        if a == b:
            return 0.0
        if a.lower() == b.lower():
            return 0.1
        if a in b or b in a:
            return 0.4
        return 1.0

    def _weighted_edit_distance(self, query_tokens: List[str], candidate_tokens: List[str]) -> float:
        m = len(query_tokens)
        n = len(candidate_tokens)
        dp = [[0.0] * (n + 1) for _ in range(m + 1)]

        for i in range(1, m + 1):
            dp[i][0] = float(i)
        for j in range(1, n + 1):
            dp[0][j] = float(j)

        for i in range(1, m + 1):
            for j in range(1, n + 1):
                delete_cost = dp[i - 1][j] + 1.0
                insert_cost = dp[i][j - 1] + 1.0
                replace_cost = dp[i - 1][j - 1] + self._substitution_cost(query_tokens[i - 1], candidate_tokens[j - 1])
                dp[i][j] = min(delete_cost, insert_cost, replace_cost)

        return dp[m][n]
