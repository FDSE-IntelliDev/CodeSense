"""Mine reviewable CodeSense queries from Open-SWE trace JSONL records."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

# Keep the repository-local evaluation package importable when this file is
# launched as ``python scripts/mine_trace_queries.py``.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    from codesense.llm import LlmConfig
    from evaluation.query_mining import OpenAIQueryGenerator, mine_queries
    from evaluation.trace_adapters.open_swe_traces import iter_jsonl

    generator = None
    if not args.dry_run:
        generator = OpenAIQueryGenerator(
            LlmConfig.load(
                base_url=args.base_url,
                model=args.model,
                timeout=args.timeout,
            )
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    processed = 0
    with args.output.open("w", encoding="utf-8") as output:
        for case in iter_jsonl(args.input):
            if args.limit and processed >= args.limit:
                break
            if args.dry_run:
                rows = _prompt_rows(case, args.prompt_version)
            else:
                rows = [
                    query.to_dict()
                    for query in mine_queries(case, generator, prompt_version=args.prompt_version)
                ]
            for row in rows:
                output.write(json.dumps(row, ensure_ascii=False) + "\n")
            processed += 1
    return 0


def _prompt_rows(case, prompt_version: str) -> list[dict[str, object]]:
    from evaluation.query_mining import build_prompt, find_episodes

    rows = []
    for episode_index, episode in enumerate(find_episodes(case)):
        rows.append(
            {
                "type": "prompt",
                "repo": case.repo,
                "instance_id": case.instance_id,
                "trajectory_id": case.trajectory_id,
                "episode_index": episode_index,
                "anchor_event": episode[0].index,
                "raw_action": episode[0].tool_input or episode[0].text,
                "prompt": build_prompt(case, episode, prompt_version=prompt_version),
            }
        )
    return rows


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="Open-SWE trace JSONL")
    parser.add_argument("--output", type=Path, required=True, help="query or prompt JSONL")
    parser.add_argument(
        "--limit", type=int, default=0, help="maximum records to process; 0 means all"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="write prefix-only prompts without LLM calls"
    )
    from codesense.llm import DEFAULT_BASE_URL, DEFAULT_MODEL

    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--prompt-version", default="trace-query-v1")
    args = parser.parse_args(argv)
    if args.limit < 0:
        parser.error("--limit must be non-negative")
    return args


if __name__ == "__main__":
    raise SystemExit(main())
