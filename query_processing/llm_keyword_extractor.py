"""
LLM-based keyword extraction for CodeSearch queries.

Design goals from project requirements:
1) Keywords must be contiguous spans from original query text.
2) Avoid skip-gram phrases (e.g., "function performs" when "that" is in between).
3) Prefer concrete semantic terms over generic words.
4) Output stable JSON format: [{"keyword": str, "score": float}, ...].
"""

import json
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, Optional
from utils.llm_api import call_response_llm

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
        dsl_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "DSL", "query_dsl_new.json")
        with open(dsl_path, "r", encoding="utf-8") as f:
            self.dsl = json.load(f)

    def extract_keywords(self, query: str) -> Dict[str, Any]:
        query = (query or "").strip()
        if not query:
            return {"keywords": [], "target": "any", "filters": [], "exclude": [], "raw_query": query}

        raw_text = self._call_llm(query)
        parsed = self._parse_json(raw_text)
        # validated = self._validate_and_normalize(query, parsed)
        return parsed

    def _call_llm(self, query: str) -> str:
        prompt = self._build_prompt(query)
        prompt_messages: Any = [
            {
                "role": "system",
                "content": (
                    "You extract search keywords for code retrieval. "
                    "Return strict JSON only."
                ),
            },
            {"role": "user", "content": prompt},
        ]
        return call_response_llm(
            input=prompt_messages,
            model=self.config.model,
            temperature=self.config.temperature,
            client=self.client,
        )
    def _build_prompt(self, query: str) -> str:
        dsl_schema = json.dumps(self.dsl, indent=2, ensure_ascii=False)
        return f"""
You are an expert code search query parser.

Your task is to convert a natural language query into a structured DSL JSON.

## DSL schema
{dsl_schema}

## Instructions

### 1. Intent Extraction (VERY IMPORTANT)

- If the query clearly expresses an action-object relationship (e.g., "add user", "delete file"):
  - Extract:
    - action = verb
    - object = noun
  - Generate synonyms for BOTH.

- If NO clear action-object structure:
  - Set "intent" to null

### 2. Keywords Extraction

- Always extract at least one keyword phrase.
- Prefer meaningful technical phrases.
- Include combined phrases (e.g., "add user", "memory mapping").

### 3. Synonyms

- For action:
  - Include verbs with similar semantics (e.g., add → create, insert).
- For object:
  - Include domain-related equivalents (user → account, member).
- For keyword:
  - Include natural variations.

### 4. Target

- Infer from query:
  function, class, method, variable, file, module
- Default: "function"

### 5. Filters

- Extract domain or intent constraints:
  e.g., disk, network, performance, security

### 6. Exclude

- Extract negative constraints if present.

### 7. Output rules

- Output valid JSON ONLY.
- No explanation.
- If no intent → "intent": null

## Example

Input:
"functions that add user accounts"

Output:
{{
  "intent": {{
    "action": {{
      "term": "add",
      "synonyms": ["create", "insert", "register"]
    }},
    "object": {{
      "term": "user",
      "synonyms": ["account", "member"]
    }}
  }},
  "keywords": [
    {{
      "term": "add user",
      "synonyms": ["create user", "register user"]
    }}
  ],
  "target": "function",
  "filters": [],
  "exclude": [],
  "raw_query": "functions that add user accounts"
}}

## Now process:
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
        empty = {"keywords": [], "target": "any", "filters": [], "exclude": [], "raw_query": ""}
        if not text or not text.strip():
            return empty

        text = text.strip()

        try:
            obj = json.loads(text)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass

        m = re.search(r"\{[\s\S]*}", text)
        if m:
            try:
                obj = json.loads(m.group(0))
                if isinstance(obj, dict):
                    return obj
            except Exception:
                pass

        return empty

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
    ) -> Dict[str, Any]:
        """
        对LLM返回的原始DSL结果进行校验和归一化，确保输出符合DSL规范。

        处理逻辑：
        1) keywords: 校验每个term必须是原始query的连续子串（大小写不敏感），
           去重，保留synonyms列表，最多保留top_n个。
        2) target: 校验是否属于允许的代码元素类型枚举，不合法则回退为"any"。
        3) filters: 校验concept非空且去重，relation归一化到允许的枚举值，
           不合法则回退为"related_to"，最多保留8个。
        4) exclude: 清理为非空字符串列表。
        5) raw_query: 强制使用原始query，不信任LLM的输出。
        """
        q_norm = self._norm_ws(query).lower()

        allowed_targets = {
            "function", "class", "method", "variable", "constant",
            "interface", "struct", "module", "file", "any"
        }
        allowed_relations = {
            "related_to", "uses", "implements", "extends", "calls",
            "is_called_by", "contains", "returns", "handles", "modifies", "optimizes"
        }

        raw_keywords = payload.get("keywords", [])
        if not isinstance(raw_keywords, list):
            raw_keywords = []

        keywords = []
        seen_terms = set()
        for item in raw_keywords:
            if not isinstance(item, dict):
                continue
            term = self._norm_ws(str(item.get("term", "")).strip())
            if not term or term.lower() in seen_terms:
                continue
            if term.lower() not in q_norm:
                continue
            synonyms = item.get("synonyms", [])
            if not isinstance(synonyms, list):
                synonyms = []
            synonyms = [str(s).strip() for s in synonyms if str(s).strip()]
            keywords.append({"term": term, "synonyms": synonyms})
            seen_terms.add(term.lower())

        target = str(payload.get("target", "any")).strip().lower()
        if target not in allowed_targets:
            target = "any"

        raw_filters = payload.get("filters", [])
        if not isinstance(raw_filters, list):
            raw_filters = []

        filters = []
        seen_concepts = set()
        for item in raw_filters:
            if not isinstance(item, dict):
                continue
            concept = self._norm_ws(str(item.get("concept", "")).strip())
            if not concept or concept.lower() in seen_concepts:
                continue
            relation = str(item.get("relation", "related_to")).strip().lower()
            if relation not in allowed_relations:
                relation = "related_to"
            filters.append({"concept": concept, "relation": relation})
            seen_concepts.add(concept.lower())

        exclude = payload.get("exclude", [])
        if not isinstance(exclude, list):
            exclude = []
        exclude = [str(e).strip() for e in exclude if str(e).strip()]

        return {
            "keywords": keywords[:self.config.top_n],
            "target": target,
            "filters": filters[:8],
            "exclude": exclude,
            "raw_query": query,
        }

    @staticmethod
    def _norm_ws(text: str) -> str:
        return re.sub(r"\s+", " ", text.strip())
