from evaluation.models import ToolCall, TraceEvent
from evaluation.trace_search import (
    CandidateLocation,
    SearchEpisode,
    find_search_episodes,
    parse_episode_candidates,
)


def test_find_search_episodes_uses_call_id_before_position() -> None:
    events = (
        TraceEvent(index=0, role="user", text="Fix pool order."),
        TraceEvent(index=1, role="assistant", text="I will inspect both pool paths."),
        TraceEvent(
            index=2,
            role="assistant",
            text="",
            tool_calls=(
                ToolCall("search-a", "rg", "rg fifo src"),
                ToolCall("search-b", "find", "find src -name '*.java'"),
            ),
        ),
        TraceEvent(
            index=3,
            role="tool",
            text="",
            tool_output="src/A.java",
            tool_call_id="search-a",
        ),
        TraceEvent(
            index=4,
            role="tool",
            text="",
            tool_output="src/B.java",
            tool_call_id="search-b",
        ),
    )

    found = find_search_episodes(events)

    assert [
        (episode.tool_call.id, [event.index for event in episode.result_events])
        for episode in found.episodes
    ] == [("search-a", [3]), ("search-b", [4])]
    assert [event.index for event in found.episodes[0].context_events] == [1, 2]


def test_find_search_episodes_rejects_ambiguous_unidentified_multiple_calls() -> None:
    events = (
        TraceEvent(
            index=0,
            role="assistant",
            text="",
            tool_calls=(
                ToolCall(None, "rg", "rg fifo src"),
                ToolCall(None, "rg", "rg lifo src"),
            ),
        ),
        TraceEvent(index=1, role="tool", text="", tool_output="src/Pool.java"),
    )

    found = find_search_episodes(events)

    assert found.episodes == ()
    assert found.skip_reasons == ("ambiguous_tool_result",)


def test_find_search_episodes_falls_back_to_contiguous_tool_result() -> None:
    events = (
        TraceEvent(index=0, role="assistant", text="I will inspect pool order."),
        TraceEvent(index=1, role="assistant", text="", tool_name="rg", tool_input="rg pool src"),
        TraceEvent(index=2, role="tool", text="", tool_output="src/Pool.java"),
        TraceEvent(index=3, role="assistant", text="Now I will read it."),
    )

    found = find_search_episodes(events)

    assert len(found.episodes) == 1
    assert [event.index for event in found.episodes[0].result_events] == [2]
    assert [event.index for event in found.episodes[0].context_events] == [0, 1]


def _episode_with_output(output: str) -> SearchEpisode:
    call = ToolCall("search-a", "rg", "rg pool src/main/java")
    action = TraceEvent(index=2, role="assistant", text="", tool_calls=(call,))
    result = TraceEvent(
        index=3,
        role="tool",
        text="",
        tool_output=output,
        tool_call_id="search-a",
    )
    return SearchEpisode(2, action, call, (action,), (result,), call.arguments or "")


def test_parse_candidates_normalizes_workspace_paths_and_declarations() -> None:
    raw = (
        "/workspace/open-feature__java-sdk__1.0/src/main/java/dev/Client.java:42: "
        "public Evaluation getDoubleValue(String key) {"
    )

    found = parse_episode_candidates(_episode_with_output(raw), "open-feature/java-sdk")

    assert found == (
        CandidateLocation("src/main/java/dev/Client.java", ("getDoubleValue",), 42, raw),
    )


def test_parse_candidates_excludes_tests_and_does_not_infer_call_names() -> None:
    raw = (
        "src/test/java/dev/ClientTest.java\n"
        "src/main/java/dev/Client.java:80: provider.getDoubleValue(key);"
    )

    found = parse_episode_candidates(_episode_with_output(raw), "open-feature/java-sdk")

    assert found == (
        CandidateLocation(
            "src/main/java/dev/Client.java",
            (),
            80,
            "src/main/java/dev/Client.java:80: provider.getDoubleValue(key);",
        ),
    )
