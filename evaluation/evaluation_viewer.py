"""Serve a saved CodeSense evaluation report without running searches."""

from __future__ import annotations

import json
import threading
from collections.abc import Mapping
from pathlib import Path

if __package__:
    from .live_results import LiveEvaluationViewer
else:
    # Direct ``python evaluation/evaluation_viewer.py`` puts this directory,
    # not the repository root, on sys.path.
    from live_results import LiveEvaluationViewer

__all__ = ["INPUT", "PORT", "read_evaluation_report", "start_evaluation_viewer"]

# Edit this path when reviewing another completed evaluation run.
INPUT = (
    Path(__file__).resolve().parents[1]
    / "outputs"
    / "open_swe_traces"
    / "codesense-evaluation.json"
)
PORT = 8767


def read_evaluation_report(path: Path) -> dict[str, object]:
    """Load either saved report shape into the live page's snapshot format."""
    raw = json.loads(path.expanduser().read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise ValueError("evaluation report must contain a JSON object")
    cases = raw.get("cases")
    if not isinstance(cases, list):
        raise ValueError("evaluation report cases must be an array")

    config = raw.get("config")
    summary = raw.get("summary")
    trace_coverage = raw.get("trace_coverage")
    meta = {
        "benchmark": raw.get("benchmark", ""),
        "routes": config.get("routes", []) if isinstance(config, Mapping) else [],
    }
    records: list[dict[str, object]] = []
    for case_number, case in enumerate(cases, 1):
        if not isinstance(case, Mapping):
            raise ValueError(f"evaluation report case {case_number} must be an object")
        identity = {
            key: case[key]
            for key in ("query_id", "repo", "instance_id", "trajectory_id")
            if key in case
        }
        evaluation = case.get("evaluation")
        if isinstance(evaluation, Mapping):
            records.append({"key": str(case_number), **identity, "evaluation": dict(evaluation)})
            continue

        # Older reports placed several searches under one case. The page's
        # contract is one card per query, so flatten only this legacy shape.
        searches = case.get("searches")
        if not isinstance(searches, list):
            raise ValueError(f"evaluation report case {case_number} has no query results")
        for search_number, search in enumerate(searches, 1):
            if not isinstance(search, Mapping):
                raise ValueError(
                    f"evaluation report search {case_number}:{search_number} must be an object"
                )
            converted = dict(search)
            converted["answer"] = converted.pop("answers", [])
            converted["source_event_indices"] = converted.pop("event_indices", [])
            records.append(
                {
                    "key": f"{case_number}:{search_number}",
                    **identity,
                    "evaluation": converted,
                }
            )

    return {
        "meta": meta,
        "records": records,
        "summary": {
            "routes": dict(summary) if isinstance(summary, Mapping) else {},
            "trace_coverage": list(trace_coverage) if isinstance(trace_coverage, list) else [],
        },
    }


def start_evaluation_viewer(path: Path, *, port: int = PORT) -> LiveEvaluationViewer:
    """Start the existing read-only page, preloaded from one saved report."""
    snapshot = read_evaluation_report(path)
    viewer = LiveEvaluationViewer.start(port=port, meta=snapshot["meta"])
    try:
        for record in snapshot["records"]:
            viewer.publish(record)
        viewer.finish(snapshot["summary"])
    except Exception:
        viewer.close()
        raise
    return viewer


def main() -> int:
    """Keep the report page available until Ctrl+C."""
    viewer = start_evaluation_viewer(INPUT, port=PORT)
    print(f"evaluation viewer: {viewer.url}")
    print(f"source: {INPUT}")
    print("press Ctrl+C to exit")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        viewer.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
