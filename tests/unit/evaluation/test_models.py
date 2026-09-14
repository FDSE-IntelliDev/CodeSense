from evaluation.models import CodeLocation, PreparedQuery, TraceCase, TraceEvent


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
        query_id="trace-1",
        repo="acme/project",
        instance_id="issue-1",
        trajectory_id="trace-1",
        issue_statement="Navigation mode can leave stale state.",
        query=(
            "Find the logic that can leave navigation state inconsistent when switching "
            "between cursor-based and page-based access."
        ),
        answer=(CodeLocation("src/main/java/Navigation.java", ("afterCursor",)),),
        source_event_indices=(0,),
        strategy="semantic-generated",
        source_events=(event,),
        provenance={"prompt_version": "semantic-query-v1", "query_reason": "behavioral"},
    )

    payload = query.to_dict()

    assert payload["query_id"] == "trace-1"
    assert payload["strategy"] == "semantic-generated"
    assert payload["issue_statement"] == "Navigation mode can leave stale state."
    assert payload["query"].startswith("Find the logic")
    assert payload["answer"] == [
        {"file": "src/main/java/Navigation.java", "functions": ["afterCursor"]}
    ]
    assert payload["source_event_indices"] == [0]
    assert payload["source_events"] == [event.to_dict()]
    assert payload["provenance"] == {
        "prompt_version": "semantic-query-v1",
        "query_reason": "behavioral",
    }
    assert "searches" not in payload
    assert "final_answer" not in payload
