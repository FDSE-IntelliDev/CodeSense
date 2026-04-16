"""
LLM-based keyword extraction for CodeSearch queries.

Design goals from project requirements:
1) Keywords must be contiguous spans from original query text.
2) Avoid skip-gram phrases (e.g., "function performs" when "that" is in between).
3) Prefer concrete semantic terms over generic words.
4) Output stable JSON format: [{"keyword": str, "score": float}, ...].
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from openai import OpenAI

@dataclass
class LLMKeywordExtractorConfig:
    model: str = "qwen-plus"
    top_n: int = 8
    temperature: float = 0.0


class LLMKeywordExtractor:
    """Extract ranked keywords via LLM with strict post-validation."""

    def __init__(
        self,
        config: Optional[LLMKeywordExtractorConfig] = None,
        client: Any = None,
    ) -> None:
        self.config = config or LLMKeywordExtractorConfig()
        self.client = client

    def extract_keywords(self, query: str) -> List[Dict[str, float]]:
        query = (query or "").strip()
        if not query:
            return []

        raw_text = self._call_llm(query)
        parsed = self._parse_json(raw_text)
        validated = self._validate_and_normalize(query, parsed)
        # return validated[: self.config.top_n]
        return {
            "keywords": validated["keywords"][: self.config.top_n],
            "constraints": validated["constraints"][:8],
        }

    def _call_llm(self, query: str) -> str:
        if self.client is None:
            # self.client = OpenAI(base_url="https://api.poixe.com/v1",api_key="sk-wxSYiJvqK8bUVQPY3p4HRP1oePt6qFrhe4vQtrQZDLBWPCpf")
            # self.client = OpenAI(base_url="https://openkey.cloud/v1",
            #                  api_key="sk-qJN0l8K8tFDtobxlDc083e5c4d684062B49b02A5C6F3Be6a")
            self.client=OpenAI(base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",api_key="sk-e2a20472cac148bdb711c663110c4d6f")

        prompt = self._build_prompt(query)
        # OpenAI Responses API style
        resp = self.client.responses.create(
            model=self.config.model,
            temperature=self.config.temperature,
            input=[
                {
                    "role": "system",
                    "content": (
                        "You extract search keywords for code retrieval. "
                        "Return strict JSON only."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
        )
        return getattr(resp, "output_text", "") or ""

#     def _build_prompt(self, query: str) -> str:
#         return f"""
# Task: Extract ranked search keywords from the query for code search.
#
# Hard constraints:
# 1) Every keyword MUST be an exact contiguous span from the original query string.
# 2) Do NOT generate rewritten forms not present in query
#    (e.g., query has "readahead" -> do not output "read ahead").
# 3) Do NOT output skip-gram phrases that skip middle words
#    (e.g., query "function that performs" -> do not output "function performs").
# 4) Prefer concrete semantic terms/phrases (domain entities/actions) over generic words.
# 5) Keep keywords concise. Prefer single terms or short phrases.
#
# Output format (JSON only):
# [
#   {{"keyword": "string_from_query", "score": 0.0 to 1.0}},
#   ...
# ]
#
# Query:
# {query}
# """.strip()
    def _build_prompt(self, query: str) -> str:
        return f"""
    Task:
    Given a user query for code search, extract two outputs:

    A) keywords: core retrieval terms (with score)
    B) constraints: retrieval constraints that narrow search scope/context

    Hard constraints:
    1) Every extracted text span MUST be an exact contiguous substring from the original query.
    2) Do NOT rewrite terms not present in query
       (e.g., query has "readahead" -> do not output "read ahead").
    3) Do NOT output skip-gram phrases that skip middle words
       (e.g., query "function that performs" -> do not output "function performs").
    4) Prefer concrete semantic action/entity phrases as keywords.
    5) Generic context words (e.g., function/module/class/file/path/language/location-like hints)
       should be treated as constraints unless they are clearly core retrieval targets.
    6) Keep keywords concise and useful for retrieval ranking.
    7) Scores must be in [0.0, 1.0].

    How to separate keywords vs constraints:
    - keywords:
      - represent the main intent/action/entity to retrieve
      - if removed, the query intent would be significantly lost
      - usually verbs+nouns or key domain terms
    - constraints:
      - represent scope/filter conditions (module, layer, artifact type, location, language, framework, etc.)
      - refine where/how to search but are not the main target
      - include relation hints such as "in X", "under Y", "from Z", "as function/method/class"

    Output JSON only (no markdown, no extra text):
    {{
      "keywords": [
        {{"text": "exact span from query", "score": 0.0}}
      ],
      "constraints": [
        {{
          "text": "exact span from query",
          "type": "scope | location | artifact | language | framework | relation | other",
          "score": 0.0
        }}
      ]
    }}

    Additional requirements:
    - Remove duplicates.
    - Keep at most 6 keywords and 8 constraints.
    - Sort each list by score descending.
    - If uncertain, still output best-effort items with lower score.

    Example:
    query: "function that performs readahead in disk"
    valid interpretation:
    - keywords: "readahead"
    - constraints: "function", "in disk", "disk"
    invalid:
    - keyword "read ahead" (not exact span)
    - keyword "function performs" (skip-gram)

    Now process this query:
    {query}
    """.strip()

    # def _parse_json(self, text: str) -> List[Dict[str, Any]]:
    #     if not text.strip():
    #         return []
    #     try:
    #         obj = json.loads(text)
    #         if isinstance(obj, list):
    #             return [x for x in obj if isinstance(x, dict)]
    #     except json.JSONDecodeError:
    #         # Try extracting JSON array from fenced or noisy text.
    #         m = re.search(r"\[\s*\{.*\}\s*\]", text, flags=re.DOTALL)
    #         if m:
    #             try:
    #                 obj = json.loads(m.group(0))
    #                 if isinstance(obj, list):
    #                     return [x for x in obj if isinstance(x, dict)]
    #             except Exception:
    #                 return []
    #     return []
    def _parse_json(self, text: str) -> Dict[str, Any]:
        """
        Parse LLM output into object schema:
        {
          "keywords": [{"text": "...", "score": ...}, ...],
          "constraints": [{"text": "...", "type": "...", "score": ...}, ...]
        }
        """
        if not text or not text.strip():
            return {"keywords": [], "constraints": []}

        text = text.strip()

        # 1) direct parse
        try:
            obj = json.loads(text)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass

        # 2) extract largest JSON object block if model returned extra text/fences
        m = re.search(r"\{[\s\S]*\}", text)
        if m:
            try:
                obj = json.loads(m.group(0))
                if isinstance(obj, dict):
                    return obj
            except Exception:
                pass

        # 3) fallback empty
        return {"keywords": [], "constraints": []}

    # def _validate_and_normalize(
    #     self, query: str, items: List[Dict[str, Any]]
    # ) -> List[Dict[str, float]]:
    #     """
    #         Validate and normalize raw keyword candidates returned by LLM.
    #
    #         Processing rules:
    #         1) Parse each item and keep only valid `keyword`/`score` pairs.
    #         2) Enforce query-span constraint: keyword must appear as a contiguous
    #            substring in the original query (case-insensitive).
    #         3) Clamp score into [0, 1].
    #         4) Apply a mild penalty to overly long phrases (3+ terms) so concise
    #            keywords rank higher for retrieval.
    #         5) Deduplicate by keyword and keep the highest score.
    #
    #         Args:
    #             query: Original user query text.
    #             items: Raw LLM output list, each item like
    #                    {"keyword": "...", "score": float}.
    #
    #         Returns:
    #             A sorted list (descending by score) in normalized format:
    #             [{"keyword": str, "score": float}].
    #     """
    #     q_norm = self._norm_ws(query).lower()
    #     out: Dict[str, float] = {}
    #
    #     for it in items:
    #         keyword = str(it.get("keyword", "")).strip()
    #         if not keyword:
    #             continue
    #         score_raw = it.get("score", 0.0)
    #         try:
    #             score = float(score_raw)
    #         except Exception:
    #             score = 0.0
    #
    #         keyword_norm = self._norm_ws(keyword)
    #         if not keyword_norm:
    #             continue
    #
    #         # Must be contiguous substring in original query (case-insensitive).
    #         if keyword_norm.lower() not in q_norm:
    #             continue
    #
    #         # Clamp score.
    #         score = max(0.0, min(1.0, score))
    #
    #         # Slight bias against overly long phrases.
    #         n_terms = len(keyword_norm.split())
    #         if n_terms >= 4:
    #             score *= 0.8
    #         elif n_terms == 3:
    #             score *= 0.9
    #
    #         prev = out.get(keyword_norm, 0.0)
    #         if score > prev:
    #             out[keyword_norm] = score
    #
    #     ranked = sorted(out.items(), key=lambda x: x[1], reverse=True)
    #     return [{"keyword": k, "score": round(v, 4)} for k, v in ranked]
    def _validate_and_normalize(
            self, query: str, payload: Dict[str, Any]
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        Validate and normalize LLM payload with two channels: keywords + constraints.

        Rules:
        - text must be contiguous substring of original query (case-insensitive)
        - score clamped to [0, 1]
        - mild length penalty for long phrases
        - deduplicate by text, keep highest score
        - constraints normalize type into a controlled enum
        """
        q_norm = self._norm_ws(query).lower()

        raw_keywords = payload.get("keywords", [])
        raw_constraints = payload.get("constraints", [])

        if not isinstance(raw_keywords, list):
            raw_keywords = []
        if not isinstance(raw_constraints, list):
            raw_constraints = []

        keyword_map: Dict[str, float] = {}
        constraint_map: Dict[str, Dict[str, Any]] = {}

        allowed_types = {
            "scope", "location", "artifact", "language",
            "framework", "relation", "other"
        }

        def _normalize_score(v: Any) -> float:
            try:
                s = float(v)
            except Exception:
                s = 0.0
            return max(0.0, min(1.0, s))

        def _length_penalty(text: str, score: float) -> float:
            n_terms = len(text.split())
            if n_terms >= 4:
                return score * 0.8
            if n_terms == 3:
                return score * 0.9
            return score

        # keywords
        for item in raw_keywords:
            if not isinstance(item, dict):
                continue
            text = self._norm_ws(str(item.get("text", "")).strip())
            if not text:
                continue
            if text.lower() not in q_norm:
                continue

            score = _normalize_score(item.get("score", 0.0))
            score = _length_penalty(text, score)

            prev = keyword_map.get(text, 0.0)
            if score > prev:
                keyword_map[text] = round(score, 4)

        # constraints
        for item in raw_constraints:
            if not isinstance(item, dict):
                continue
            text = self._norm_ws(str(item.get("text", "")).strip())
            if not text:
                continue
            if text.lower() not in q_norm:
                continue

            c_type = str(item.get("type", "other")).strip().lower()
            if c_type not in allowed_types:
                c_type = "other"

            score = _normalize_score(item.get("score", 0.0))
            score = _length_penalty(text, score)

            prev = constraint_map.get(text)
            if prev is None or score > float(prev["score"]):
                constraint_map[text] = {
                    "text": text,
                    "type": c_type,
                    "score": round(score, 4),
                }

        keywords = [
                       {"text": k, "score": s}
                       for k, s in sorted(keyword_map.items(), key=lambda x: x[1], reverse=True)
                   ][:6]

        constraints = sorted(
            constraint_map.values(),
            key=lambda x: x["score"],
            reverse=True
        )[:8]

        return {"keywords": keywords, "constraints": constraints}

    @staticmethod
    def _norm_ws(text: str) -> str:
        return re.sub(r"\s+", " ", text.strip())
