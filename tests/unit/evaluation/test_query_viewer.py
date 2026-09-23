from __future__ import annotations

import json
from pathlib import Path
from urllib.request import urlopen

import pytest

from evaluation.query_viewer import (
    QueryFileViewer,
    group_query_records,
    query_viewer_page,
    read_query_records,
)


def _record(
    query_id: str,
    *,
    instance_id: str | None = None,
    trajectory_id: str | None = None,
) -> dict[str, object]:
    return {
        "query_id": query_id,
        "repo": "example/repo",
        "instance_id": instance_id or f"issue-{query_id}",
        "trajectory_id": trajectory_id or f"trace-{query_id}",
        "issue_statement": "Navigation can retain stale state after changing access mode.",
        "query": (
            "Find the logic that can leave navigation state inconsistent when switching "
            "between cursor-based and page-based access."
        ),
        "answer": [
            {
                "file": "src/main/java/example/Navigation.java",
                "functions": ["afterCursor"],
            }
        ],
        "trace_answer": [
            {
                "file": "src/main/java/example/Navigation.java",
                "functions": ["afterCursor"],
            }
        ],
        "candidate_answers": [{"file": "src/main/java/example/PageState.java", "functions": []}],
        "usage_evidence": [
            {
                "file": "src/main/java/example/Navigation.java",
                "functions": ["afterCursor"],
                "event_index": 4,
                "kind": "opened",
            }
        ],
        "source_event_indices": [1, 2],
        "result_event_indices": [3],
        "provenance": {"query_reason": "It describes a state transition failure."},
        "source_events": [
            {"index": 1, "role": "assistant", "text": "I will inspect navigation state."},
            {
                "index": 2,
                "role": "assistant",
                "tool_name": "rg",
                "tool_input": "rg cursor src/main/java",
            },
            {
                "index": 3,
                "role": "tool",
                "tool_output": "src/main/java/example/Navigation.java",
            },
        ],
    }


def _write_jsonl(path: Path, records: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )


def test_read_query_records_reads_the_current_jsonl_contents(tmp_path: Path) -> None:
    path = tmp_path / "queries.jsonl"
    _write_jsonl(path, [_record("one")])

    assert [record["query_id"] for record in read_query_records(path)] == ["one"]

    _write_jsonl(path, [_record("one"), _record("two")])

    assert [record["query_id"] for record in read_query_records(path)] == ["one", "two"]


def test_read_query_records_reports_the_invalid_line(tmp_path: Path) -> None:
    path = tmp_path / "queries.jsonl"
    path.write_text(json.dumps(_record("one")) + "\nnot-json\n", encoding="utf-8")

    with pytest.raises(ValueError, match="invalid JSON on line 2"):
        read_query_records(path)


def test_group_query_records_nests_queries_by_trace_in_input_order() -> None:
    records = [
        _record("trace-a:2", instance_id="issue-a", trajectory_id="trace-a"),
        _record("trace-b:3", instance_id="issue-b", trajectory_id="trace-b"),
        _record("trace-a:7", instance_id="issue-a", trajectory_id="trace-a"),
    ]

    traces = group_query_records(records)

    assert [trace["trajectory_id"] for trace in traces] == ["trace-a", "trace-b"]
    assert [query["query_id"] for query in traces[0]["queries"]] == [
        "trace-a:2",
        "trace-a:7",
    ]
    assert traces[0]["trace_answer"] == records[0]["trace_answer"]


def test_group_query_records_does_not_merge_rows_without_trace_identity() -> None:
    first = _record("query-one")
    second = _record("query-two")
    first.pop("trajectory_id")
    second.pop("trajectory_id")

    traces = group_query_records([first, second])

    assert [trace["queries"][0]["query_id"] for trace in traces] == [
        "query-one",
        "query-two",
    ]


def test_query_viewer_page_polls_json_api_and_uses_safe_dom_rendering() -> None:
    page = query_viewer_page(refresh_seconds=2.0)

    assert "CodeSense Query Viewer" in page
    assert "fetch('/api/cases'" in page
    assert "setInterval(loadCases, 2000)" in page
    assert all(
        field in page
        for field in (
            "issue_statement",
            "source_event_indices",
            "result_event_indices",
            "query_reason",
            "source_events",
            "Gold answers",
            "Candidate answers",
            "Usage evidence",
            "Trace answer",
            "Original trace",
            "Query-related trace events",
            "traceCard",
            "queryCard",
            "trace-nav",
        )
    )
    assert ".textContent" in page
    assert ".innerHTML" not in page


def test_query_file_viewer_rereads_the_file_for_each_api_request(tmp_path: Path) -> None:
    path = tmp_path / "queries.jsonl"
    first_record = _record("trace-one:2", instance_id="issue-one", trajectory_id="trace-one")
    second_record = _record("trace-one:7", instance_id="issue-one", trajectory_id="trace-one")
    _write_jsonl(path, [first_record])
    viewer = QueryFileViewer.start(path, port=0, refresh_seconds=0.1)
    try:
        with urlopen(f"{viewer.url}/api/cases", timeout=2) as response:  # noqa: S310
            first = json.load(response)
        _write_jsonl(path, [first_record, second_record])
        with urlopen(f"{viewer.url}/api/cases", timeout=2) as response:  # noqa: S310
            second = json.load(response)

        assert first["trace_count"] == 1
        assert first["query_count"] == 1
        assert second["trace_count"] == 1
        assert second["query_count"] == 2
        assert [query["query_id"] for query in second["traces"][0]["queries"]] == [
            "trace-one:2",
            "trace-one:7",
        ]
    finally:
        viewer.close()
