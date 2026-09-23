from evaluation.models import CodeLocation, ToolCall, TraceCase, TraceEvent
from evaluation.trace_search import (
    CandidateLocation,
    SearchEpisode,
    find_search_episodes,
    parse_episode_candidates,
    supervise_search_episodes,
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


def test_find_search_episodes_rejects_unidentified_search_mixed_with_other_call() -> None:
    events = (
        TraceEvent(
            index=0,
            role="assistant",
            text="",
            tool_calls=(
                ToolCall(None, "rg", "rg fifo src"),
                ToolCall(None, "view", '{"path":"src/Pool.java"}'),
            ),
        ),
        TraceEvent(index=1, role="tool", text="", tool_output="src/Pool.java"),
        TraceEvent(index=2, role="tool", text="", tool_output="class Pool {}"),
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


def _trace_case(events: tuple[TraceEvent, ...]) -> TraceCase:
    return TraceCase(
        repo="acme/project",
        language="java",
        instance_id="issue-1",
        trajectory_id="trace-1",
        issue_statement="Pool reuse order is inconsistent.",
        base_commit=None,
        events=events,
        raw={},
    )


def test_open_edit_and_followup_search_create_usage_evidence() -> None:
    events = (
        TraceEvent(0, "assistant", "", "rg", "rg pool src/main/java", None),
        TraceEvent(1, "tool", "", "rg", None, "src/main/java/Pool.java"),
        TraceEvent(
            2,
            "assistant",
            "",
            "str_replace_editor",
            '{"command":"view","path":"src/main/java/Pool.java"}',
            None,
        ),
        TraceEvent(3, "tool", "", "str_replace_editor", None, "class Pool {}"),
        TraceEvent(4, "assistant", "", "bash", '{"command":"rg Pool.java notes.txt"}', None),
        TraceEvent(5, "tool", "", "bash", None, "no matches"),
        TraceEvent(
            6,
            "assistant",
            "",
            "str_replace_editor",
            '{"command":"str_replace","path":"src/main/java/Pool.java"}',
            None,
        ),
    )

    episode = supervise_search_episodes(_trace_case(events)).episodes[0]

    assert [(item.file, item.kind) for item in episode.usage_evidence] == [
        ("src/main/java/Pool.java", "opened"),
        ("src/main/java/Pool.java", "searched"),
        ("src/main/java/Pool.java", "edited"),
    ]


def test_tool_output_alone_is_not_usage_evidence() -> None:
    events = (
        TraceEvent(0, "assistant", "", "rg", "rg pool src/main/java", None),
        TraceEvent(1, "tool", "", "rg", None, "src/main/java/Pool.java"),
        TraceEvent(2, "assistant", "The search completed."),
        TraceEvent(3, "tool", "", "unknown", None, "src/main/java/Pool.java"),
    )

    batch = supervise_search_episodes(_trace_case(events))

    assert batch.episodes == ()
    assert "no_usage_evidence" in batch.skip_reasons


def test_unsupported_tool_action_is_not_assistant_reference_evidence() -> None:
    events = (
        TraceEvent(0, "assistant", "", "rg", "rg pool src/main/java", None),
        TraceEvent(1, "tool", "", "rg", None, "src/main/java/Pool.java"),
        TraceEvent(
            2,
            "assistant",
            "",
            "unknown_tool",
            '{"path":"src/main/java/Pool.java"}',
            None,
        ),
    )

    batch = supervise_search_episodes(_trace_case(events))

    assert batch.episodes == ()


def test_basename_only_matches_when_unique() -> None:
    events = (
        TraceEvent(0, "assistant", "", "rg", "rg Client src/main/java", None),
        TraceEvent(
            1,
            "tool",
            "",
            "rg",
            None,
            "src/main/java/a/Client.java\nsrc/main/java/b/Client.java",
        ),
        TraceEvent(2, "assistant", "", "view", '{"path":"Client.java"}', None),
    )

    batch = supervise_search_episodes(_trace_case(events))

    assert batch.episodes == ()


def test_usage_is_assigned_to_the_most_recent_producer() -> None:
    events = (
        TraceEvent(2, "assistant", "", "rg", "rg pool src/main/java", None),
        TraceEvent(3, "tool", "", "rg", None, "src/main/java/Pool.java"),
        TraceEvent(8, "assistant", "", "rg", "rg pool src/main/java", None),
        TraceEvent(9, "tool", "", "rg", None, "src/main/java/Pool.java"),
        TraceEvent(
            10,
            "assistant",
            "",
            "str_replace_editor",
            '{"command":"view","path":"src/main/java/Pool.java"}',
            None,
        ),
    )

    batch = supervise_search_episodes(_trace_case(events))

    assert [episode.episode.anchor_event for episode in batch.episodes] == [8]


def test_complete_assistant_path_and_function_create_reference_evidence() -> None:
    events = (
        TraceEvent(0, "assistant", "", "rg", "rg select src/main/java", None),
        TraceEvent(
            1,
            "tool",
            "",
            "rg",
            None,
            "src/main/java/Pool.java:42: public void select() {",
        ),
        TraceEvent(
            2,
            "assistant",
            "src/main/java/Pool.java and select explain the ordering behavior.",
        ),
    )

    episode = supervise_search_episodes(_trace_case(events)).episodes[0]

    assert episode.answer == (CodeLocation("src/main/java/Pool.java", ("select",)),)
    assert episode.usage_evidence[0].kind == "referenced"


def test_read_range_promotes_only_the_declaration_inside_the_range() -> None:
    events = (
        TraceEvent(0, "assistant", "", "rg", "rg select src/main/java", None),
        TraceEvent(
            1,
            "tool",
            "",
            "rg",
            None,
            "src/main/java/Pool.java:42: public void select() {",
        ),
        TraceEvent(
            2,
            "assistant",
            "",
            "str_replace_editor",
            '{"command":"view","path":"src/main/java/Pool.java","view_range":[40,50]}',
            None,
        ),
    )

    episode = supervise_search_episodes(_trace_case(events)).episodes[0]

    assert episode.answer == (CodeLocation("src/main/java/Pool.java", ("select",)),)


def test_gold_files_are_removed_from_candidate_answers() -> None:
    events = (
        TraceEvent(0, "assistant", "", "rg", "rg pool src/main/java", None),
        TraceEvent(
            1,
            "tool",
            "",
            "rg",
            None,
            "src/main/java/Pool.java\nsrc/main/java/PoolConfig.java",
        ),
        TraceEvent(2, "assistant", "", "view", '{"path":"src/main/java/Pool.java"}', None),
    )

    episode = supervise_search_episodes(_trace_case(events)).episodes[0]

    assert episode.answer == (CodeLocation("src/main/java/Pool.java", ()),)
    assert episode.candidate_answers == (CodeLocation("src/main/java/PoolConfig.java", ()),)


def test_exact_duplicate_episodes_keep_the_later_search() -> None:
    events = (
        TraceEvent(0, "assistant", "", "rg", "rg pool src/main/java", None),
        TraceEvent(1, "tool", "", "rg", None, "src/main/java/Pool.java"),
        TraceEvent(2, "assistant", "", "view", '{"path":"src/main/java/Pool.java"}', None),
        TraceEvent(4, "assistant", "", "rg", "rg pool src/main/java", None),
        TraceEvent(5, "tool", "", "rg", None, "src/main/java/Pool.java"),
        TraceEvent(6, "assistant", "", "view", '{"path":"src/main/java/Pool.java"}', None),
    )

    batch = supervise_search_episodes(_trace_case(events))

    assert [episode.episode.anchor_event for episode in batch.episodes] == [4]
    assert "duplicate_episode" in batch.skip_reasons
