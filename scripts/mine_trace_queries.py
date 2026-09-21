"""Mine reviewable CodeSense queries from Open-SWE trace JSONL records."""

from __future__ import annotations

import json
import sys
from collections import Counter
from contextlib import suppress
from pathlib import Path

# Edit these values before running this script. The API key is still read from
# the CODESENSE_API_KEY environment variable by LlmConfig.
INPUT = (
    "/Users/huangzhuochen/PycharmProjects/CodeSense/outputs/open_swe_traces/"
    "open_swe_java_sample.jsonl"
)
OUTPUT_DIR = "/Users/huangzhuochen/PycharmProjects/CodeSense/outputs/open_swe_traces"
OUTPUT = f"{OUTPUT_DIR}/codesense-semantic-query.jsonl"
LIMIT = 0
DRY_RUN = False
BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
MODEL = "qwen3.7-plus"
TIMEOUT = 300.0
PROMPT_VERSION = "semantic-query-v1"
# Keep the repository-local evaluation package importable when this file is
# launched as ``python scripts/mine_trace_queries.py``. The scripts directory
# contains evaluation.py, so the repository root must precede it even when an
# IDE has already added the root later in sys.path.
_ROOT = Path(__file__).resolve().parents[1]
with suppress(ValueError):
    sys.path.remove(str(_ROOT))
sys.path.insert(0, str(_ROOT))


def main() -> int:
    if not INPUT:
        raise ValueError("set INPUT to an Open-SWE trace JSONL path")
    if not OUTPUT:
        raise ValueError("set OUTPUT to a query or prompt JSONL path")

    input_path = Path(INPUT).expanduser()
    output_path = Path(OUTPUT).expanduser()
    from codesense.llm import LlmConfig
    from evaluation.query_mining import OpenAIQueryGenerator, mine_query
    from evaluation.trace_adapters.open_swe_traces import iter_jsonl

    generator = None
    if not DRY_RUN:
        generator = OpenAIQueryGenerator(
            LlmConfig.load(
                base_url=BASE_URL,
                model=MODEL,
                timeout=TIMEOUT,
            )
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    processed = 0
    written = 0
    skip_reasons: Counter[str] = Counter()
    with output_path.open("w", encoding="utf-8") as output:
        for case in iter_jsonl(input_path):
            if LIMIT and processed >= LIMIT:
                break
            if DRY_RUN:
                rows = _prompt_rows(case, PROMPT_VERSION)
                if not rows:
                    skip_reasons[case.gold_error or "no_search_events"] += 1
            else:
                outcome = mine_query(case, generator, prompt_version=PROMPT_VERSION)
                rows = [outcome.query.to_dict()] if outcome.query is not None else []
                if outcome.skip_reason:
                    skip_reasons[outcome.skip_reason] += 1
            for row in rows:
                output.write(json.dumps(row, ensure_ascii=False) + "\n")
                written += 1
            processed += 1
            if processed % 10 == 0:
                break
    print(
        json.dumps(
            {"processed": processed, "written": written, "skipped": skip_reasons},
            ensure_ascii=False,
        )
    )
    return 0


def _prompt_rows(case, prompt_version: str) -> list[dict[str, object]]:
    from evaluation.query_mining import build_prompt, is_search_event, search_context

    context = search_context(case.events)
    if case.gold_error or not case.answer or not context:
        return []
    return [
        {
            "type": "prompt",
            "repo": case.repo,
            "instance_id": case.instance_id,
            "trajectory_id": case.trajectory_id,
            "search_event_indices": [
                event.index for event in case.events if is_search_event(event)
            ],
            "source_event_indices": [event.index for event in context],
            "prompt": build_prompt(case, prompt_version=prompt_version),
        }
    ]


if __name__ == "__main__":
    raise SystemExit(main())
