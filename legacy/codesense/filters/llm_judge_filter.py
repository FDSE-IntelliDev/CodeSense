"""
LLM-as-a-Judge intention filter.

This module is the final high-precision semantic filter for intention
conditions. The executor sends only gray-zone candidates and the normalized
include/exclude requirements produced by IntentionPlanner.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

from codesense.config import load_config
from codesense.parsers.read_tools import get_symbol_code
from codesense.utils.file_utils import load_res, save_res
from codesense.utils.llm_api import call_chat_llm


JudgeCaller = Callable[[List[Dict[str, str]], str, float], str]


@dataclass
class LLMJudgeConfig:
    #: None 表示「用配置里的默认模型」。不要写成 ``model: str = BASE_MODEL``——
    #: 默认参数在 import 时求值，会让 import 本模块顺带读一次 YAML。
    model: Optional[str] = None
    temperature: float = 0.0

    def resolved_model(self) -> str:
        return self.model or load_config().llm.model
    batch_size: int = 5
    max_code_chars: int = 3000


class LLMJudgeFilter:
    """Filter candidates by SemQL intention conditions with an LLM judge."""

    def __init__(
        self,
        config: Optional[LLMJudgeConfig] = None,
        client: Any = None,
        caller: Optional[JudgeCaller] = None,
    ) -> None:
        self.config = config or LLMJudgeConfig()
        self.client = client
        self.caller = caller

    def run_filter(
        self,
        candidates: List[Dict[str, Any]],
        intention_plan: Dict[str, Any],
    ) -> Dict[str, Any]:
        if not candidates:
            return self._empty_result()

        requirements = extract_intention_requirements(intention_plan)
        result_logic = intention_plan.get("result_logic", {})
        if not requirements["include"] and not requirements["exclude"]:
            return {
                "kept": candidates,
                "discarded": [],
                "uncertain": [],
                "judgments": [],
                "stats": {
                    "total_initial": len(candidates),
                    "total_kept": len(candidates),
                    "total_discarded": 0,
                    "total_uncertain": 0,
                    "batch_count": 0,
                },
            }

        candidates_by_id = {
            int(candidate["symbol_id"]): candidate
            for candidate in candidates
            if candidate.get("symbol_id") is not None
        }
        kept_ids: Set[int] = set()
        discarded: List[Dict[str, Any]] = []
        uncertain: List[Dict[str, Any]] = []
        judgments: List[Dict[str, Any]] = []

        batches = list(_chunks(candidates, self.config.batch_size))
        for batch in batches:
            prompt_candidates = [
                build_candidate_payload(
                    candidate,
                    max_code_chars=self.config.max_code_chars,
                )
                for candidate in batch
                if candidate.get("symbol_id") is not None
            ]
            if not prompt_candidates:
                continue

            messages = build_judge_messages(
                requirements,
                prompt_candidates,
                result_logic,
            )
            raw_text = self._call_llm(messages)
            parsed = parse_judge_response(raw_text)
            batch_ids = {
                int(item["symbol_id"])
                for item in prompt_candidates
                if item.get("symbol_id") is not None
            }

            batch_kept = {
                int(symbol_id)
                for symbol_id in parsed.get("kept_symbol_ids", [])
                if _is_int_like(symbol_id) and int(symbol_id) in batch_ids
            }
            kept_ids.update(batch_kept)

            batch_discarded = _normalize_reason_items(
                parsed.get("discarded", []),
                valid_ids=batch_ids,
            )
            batch_uncertain = _normalize_reason_items(
                parsed.get("uncertain", []),
                valid_ids=batch_ids,
            )
            batch_discarded_ids = {
                item["symbol_id"] for item in batch_discarded
            }
            batch_uncertain = [
                item
                for item in batch_uncertain
                if item["symbol_id"] not in batch_discarded_ids
            ]

            explained_ids = batch_kept | {
                item["symbol_id"] for item in batch_discarded + batch_uncertain
            }
            for missing_id in sorted(batch_ids - explained_ids):
                batch_uncertain.append({
                    "symbol_id": missing_id,
                    "reason": "LLM response did not include a judgment for this candidate.",
                })

            discarded.extend(batch_discarded)
            uncertain.extend(batch_uncertain)
            judgments.append({
                "candidate_ids": sorted(batch_ids),
                "raw_response": raw_text,
                "parsed_response": parsed,
            })

        kept = [
            candidate
            for candidate in candidates
            if candidate.get("symbol_id") is not None
            and int(candidate["symbol_id"]) in kept_ids
        ]

        kept_set = {
            int(candidate["symbol_id"])
            for candidate in kept
            if candidate.get("symbol_id") is not None
        }
        discarded = [
            item for item in discarded
            if item["symbol_id"] not in kept_set
        ]
        uncertain = [
            item for item in uncertain
            if item["symbol_id"] not in kept_set
        ]

        return {
            "kept": kept,
            "discarded": discarded,
            "uncertain": uncertain,
            "judgments": judgments,
            "stats": {
                "total_initial": len(candidates_by_id),
                "total_kept": len(kept),
                "total_discarded": len(discarded),
                "total_uncertain": len(uncertain),
                "batch_count": len(batches),
            },
        }

    def _call_llm(self, messages: List[Dict[str, str]]) -> str:
        if self.caller is not None:
            return self.caller(
                messages, self.config.resolved_model(), self.config.temperature
            )
        return call_chat_llm(
            messages=messages,
            model=self.config.resolved_model(),
            temperature=self.config.temperature,
            client=self.client,
        )

    @staticmethod
    def _empty_result() -> Dict[str, Any]:
        return {
            "kept": [],
            "discarded": [],
            "uncertain": [],
            "judgments": [],
            "stats": {
                "total_initial": 0,
                "total_kept": 0,
                "total_discarded": 0,
                "total_uncertain": 0,
                "batch_count": 0,
            },
        }


def extract_intention_requirements(semql_query: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    """Read normalized requirements from an intention-only execution plan."""
    requirements = {"include": [], "exclude": []}
    if not isinstance(semql_query, dict):
        return requirements

    plan_requirements = semql_query.get("requirements")
    if not isinstance(plan_requirements, dict):
        return requirements
    for property_name in ("include", "exclude"):
        requirements[property_name] = _normalize_requirement_items(
            plan_requirements.get(property_name, [])
        )

    return requirements


def build_candidate_payload(
    symbol: Dict[str, Any],
    *,
    max_code_chars: int = 4000,
) -> Dict[str, Any]:
    """Build the compact symbol schema shown to the judge model."""
    code = get_symbol_code("", symbol) or ""
    if max_code_chars > 0 and len(code) > max_code_chars:
        code = code[:max_code_chars] + "\n/* ... code truncated ... */"

    return {
        "symbol_id": symbol.get("symbol_id"),
        "name": symbol.get("name"),
        "type": symbol.get("type"),
        "file": symbol.get("file"),
        "signature": symbol.get("signature"),
        "language": symbol.get("language"),
        "doc": symbol.get("doc"),
        "container": symbol.get("container"),
        "code": code,
    }


def build_judge_messages(
    requirements: Dict[str, List[Dict[str, Any]]],
    candidate_set: List[Dict[str, Any]],
    result_logic: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, str]]:
    result_logic = result_logic if isinstance(result_logic, dict) else {}
    include_operator = str(result_logic.get("include_operator") or "all")
    exclude_operator = str(result_logic.get("exclude_operator") or "any")
    system_prompt = (
        "You are an LLM-as-a-Judge module in a code search pipeline.\n\n"
        "Your task is to judge whether each candidate code symbol satisfies the "
        "user's semantic intention.\n\n"
        "The candidate_set contains only gray-zone candidates that earlier static "
        "semantic filters could not decide confidently.\n\n"
        "You will receive:\n"
        "1. Include requirements: code symbols must satisfy these.\n"
        "2. Exclude requirements: code symbols must NOT satisfy these.\n"
        "3. A candidate_set: code symbols represented with a fixed symbol schema.\n\n"
        "Rules:\n"
        f"- Include requirement operator is {include_operator}.\n"
        f"- Exclude requirement operator is {exclude_operator}.\n"
        "- When include requirements are empty, do not reject candidates for missing include evidence.\n"
        "- When exclude requirements are empty, ignore exclude matching.\n"
        "- Judge the candidate's own responsibility, not merely related code around it.\n"
        "- Do not keep a candidate only because it shares keywords with the query.\n"
        "- Do not infer behavior that is not supported by the candidate schema.\n"
        "- If evidence is insufficient, mark it as uncertain rather than keeping it.\n"
        "- Return strict JSON only."
    )
    user_prompt = f"""
Task:
Select the symbol_id values from candidate_set that satisfy the semantic intention requirements.

Include requirements:
{json.dumps(requirements.get("include", []), ensure_ascii=False, indent=2)}

Exclude requirements:
{json.dumps(requirements.get("exclude", []), ensure_ascii=False, indent=2)}

Each requirement has:
- clause_id: stable requirement id
- intent.action / intent.object: normalized behavior and target entity
- intent_statement: a yes/no semantic requirement
- aspect: functional | non_functional | domain
- non_functional_type: optional quality category
- keywords: normalized intention terms
- description: a short explanation of the requirement

Candidate symbol schema:
{{
  "symbol_id": integer,
  "name": string,
  "type": string,
  "file": string,
  "signature": string | null,
  "language": string | null,
  "doc": string | null,
  "container": string | null,
  "code": string | null
}}

candidate_set:
{json.dumps(candidate_set, ensure_ascii=False, indent=2)}

Output JSON schema:
{{
  "kept_symbol_ids": [integer],
  "discarded": [
    {{
      "symbol_id": integer,
      "reason": string
    }}
  ],
  "uncertain": [
    {{
      "symbol_id": integer,
      "reason": string
    }}
  ]
}}
    """.strip()
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def parse_judge_response(text: str) -> Dict[str, Any]:
    """Parse strict JSON, with a code-fence/object fallback for common LLM wrappers."""
    if not text or not text.strip():
        return {"kept_symbol_ids": [], "discarded": [], "uncertain": []}

    raw = text.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return _normalize_judge_payload(parsed)
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{[\s\S]*\}", raw)
    if match:
        try:
            parsed = json.loads(match.group(0))
            if isinstance(parsed, dict):
                return _normalize_judge_payload(parsed)
        except Exception:
            pass

    return {"kept_symbol_ids": [], "discarded": [], "uncertain": []}


def run_llm_judge_filter(
    candidates: List[Dict[str, Any]],
    intention_plan: Dict[str, Any],
    *,
    config: Optional[LLMJudgeConfig] = None,
    client: Any = None,
    caller: Optional[JudgeCaller] = None,
) -> Dict[str, Any]:
    return LLMJudgeFilter(config=config, client=client, caller=caller).run_filter(
        candidates,
        intention_plan,
    )


def llm_judge_filter(
    intention_plan_path: str,
    candidate_path: str,
    output_path: str,
    *,
    judge_output_path: Optional[str] = None,
    batch_size: int = 5,
    max_code_chars: int = 3000,
    model: Optional[str] = None,
    client: Any = None,
    caller: Optional[JudgeCaller] = None,
) -> List[Dict[str, Any]]:
    intention_plan = load_res(intention_plan_path)
    candidates = load_res(candidate_path)
    if not isinstance(intention_plan, dict):
        raise ValueError(
            f"Intention plan must be a JSON object: {intention_plan_path}"
        )
    if not isinstance(candidates, list):
        raise ValueError(f"Candidate set must be a JSON array: {candidate_path}")

    result = run_llm_judge_filter(
        candidates,
        intention_plan,
        config=LLMJudgeConfig(
            model=model,
            batch_size=batch_size,
            max_code_chars=max_code_chars,
        ),
        client=client,
        caller=caller,
    )

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    save_res(output_path, result["kept"])

    if judge_output_path:
        Path(judge_output_path).parent.mkdir(parents=True, exist_ok=True)
        save_res(judge_output_path, {
            key: value
            for key, value in result.items()
            if key != "kept"
        })

    return result["kept"]


def _normalize_requirement_items(items: Any) -> List[Dict[str, Any]]:
    if not isinstance(items, list):
        return []
    normalized = []
    for item in items:
        if not isinstance(item, dict):
            continue
        req = _normalize_requirement_item(item)
        if req is not None:
            normalized.append(req)
    return normalized


def _normalize_requirement_item(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    intent = item.get("intent")
    if not isinstance(intent, dict):
        intent = {}
    req = {
        "clause_id": item.get("clause_id"),
        "intent": {
            "action": intent.get("action"),
            "object": intent.get("object"),
        },
        "intent_statement": item.get("intent_statement"),
        "aspect": item.get("aspect"),
        "non_functional_type": item.get("non_functional_type"),
        "keywords": item.get("keywords", [])
        if isinstance(item.get("keywords", []), list)
        else [],
        "description": item.get("description"),
    }
    meaningful_values = [
        req["intent"]["action"],
        req["intent"]["object"],
        req["intent_statement"],
        req["aspect"],
        req["non_functional_type"],
        req["description"],
        *req["keywords"],
    ]
    if not any(meaningful_values):
        return None
    return req


def _normalize_judge_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    kept_symbol_ids = [
        int(symbol_id)
        for symbol_id in payload.get("kept_symbol_ids", [])
        if _is_int_like(symbol_id)
    ]
    return {
        "kept_symbol_ids": kept_symbol_ids,
        "discarded": payload.get("discarded", [])
        if isinstance(payload.get("discarded", []), list)
        else [],
        "uncertain": payload.get("uncertain", [])
        if isinstance(payload.get("uncertain", []), list)
        else [],
    }


def _normalize_reason_items(items: Any, *, valid_ids: Set[int]) -> List[Dict[str, Any]]:
    if not isinstance(items, list):
        return []
    normalized = []
    for item in items:
        if not isinstance(item, dict):
            continue
        symbol_id = item.get("symbol_id")
        if not _is_int_like(symbol_id):
            continue
        symbol_id = int(symbol_id)
        if symbol_id not in valid_ids:
            continue
        normalized.append({
            "symbol_id": symbol_id,
            "reason": str(item.get("reason") or "").strip(),
        })
    return normalized


def _chunks(items: List[Dict[str, Any]], size: int) -> Iterable[List[Dict[str, Any]]]:
    size = max(1, int(size or 1))
    for start in range(0, len(items), size):
        yield items[start:start + size]


def _is_int_like(value: Any) -> bool:
    try:
        int(value)
        return True
    except (TypeError, ValueError):
        return False


def main() -> None:
    # 配置在入口取，不在模块顶层取。
    from codesense.config import load_config

    QUERY_OUTPUT_DIR = str(load_config().query_output_dir())
    kept = llm_judge_filter(
        intention_plan_path=f"{QUERY_OUTPUT_DIR}/intention_semql.json",
        candidate_path=f"{QUERY_OUTPUT_DIR}/filtered_by_embedding.json",
        output_path=f"{QUERY_OUTPUT_DIR}/LLM_judge_result.json",
        judge_output_path=f"{QUERY_OUTPUT_DIR}/LLM_judge_result_debug.json",
        batch_size=5,
        max_code_chars=3000,
    )
    print(json.dumps({"kept": len(kept), "output_path": f"{QUERY_OUTPUT_DIR}/LLM_judge_result.json"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
