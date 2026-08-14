from evaluation.models import PreparedQuery, TraceEvent


def test_trace_event_serializes_all_event_fields() -> None:
    event = TraceEvent(
        index=2,
        role="assistant",
        text="search for buffer watermark",
        tool_name="rg",
        tool_input="rg watermark src/main/java",
        tool_output="src/main/java/Watermark.java",
    )

    assert event.to_dict() == {
        "index": 2,
        "role": "assistant",
        "text": "search for buffer watermark",
        "tool_name": "rg",
        "tool_input": "rg watermark src/main/java",
        "tool_output": "src/main/java/Watermark.java",
    }


def test_prepared_query_serializes_source_events_and_provenance() -> None:
    event = TraceEvent(0, "assistant", "rg watermark", "rg", "rg watermark", None)
    query = PreparedQuery(
        query_id="trace-1:0",
        repo="acme/project",
        instance_id="issue-1",
        trajectory_id="trace-1",
        episode_index=0,
        query="Find code that applies backpressure when a buffer fills",
        strategy="generated",
        anchor_event=0,
        raw_action="rg watermark src/main/java",
        source_events=(event,),
        provenance={"prompt_version": "trace-query-v1", "model": "qwen-plus"},
    )

    payload = query.to_dict()

    assert payload["query_id"] == "trace-1:0"
    assert payload["strategy"] == "generated"
    assert payload["source_events"] == [event.to_dict()]
    assert payload["provenance"] == {
        "prompt_version": "trace-query-v1",
        "model": "qwen-plus",
    }
