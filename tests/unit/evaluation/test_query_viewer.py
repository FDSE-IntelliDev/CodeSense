from __future__ import annotations

import json
from pathlib import Path
from urllib.request import urlopen

import pytest

from evaluation.query_viewer import QueryFileViewer, query_viewer_page, read_query_records


def _record(query_id: str) -> dict[str, object]:
    return {
        "query_id": query_id,
        "repo": "example/repo",
        "instance_id": f"issue-{query_id}",
        "trajectory_id": f"trace-{query_id}",
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
        "source_event_indices": [1, 2, 3],
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
            "query_reason",
            "source_events",
        )
    )
    assert ".textContent" in page
    assert ".innerHTML" not in page


def test_query_file_viewer_rereads_the_file_for_each_api_request(tmp_path: Path) -> None:
    path = tmp_path / "queries.jsonl"
    _write_jsonl(path, [_record("one")])
    viewer = QueryFileViewer.start(path, port=0, refresh_seconds=0.1)
    try:
        with urlopen(f"{viewer.url}/api/cases", timeout=2) as response:  # noqa: S310
            first = json.load(response)
        _write_jsonl(path, [_record("one"), _record("two")])
        with urlopen(f"{viewer.url}/api/cases", timeout=2) as response:  # noqa: S310
            second = json.load(response)

        assert first["count"] == 1
        assert second["count"] == 2
        assert [record["query_id"] for record in second["records"]] == ["one", "two"]
    finally:
        viewer.close()
