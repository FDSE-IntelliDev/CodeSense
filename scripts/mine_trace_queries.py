"""Mine reviewable CodeSense queries from Open-SWE trace JSONL records."""

from __future__ import annotations

import json
import sys
from collections import Counter
from collections.abc import Iterable
from contextlib import suppress
from pathlib import Path

# Edit these values before running this script. The API key is still read from
# the CODESENSE_API_KEY environment variable by LlmConfig.
_ROOT = Path(__file__).resolve().parents[1]
INPUT = str(_ROOT / "outputs/open_swe_traces/open_swe_java_sample.jsonl")
OUTPUT_DIR = str(_ROOT / "outputs/open_swe_traces")
OUTPUT = f"{OUTPUT_DIR}/codesense-semantic-query.jsonl"
LIMIT = 0
DRY_RUN = False
BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
MODEL = "qwen3.7-plus"
TIMEOUT = 300.0
PROMPT_VERSION = "trace-search-v3"
# Keep the repository-local evaluation package importable when this file is
# launched as ``python scripts/mine_trace_queries.py``. The scripts directory
# contains evaluation.py, so the repository root must precede it even when an
# IDE has already added the root later in sys.path.
with suppress(ValueError):
    sys.path.remove(str(_ROOT))
sys.path.insert(0, str(_ROOT))

from evaluation.answer_cleaning import clean_record  # noqa: E402
from evaluation.models import TraceCase  # noqa: E402
from evaluation.query_mining import (  # noqa: E402
    QueryGenerator,
    build_prompt,
    mine_queries,
)
from evaluation.repo_cache import resolve_repo  # noqa: E402
from evaluation.trace_search import supervise_search_episodes  # noqa: E402


def main() -> int:
    if not INPUT:
        raise ValueError("set INPUT to an Open-SWE trace JSONL path")
    if not OUTPUT:
        raise ValueError("set OUTPUT to a query or prompt JSONL path")

    input_path = Path(INPUT).expanduser()
    output_path = Path(OUTPUT).expanduser()
    from codesense.llm import LlmConfig
    from evaluation.query_mining import OpenAIQueryGenerator
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
    summary = _run_cases(
        iter_jsonl(input_path),
        generator,
        output_path,
        limit=LIMIT,
        prompt_version=PROMPT_VERSION,
        dry_run=DRY_RUN,
    )
    print(json.dumps(summary, ensure_ascii=False))
    return 0


def _prompt_rows(
    case: TraceCase, prompt_version: str
) -> tuple[list[dict[str, object]], int, int, tuple[str, ...]]:
    supervision = supervise_search_episodes(case)
    rows = [
        {
            "type": "prompt",
            "repo": case.repo,
            "trajectory_id": case.trajectory_id,
            "anchor_event": item.episode.anchor_event,
            "source_event_indices": [event.index for event in item.episode.context_events],
            "result_event_indices": [event.index for event in item.episode.result_events],
            "prompt": build_prompt(case, item, prompt_version=prompt_version),
        }
        for item in supervision.episodes
    ]
    return (
        rows,
        supervision.search_episode_count,
        len(supervision.episodes),
        supervision.skip_reasons,
    )


def _run_cases(
    cases: Iterable[TraceCase],
    generator: QueryGenerator | None,
    output_path: Path,
    *,
    limit: int,
    prompt_version: str,
    dry_run: bool,
) -> dict[str, object]:
    processed = written = search_episodes = eligible_episodes = 0
    repo_roots: dict[tuple[str, str], Path] = {}
    skip_reasons: Counter[str] = Counter()
    with output_path.open("w", encoding="utf-8") as output:
        for case in cases:
            if limit and processed >= limit:
                break
            if dry_run:
                rows, search_count, eligible_count, reasons = _prompt_rows(case, prompt_version)
            else:
                if generator is None:
                    raise ValueError("generator is required unless DRY_RUN is true")
                batch = mine_queries(case, generator, prompt_version=prompt_version)
                rows = [query.to_dict() for query in batch.queries]
                rows = _clean_rows(rows, repo_roots, skip_reasons)
                search_count = batch.search_episode_count
                eligible_count = batch.eligible_episode_count
                reasons = batch.skip_reasons
            for row in rows:
                output.write(json.dumps(row, ensure_ascii=False) + "\n")
            skip_reasons.update(reasons)
            processed += 1
            written += len(rows)
            search_episodes += search_count
            eligible_episodes += eligible_count
    return {
        "processed_traces": processed,
        "search_episodes": search_episodes,
        "eligible_episodes": eligible_episodes,
        "written_queries": written,
        "skipped": dict(skip_reasons),
    }


def _clean_rows(
    rows: list[dict[str, object]],
    repo_roots: dict[tuple[str, str], Path],
    skip_reasons: Counter[str],
) -> list[dict[str, object]]:
    """Annotate answer existence in mined rows, resolving each repo once.

    A row without a repo/base_commit cannot be pinned to a revision, so it is
    written through untouched. A resolve failure degrades to "no annotation"
    for that repository rather than aborting the whole mining run.
    """
    cleaned: list[dict[str, object]] = []
    for row in rows:
        repo = str(row.get("repo") or "")
        commit = row.get("base_commit")
        if not repo or not commit:
            cleaned.append(row)
            continue
        key = (repo, str(commit))
        root = repo_roots.get(key)
        if root is None:
            try:
                root = resolve_repo(repo, str(commit))
            except Exception as exc:  # noqa: BLE001 -- one repo must not stop mining
                skip_reasons[f"repo_resolve_failed:{type(exc).__name__}"] += 1
                cleaned.append(row)
                continue
            repo_roots[key] = root
        cleaned.append(clean_record(row, root))
    return cleaned


if __name__ == "__main__":
    raise SystemExit(main())
