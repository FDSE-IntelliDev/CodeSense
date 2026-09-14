"""Behavior tests for the live evaluation result viewer."""

from __future__ import annotations

import http.client
import json
from queue import Empty
from urllib.parse import urlsplit
from urllib.request import urlopen

import pytest

from evaluation.live_results import LiveEvaluationViewer, LiveResultStore, viewer_page


def test_store_snapshots_records_and_streams_later_queries() -> None:
    store = LiveResultStore({"routes": ["lexical", "planned"]})
    store.publish({"key": "1", "evaluation": {"query": "first", "routes": {}}})

    subscriber, snapshot = store.subscribe()

    assert snapshot == {
        "meta": {"routes": ["lexical", "planned"]},
        "records": [{"key": "1", "evaluation": {"query": "first", "routes": {}}}],
        "summary": None,
    }
    store.publish({"key": "2", "evaluation": {"query": "second", "routes": {}}})
    assert subscriber.get_nowait() == (
        "query",
        {"key": "2", "evaluation": {"query": "second", "routes": {}}},
    )


def test_store_copies_inputs_and_snapshots() -> None:
    store = LiveResultStore({"routes": ["lexical"]})
    record = {"key": "1", "evaluation": {"query": "safe", "answer": [{"file": "A.java"}]}}

    store.publish(record)
    record["evaluation"] = {}
    snapshot = store.snapshot()
    snapshot["records"].clear()

    assert store.snapshot()["records"] == [
        {"key": "1", "evaluation": {"query": "safe", "answer": [{"file": "A.java"}]}}
    ]


def test_store_finishes_closes_and_unsubscribes_without_blocking() -> None:
    store = LiveResultStore()
    subscriber, _ = store.subscribe()

    store.finish({"lexical": {"completed": 2}})
    assert subscriber.get_nowait() == ("summary", {"lexical": {"completed": 2}})

    store.unsubscribe(subscriber)
    store.publish({"key": "1:1"})
    with pytest.raises(Empty):
        subscriber.get_nowait()

    closing, _ = store.subscribe()
    store.close()
    assert closing.get_nowait() == ("close", {})


def test_viewer_page_defines_live_diff_and_safe_dom_contract() -> None:
    page = viewer_page()

    assert "CodeSense Live Evaluation" in page
    assert "new EventSource('/events')" in page
    assert all(status in page for status in ("matched", "missed", "extra"))
    assert all(metric in page for metric in ("file_precision", "file_recall", "function_recall"))
    assert "matched_files" in page and "matched_functions" in page
    assert "matchedFunctions.has(hitPairKey(hit))" in page
    assert "record.evaluation" in page
    assert "evaluation.answer" in page
    assert "record.search" not in page
    assert "search.answers" not in page
    assert ".textContent" in page
    assert ".innerHTML" not in page


def test_viewer_serves_the_page_and_current_snapshot() -> None:
    viewer = LiveEvaluationViewer.start(port=0, meta={"routes": ["lexical"]})
    viewer.publish({"key": "1", "evaluation": {"query": "find retry"}})
    try:
        with urlopen(viewer.url, timeout=2) as response:  # noqa: S310 -- loopback test server
            assert response.headers.get_content_type() == "text/html"
            assert b"CodeSense Live Evaluation" in response.read()
        with urlopen(  # noqa: S310 -- loopback test server
            f"{viewer.url}/api/snapshot", timeout=2
        ) as response:
            snapshot = json.load(response)
            assert response.headers.get_content_type() == "application/json"
        assert snapshot == {
            "meta": {"routes": ["lexical"]},
            "records": [{"key": "1", "evaluation": {"query": "find retry"}}],
            "summary": None,
        }
    finally:
        viewer.close()


def test_sse_sends_an_atomic_snapshot_then_new_queries() -> None:
    viewer = LiveEvaluationViewer.start(port=0, meta={"routes": ["lexical"]})
    viewer.publish({"key": "1", "evaluation": {"query": "first"}})
    parsed = urlsplit(viewer.url)
    connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=2)
    try:
        connection.request("GET", "/events")
        response = connection.getresponse()
        assert response.status == 200
        assert response.headers.get_content_type() == "text/event-stream"
        assert _read_sse_event(response) == (
            "snapshot",
            {
                "meta": {"routes": ["lexical"]},
                "records": [{"key": "1", "evaluation": {"query": "first"}}],
                "summary": None,
            },
        )

        viewer.publish({"key": "2", "evaluation": {"query": "second"}})

        assert _read_sse_event(response) == (
            "query",
            {"key": "2", "evaluation": {"query": "second"}},
        )
    finally:
        connection.close()
        viewer.close()


def test_viewer_close_is_idempotent() -> None:
    viewer = LiveEvaluationViewer.start(port=0)

    viewer.close()
    viewer.close()


def _read_sse_event(response: http.client.HTTPResponse) -> tuple[str, dict[str, object]]:
    event = ""
    data = ""
    while True:
        raw = response.readline()
        if not raw:
            raise AssertionError("SSE connection closed before a complete event")
        line = raw.decode("utf-8").rstrip("\r\n")
        if not line:
            return event, json.loads(data)
        if line.startswith("event: "):
            event = line.removeprefix("event: ")
        elif line.startswith("data: "):
            data = line.removeprefix("data: ")
