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
    assert all(
        status in page
        for status in (
            "matched",
            "missed",
            "candidate",
            "extra",
            "gold_hit",
            "trace_answer_hit",
            "candidate_hit",
            "unlabeled_hit",
        )
    )
    assert all(
        metric in page
        for metric in (
            "observed_file_precision",
            "file_recall",
            "first_gold_rank",
            "mrr",
            "function_recall",
        )
    )
    assert "matched_files" in page and "matched_functions" in page
    assert "query_plus_trace_answer" in page
    assert "trace_answer_coverage" in page
    assert "evaluation.trace_answer" in page
    assert "Trace answer diff" in page
    assert "record.evaluation" in page
    assert "evaluation.answer" in page
    assert "evaluation.candidate_answers" in page
    assert "record.search" not in page
    assert "search.answers" not in page
    assert ".textContent" in page
    assert ".innerHTML" not in page


def test_viewer_page_explains_the_hit_and_diff_color_legend() -> None:
    page = viewer_page()

    # A persistent legend at the top of the page, above the result cards.
    assert 'id="legend"' in page
    # Search hits: each result is colored by which kind of answer it matched.
    assert "Search hits：每条搜索结果按其命中的答案类型着色" in page
    assert "绿色 · gold_hit：命中该 query 的主标准答案（answer 字段）" in page
    assert "紫色 · trace_answer_hit：命中 trace 的最终答案（加分项，不属于主答案）" in page
    assert "黄色 · candidate_hit：命中候选答案（candidate_answers，弱相关加分项）" in page
    assert "灰色 · extra：搜到但不属于任何答案，即多余结果" in page
    # Answer diff: only the gold answers, marked found (green) / missed (red).
    assert "Answer diff：只展示 gold_answer，逐条标注是否被搜到" in page
    assert "绿色 · matched：这条 gold 被搜索命中" in page
    assert "红色 · missed：这条 gold 没被搜到（漏召回）" in page


def test_viewer_page_explains_the_three_answer_types() -> None:
    page = viewer_page()

    # The legend also defines the three answer kinds, each tied to the same
    # color the search-hits list uses for it.
    assert "答案类型：gold / trace_answer / candidate 分别是什么" in page
    assert 'class="row matched">gold_answer（answer）：这条 query 自身的检索答案' in page
    assert 'class="row trace-answer">trace_answer：整个 issue 修复任务的最终答案' in page
    assert 'class="row candidate">candidate_answer：' in page
    assert "同一次搜索返回、但后续未观察到被使用的弱标注候选" in page


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
