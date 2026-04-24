"""
Step A + Step B keyword expansion pipeline for CodeSearch.

Step A: initial retrieval from local indexes (symbols + ngram inverted index).
Step B: LLM-based query expansion using query/base_keywords/seed_hits context.

This module intentionally stops before second retrieval/reranking.
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from definition import BASE_URL,BASE_MODEL,API_KEY
from openai import OpenAI
from parsers.read_tools import get_code_by_line_range



@dataclass
class ExpansionConfig:
    symbols_index_path: str
    ngram_index_path: str
    seed_top_k: int = 30
    expansion_top_n: int = 12
    model: str = "gpt-4o-mini"
    temperature: float = 0.0


class KeywordExpander:
    def __init__(self, config: ExpansionConfig, client: Any = None) -> None:
        self.config = config
        self.client = client
        self._symbols = self._load_json(config.symbols_index_path, default=[])
        self._ngram = self._load_json(config.ngram_index_path, default={})

    def search_and_expansion(
        self,
        query: str,
        base_keywords: List[Dict[str, float]],
    ) -> Dict[str, Any]:
        """
        Returns:
        {
          "query": ...,
          "base_keywords": [...],
          "seed_hits": [...],
          "expanded_keywords": [...],
          "constraints": [...]
        }
        """
        seed_hits = self.initial_retrieve(query, base_keywords, top_k=self.config.seed_top_k)
        llm_out = self.expand_with_llm(query, base_keywords, seed_hits)
        return {
            "query": query,
            "base_keywords": base_keywords,
            "seed_hits": seed_hits,
            "expanded_keywords": llm_out.get("expanded_keywords", []),
            "constraints": llm_out.get("constraints", []),
        }

    # ---------- Step A ----------
    def initial_retrieve(
        self,
        query: str,
        base_keywords: List[Dict[str, float]],
        top_k: int = 30,
    ) -> List[Dict[str, Any]]:
        """
        Lightweight initial retrieval using:
        - ngram inverted index token -> symbol names
        - symbols_index entries for metadata enrichment
        """
        symbols_by_name = self._build_symbol_lookup(self._symbols)

        # Collect candidate names from base keywords and query tokens.
        tokens = self._collect_query_tokens(query, base_keywords)
        name_scores: Dict[str, float] = {}

        for token, token_score in tokens:
            matched_names = self._ngram.get(token, [])
            if not isinstance(matched_names, list):
                continue
            for name in matched_names:
                prev = name_scores.get(name, 0.0)
                name_scores[name] = prev + token_score

        # Sort by rough lexical score and attach symbol info.
        ranked_names = sorted(name_scores.items(), key=lambda x: x[1], reverse=True)[: top_k * 3]

        hits: List[Dict[str, Any]] = []
        for name, rough_score in ranked_names:
            entries = symbols_by_name.get(name, [])
            for e in entries:
                hits.append(
                    {
                        "name": e.get("name", ""),
                        "type": e.get("type", ""),
                        "file": e.get("file", ""),
                        "container": e.get("container", ""),
                        "signature": e.get("signature", ""),
                        "doc": (e.get("doc", "") or "")[:200],
                        "match_score": round(float(rough_score), 4),
                    }
                )
                if len(hits) >= top_k:
                    return hits

        return hits[:top_k]

    # ---------- Step B ----------
    def expand_with_llm(
        self,
        query: str,
        base_keywords: List[Dict[str, float]],
        seed_hits: List[Dict[str, Any]],
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        Ask LLM to produce expansion candidates + constraints.
        """
        prompt = self._build_expansion_prompt(query, base_keywords, seed_hits)

        if self.client is None:
            self.client = OpenAI(api_key=API_KEY,base_url=BASE_URL)

        resp = self.client.responses.create(
            model=self.config.model,
            temperature=self.config.temperature,
            input=[
                {
                    "role": "system",
                    "content": "You are a code-search keyword expansion assistant. Return strict JSON only.",
                },
                {"role": "user", "content": prompt},
            ],
        )
        raw = getattr(resp, "output_text", "") or ""
        parsed = self._parse_expansion_json(raw)
        validated = self._validate_expansion_payload(query, parsed)
        return validated

    def _build_expansion_prompt(
        self,
        query: str,
        base_keywords: List[Dict[str, float]],
        seed_hits: List[Dict[str, Any]],
    ) -> str:
        seed_hits_compact = seed_hits[: min(len(seed_hits), 20)]
        return f"""
Task:
Given query + base keywords + initial retrieval hits, generate expansion terms for code search.

Input:
- query: {json.dumps(query, ensure_ascii=False)}
- base_keywords: {json.dumps(base_keywords, ensure_ascii=False)}
- seed_hits: {json.dumps(seed_hits_compact, ensure_ascii=False)}

Rules:
1) Expansion terms should be synonyms/aliases/abbreviations/domain terms related to base keywords.
2) Prefer terms likely to exist in the repository context shown by seed_hits.
3) Avoid generic words unless strongly supported by context.
4) Keep terms concise (1-3 words).
5) Provide confidence in [0,1].
6) Do not output duplicates.

Output JSON only:
{{
  "expanded_keywords": [
    {{
      "source": "base keyword",
      "text": "candidate term",
      "type": "synonym|alias|abbreviation|domain_term|related_api",
      "confidence": 0.0
    }}
  ]
}}

Keep at most {self.config.expansion_top_n} expanded keywords and 8 constraints.
""".strip()

    def _parse_expansion_json(self, text: str) -> Dict[str, Any]:
        if not text.strip():
            return {"expanded_keywords": []}
        try:
            obj = json.loads(text)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass

        # Tolerate fenced/noisy output by extracting outermost JSON object.
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                obj = json.loads(text[start : end + 1])
                if isinstance(obj, dict):
                    return obj
            except Exception:
                pass

        return {"expanded_keywords": []}

    def _validate_expansion_payload(self, query: str, payload: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
        expanded = payload.get("expanded_keywords", [])

        if not isinstance(expanded, list):
            expanded = []

        # Build vocab from repository evidence for light filtering.
        repo_vocab = set(self._ngram.keys())

        allowed_exp_types = {"synonym", "alias", "abbreviation", "domain_term", "related_api"}

        clean_exp: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for item in expanded:
            if not isinstance(item, dict):
                continue
            source = str(item.get("source", "")).strip()
            text = self._norm_ws(str(item.get("text", "")).strip().lower())
            e_type = str(item.get("type", "domain_term")).strip().lower()
            confidence = self._clamp01(item.get("confidence", 0.0))

            if not text:
                continue
            if e_type not in allowed_exp_types:
                e_type = "domain_term"

            # Prefer terms appearing in project vocab, but keep high-confidence out-of-vocab.
            in_vocab = text in repo_vocab
            if (not in_vocab) and confidence < 0.75:
                continue

            key = (source, text)
            prev = clean_exp.get(key)
            record = {
                "source": source,
                "text": text,
                "type": e_type,
                "confidence": round(confidence, 4),
            }
            if prev is None or record["confidence"] > prev["confidence"]:
                clean_exp[key] = record

        exp_out = sorted(clean_exp.values(), key=lambda x: x["confidence"], reverse=True)[: self.config.expansion_top_n]
        return {"expanded_keywords": exp_out}

    # ---------- helpers ----------
    def _collect_query_tokens(
            self,
            query: str,
            base_keywords: List[Dict[str, float]],
    ) -> List[Tuple[str, float]]:
        """
        Collect retrieval tokens from extracted base_keywords only.

        Rationale:
        Step A is a quick seed retrieval stage. Using full-query fallback tokens
        introduces noisy terms (e.g., function/that/in) and can hurt seed quality.
        """
        tokens: Dict[str, float] = {}

        for k in base_keywords:
            text = self._norm_ws(str(k.get("keyword", "")).strip().lower())
            score = self._clamp01(k.get("score", 0.5))
            if not text:
                continue

            for t in text.split():
                # Keep the strongest weight for duplicated tokens.
                tokens[t] = max(tokens.get(t, 0.0), score)

        return sorted(tokens.items(), key=lambda x: x[1], reverse=True)

    @staticmethod
    def _build_symbol_lookup(symbols: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
        out: Dict[str, List[Dict[str, Any]]] = {}
        for s in symbols:
            name = s.get("name", "")
            if not isinstance(name, str) or not name:
                continue
            out.setdefault(name, []).append(s)
        return out

    @staticmethod
    def _load_json(path: str, default: Any) -> Any:
        p = Path(path)
        if not p.exists():
            return default
        try:
            with p.open("r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default

    @staticmethod
    def _clamp01(v: Any) -> float:
        try:
            x = float(v)
        except Exception:
            x = 0.0
        return max(0.0, min(1.0, x))

    @staticmethod
    def _norm_ws(text: str) -> str:
        return " ".join(text.split())
