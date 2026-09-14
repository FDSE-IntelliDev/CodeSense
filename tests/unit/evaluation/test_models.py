from evaluation.models import CodeLocation, PreparedQuery, SearchQuery, TraceCase, TraceEvent


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


def test_trace_case_serializes_patch_gold() -> None:
    case = TraceCase(
        "owner/repo",
        "java",
        "issue-1",
        "trace-1",
        "Fix navigation state.",
        None,
        (),
        {},
        (CodeLocation("src/main/java/Navigation.java", ("afterCursor",)),),
        None,
    )

    assert case.to_dict()["answer"] == [
        {"file": "src/main/java/Navigation.java", "functions": ["afterCursor"]}
    ]


def test_prepared_query_serializes_source_events_and_provenance() -> None:
    event = TraceEvent(0, "assistant", "rg watermark", "rg", "rg watermark", None)
    query = PreparedQuery(
        query_id="trace-1:0",
        repo="acme/project",
        instance_id="issue-1",
        trajectory_id="trace-1",
        searches=(
            SearchQuery(
                event_indices=(0, 1),
                query="Find code that applies backpressure when a buffer fills",
                answers=(
                    CodeLocation(
                        file="src/main/java/Buffer.java",
                        functions=("Buffer#write",),
                    ),
                ),
            ),
        ),
        final_answer=(
            CodeLocation(
                file="src/main/java/Buffer.java",
                functions=("Buffer#write",),
            ),
        ),
        strategy="generated",
        source_events=(event,),
        provenance={"prompt_version": "trace-query-v1", "model": "qwen-plus"},
    )

    payload = query.to_dict()

    assert payload["query_id"] == "trace-1:0"
    assert payload["strategy"] == "generated"
    assert payload["searches"] == [
        {
            "event_indices": [0, 1],
            "query": "Find code that applies backpressure when a buffer fills",
            "answers": [
                {
                    "file": "src/main/java/Buffer.java",
                    "functions": ["Buffer#write"],
                }
            ],
        }
    ]
    assert payload["final_answer"] == [
        {
            "file": "src/main/java/Buffer.java",
            "functions": ["Buffer#write"],
        }
    ]
    assert payload["source_events"] == [event.to_dict()]
    assert payload["provenance"] == {
        "prompt_version": "trace-query-v1",
        "model": "qwen-plus",
    }
