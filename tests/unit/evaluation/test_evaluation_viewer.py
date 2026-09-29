"""Offline evaluation reports use the same observable page as live runs."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from urllib.request import urlopen

import pytest


def _write_report(
    path: Path,
    cases: list[dict[str, object]],
    *,
    trace_coverage: list[dict[str, object]] | None = None,
) -> None:
    path.write_text(
        json.dumps(
            {
                "benchmark": "queries.jsonl",
                "config": {"routes": ["lexical", "planned"]},
                "summary": {"lexical": {"queries": 1, "completed": 1}},
                "trace_coverage": trace_coverage or [],
                "cases": cases,
            }
        ),
        encoding="utf-8",
    )


def test_current_report_keeps_query_results_and_route_order(tmp_path: Path) -> None:
    from evaluation.evaluation_viewer import read_evaluation_report

    path = tmp_path / "codesense-evaluation-920.json"
    evaluation = {
        "query": "Find PageRequest files",
        "answer": [{"file": "src/PageRequest.java", "functions": []}],
        "routes": {"lexical": {"hits": [{"name": "PageRequest.java"}]}},
    }
    _write_report(path, [{"repo": "example/repo", "evaluation": evaluation}])

    snapshot = read_evaluation_report(path)

    assert snapshot["meta"] == {"benchmark": "queries.jsonl", "routes": ["lexical", "planned"]}
    assert snapshot["records"] == [{"key": "1", "repo": "example/repo", "evaluation": evaluation}]
    assert snapshot["summary"] == {
        "routes": {"lexical": {"queries": 1, "completed": 1}},
        "trace_coverage": [],
    }


def test_current_report_keeps_trace_coverage_for_the_final_summary(tmp_path: Path) -> None:
    from evaluation.evaluation_viewer import read_evaluation_report

    path = tmp_path / "codesense-evaluation.json"
    coverage = [
        {
            "trajectory_id": "trace-1",
            "routes": {"lexical": {"file_recall": 0.5}},
        }
    ]
    _write_report(path, [], trace_coverage=coverage)

    snapshot = read_evaluation_report(path)

    assert snapshot["summary"]["trace_coverage"] == coverage


def test_legacy_report_expands_each_search_into_its_own_card(tmp_path: Path) -> None:
    from evaluation.evaluation_viewer import read_evaluation_report

    path = tmp_path / "codesense-evaluation-920.json"
    searches = [
        {
            "query": "first",
            "answers": [{"file": "src/One.java"}],
            "event_indices": [2],
            "routes": {},
        },
        {
            "query": "second",
            "answers": [{"file": "src/Two.java"}],
            "event_indices": [5],
            "routes": {},
        },
    ]
    _write_report(path, [{"repo": "example/repo", "instance_id": "issue-1", "searches": searches}])

    records = read_evaluation_report(path)["records"]

    assert records == [
        {
            "key": "1:1",
            "repo": "example/repo",
            "instance_id": "issue-1",
            "evaluation": {
                "query": "first",
                "answer": [{"file": "src/One.java"}],
                "source_event_indices": [2],
                "routes": {},
            },
        },
        {
            "key": "1:2",
            "repo": "example/repo",
            "instance_id": "issue-1",
            "evaluation": {
                "query": "second",
                "answer": [{"file": "src/Two.java"}],
                "source_event_indices": [5],
                "routes": {},
            },
        },
    ]


def test_offline_viewer_serves_report_through_existing_page(tmp_path: Path) -> None:
    from evaluation.evaluation_viewer import start_evaluation_viewer

    path = tmp_path / "codesense-evaluation-920.json"
    _write_report(path, [{"repo": "example/repo", "evaluation": {"query": "find files"}}])

    viewer = start_evaluation_viewer(path, port=0)
    try:
        with urlopen(f"{viewer.url}/api/snapshot", timeout=2) as response:  # noqa: S310
            snapshot = json.load(response)
        with urlopen(viewer.url, timeout=2) as response:  # noqa: S310
            page = response.read().decode("utf-8")

        assert snapshot["records"][0]["evaluation"]["query"] == "find files"
        assert snapshot["summary"]["routes"]["lexical"]["completed"] == 1
        assert "CodeSense Live Evaluation" in page
        assert "Query answer diff" in page
    finally:
        viewer.close()


def test_invalid_report_is_rejected_before_opening_the_viewer(tmp_path: Path) -> None:
    from evaluation.evaluation_viewer import read_evaluation_report

    path = tmp_path / "codesense-evaluation-920.json"
    path.write_text('{"cases": {}}', encoding="utf-8")

    with pytest.raises(ValueError, match="cases"):
        read_evaluation_report(path)


def test_direct_script_imports_its_sibling_viewer_without_repo_on_python_path(
    tmp_path: Path,
) -> None:
    script = Path(__file__).resolve().parents[3] / "evaluation" / "evaluation_viewer.py"
    subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            "import runpy, sys; sys.path.insert(0, sys.argv[1]); "
            "runpy.run_path(sys.argv[2], run_name='offline_probe')",
            str(script.parent),
            str(script),
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
