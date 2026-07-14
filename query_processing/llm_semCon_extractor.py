"""
LLM-based SemCon extraction for CodeSearch queries.

SemCon is the atomic condition layer used by SemQL. This extractor returns one
object with surface / intention / relation condition lists.
"""

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, Optional

from definition import BASE_MODEL
from DSL.surface_con import surface_condition
from DSL.intention_con import intention_condition
from DSL.relation_con import relation_condition
from parsers.code_element_types import get_common_code_element_types
from utils.llm_api import call_chat_llm


@dataclass
class LLMSemConExtractorConfig:
    model: str = BASE_MODEL
    temperature: float = 0.0


class LLMSemConExtractor:
    """Extract surface / intention / relation SemCon objects via LLM."""

    def __init__(
        self,
        config: Optional[LLMSemConExtractorConfig] = None,
        client: Any = None,
    ) -> None:
        self.config = config or LLMSemConExtractorConfig()
        self.client = client
        self.schemas = {
            "surface": surface_condition,
            "intention": intention_condition,
            "relation": relation_condition,
        }

    def extract_semCon(self, query: str) -> Dict[str, Any]:
        query = (query or "").strip()
        if not query:
            return self._empty_semCon()

        raw_text = self._call_llm(query)
        parsed = self._parse_json(raw_text)
        self._fill_unknown_code_element_type(parsed)
        return self._normalize_semCon(parsed)

    def _call_llm(self, query: str) -> str:
        prompt = self._build_prompt(query)
        prompt_messages: Any = [
            {
                "role": "system",
                "content": "You extract SemCon conditions for code search and return strict JSON only.",
            },
            {"role": "user", "content": prompt},
        ]
        return call_chat_llm(
            messages=prompt_messages,
            model=self.config.model,
            temperature=self.config.temperature,
            client=self.client,
        )

    def _build_prompt(self, query: str) -> str:
        schemas = json.dumps(self.schemas, indent=2, ensure_ascii=False)
        return f"""
Convert the query into SemCon atomic conditions.

## Condition schemas
Use the following imported schemas as the exact output format for each condition item:

{schemas}

## Required JSON shape

{{
  "surface": [/* surface_condition items */],
  "intention": [/* intention_condition items */],
  "relation": [/* relation_condition items */]
}}

## Strict rules

1. Output valid JSON only.
2. The top-level object must contain exactly these keys: surface, intention, relation.
3. Each top-level value must be a list.
4. Every list item must strictly follow its corresponding schema above.
5. Do not add fields outside the corresponding schema.
6. If a category has no conditions, use an empty list.
7. Surface group_logic rules:
   - group_logic only describes graph-aware AND among include keyword groups; never put exclude groups in it.
   - Put groups in group_logic when their matched code elements can jointly form one relevant result if connected within hop_count in the code graph.
   - graph_scope="call" uses call-chain distance; graph_scope="import" uses file/import distance, with same-file as hop_count=0.
   - Use pairwise_hop_counts only when a specific include group pair needs a non-default hop_count.
8. We will use surface for literal/code-text matching, intention for behavior/intent/domain meaning, and relation for code structure constraints.
9. CRITICAL — match_kind atomicity: Each surface condition must have exactly ONE match_kind value (code_element / code_snippet / code_line / unknown). Do NOT combine multiple match_kinds into a single surface condition. If the query involves matching more than one kind (e.g., both a code element name and a code line), split them into separate surface conditions, each with its own match_kind and corresponding keyword_groups.

## Query
{query}
        """.strip()

    def _parse_json(self, text: str) -> Dict[str, Any]:
        if not text or not text.strip():
            return self._empty_semCon()

        text = text.strip()
        try:
            obj = json.loads(text)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass

        m = re.search(r"\{[\s\S]*\}", text)
        if m:
            try:
                obj = json.loads(m.group(0))
                if isinstance(obj, dict):
                    return obj
            except Exception:
                pass

        return self._empty_semCon()

    def _normalize_semCon(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        result = self._empty_semCon()
        if not isinstance(payload, dict):
            return result

        for condition_type, schema in self.schemas.items():
            raw_items = payload.get(condition_type, [])
            if not isinstance(raw_items, list):
                continue

            allowed_fields = set(schema.keys())
            normalized_items = []
            for item in raw_items:
                if not isinstance(item, dict):
                    continue
                normalized = {k: v for k, v in item.items() if k in allowed_fields}
                normalized["type"] = condition_type
                normalized_items.append(normalized)

            result[condition_type] = normalized_items

        return result

    @staticmethod
    def _fill_unknown_code_element_type(payload: Dict[str, Any]) -> None:
        surface_items = payload.get("surface", [])
        if not isinstance(surface_items, list):
            return

        all_types = get_common_code_element_types()
        for item in surface_items:
            if not isinstance(item, dict):
                continue
            match_kind = str(item.get("match_kind", "")).strip().lower()
            if match_kind == "unknown":
                item["code_element_type"] = all_types

    @staticmethod
    def _empty_semCon() -> Dict[str, Any]:
        return {
            "surface": [],
            "intention": [],
            "relation": [],
        }
