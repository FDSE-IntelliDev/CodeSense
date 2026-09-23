import json

import pytest

from evaluation.models import TraceCase, TraceEvent
from evaluation.query_mining import (
    MiningBatch,
    build_prompt,
    contains_shell_or_java_path,
    is_pure_direct_lookup,
    is_search_event,
    mine_queries,
)
from evaluation.trace_search import supervise_search_episodes


class _Generator:
    model = "test-model"

    def __init__(self, *answers: str | None) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> str | None:
        self.prompts.append(prompt)
        return self.answers.pop(0) if self.answers else None


def _valid_response(query: str) -> str:
    return json.dumps(
        {
            "status": "valid",
            "query": query,
            "reason": "The anchor is combined with its behavioral effect.",
            "anchor_terms": ["isPoolLifo"],
            "semantic_constraints": ["changes reusable connection selection"],
        }
    )


def _case(*, two_searches: bool = False) -> TraceCase:
    events = [
        TraceEvent(0, "user", "Pool reuse order can change request behavior."),
        TraceEvent(1, "assistant", "I will inspect pool ordering."),
        TraceEvent(2, "assistant", "", "rg", "rg isPoolLifo src/main/java", None),
        TraceEvent(
            3,
            "tool",
            "",
            "rg",
            None,
            "src/main/java/Pool.java\nsrc/main/java/PoolConfig.java",
        ),
        TraceEvent(
            4,
            "assistant",
            "I will read Pool.java now.",
            "str_replace_editor",
            '{"command":"view","path":"src/main/java/Pool.java"}',
            None,
        ),
        TraceEvent(5, "tool", "", "str_replace_editor", None, "class Pool {}"),
    ]
    if two_searches:
        events.extend(
            [
                TraceEvent(6, "assistant", "I will inspect buffer pressure."),
                TraceEvent(7, "assistant", "", "rg", "rg watermark src/main/java", None),
                TraceEvent(8, "tool", "", "rg", None, "src/main/java/Buffer.java"),
                TraceEvent(
                    9,
                    "assistant",
                    "",
                    "str_replace_editor",
                    '{"command":"view","path":"src/main/java/Buffer.java"}',
                    None,
                ),
            ]
        )
    return TraceCase(
        "acme/project",
        "java",
        "issue-1",
        "trace-1",
        "Pool reuse order can change request behavior.",
        None,
        tuple(events),
        {"metadata": {"reference_patch": {"patch": "SECRET_PATCH"}}},
    )


def test_json_bash_command_with_find_is_a_search_event() -> None:
    event = TraceEvent(
        0,
        "assistant",
        "",
        "bash",
        '{"command": "find /workspace/project -name \\"*.java\\" | xargs '
        'grep -l \\"failonwarnings\\""}',
        None,
    )
    quoted = TraceEvent(
        1,
        "assistant",
        "",
        "bash",
        '\'{"command": "find /workspace/project -name \\"*.java\\""}\'',
        None,
    )

    assert is_search_event(event)
    assert is_search_event(quoted)


def test_prompt_contains_issue_reasoning_action_but_hides_result_and_future() -> None:
    case = _case()
    episode = supervise_search_episodes(case).episodes[0]

    prompt = build_prompt(case, episode, prompt_version="trace-search-v2")

    assert case.issue_statement in prompt
    assert "isPoolLifo" in prompt
    assert "I will inspect pool ordering" in prompt
    assert "src/main/java/Pool.java" not in prompt
    assert "I will read Pool.java now." not in prompt
    assert "SECRET_PATCH" not in prompt
    assert "ZephyrQueue" in prompt
    assert "ObservatorySession" in prompt
    assert "Find where isPoolLifo changes reusable connection selection." not in prompt
    assert "Find Java files that reference PageRequest." not in prompt


def test_mine_queries_returns_one_row_per_supervised_episode() -> None:
    response = _valid_response("Find code whose policy changes runtime behavior.")
    generator = _Generator(response, response)

    batch = mine_queries(_case(two_searches=True), generator)

    assert [query.query_id for query in batch.queries] == ["trace-1:2", "trace-1:7"]
    assert batch.search_episode_count == 2
    assert batch.eligible_episode_count == 2
    assert len(generator.prompts) == 2


def test_mined_query_contains_episode_supervision_and_provenance() -> None:
    batch = mine_queries(
        _case(),
        _Generator(_valid_response("Find where isPoolLifo changes reusable connection selection.")),
    )

    query = batch.queries[0]
    assert [location.file for location in query.answer] == ["src/main/java/Pool.java"]
    assert [location.file for location in query.candidate_answers] == [
        "src/main/java/PoolConfig.java"
    ]
    assert query.anchor_terms == ("isPoolLifo",)
    assert query.semantic_constraints == ("changes reusable connection selection",)
    assert query.source_event_indices == (1, 2)
    assert query.result_event_indices == (3,)
    assert query.strategy == "trace-search-generated"
    assert query.provenance["model"] == "test-model"
    assert query.provenance["prompt_version"] == "trace-search-v3"
    assert query.provenance["candidate_count"] == 1


def test_exact_anchor_with_behavior_passes_but_missing_constraint_fails() -> None:
    accepted = mine_queries(
        _case(),
        _Generator(_valid_response("Find where isPoolLifo changes reusable connection selection.")),
    )
    rejected = mine_queries(
        _case(),
        _Generator(
            json.dumps(
                {
                    "status": "valid",
                    "query": "Locate calls to isPoolLifo.",
                    "reason": "Direct lookup.",
                    "anchor_terms": ["isPoolLifo"],
                    "semantic_constraints": [],
                }
            )
        ),
    )

    assert len(accepted.queries) == 1
    assert rejected.queries == ()
    assert rejected.skip_reasons == ("missing_semantic_constraint",)


def test_direct_lookup_with_claimed_constraint_is_still_rejected() -> None:
    response = json.dumps(
        {
            "status": "valid",
            "query": "Locate calls to isPoolLifo.",
            "reason": "Direct lookup.",
            "anchor_terms": ["isPoolLifo"],
            "semantic_constraints": ["changes connection selection"],
        }
    )

    batch = mine_queries(_case(), _Generator(response))

    assert batch.queries == ()
    assert batch.skip_reasons == ("direct_lookup_query",)


@pytest.mark.parametrize(
    ("query", "shell_or_path", "direct"),
    [
        ("rg isPoolLifo src/main/java", True, False),
        ("Search for Pool.java", True, False),
        ("Find Java files that reference PageRequest.", False, True),
        ("Find where isPoolLifo changes reusable connection selection.", False, False),
    ],
)
def test_semantic_gate_classifiers(query: str, shell_or_path: bool, direct: bool) -> None:
    assert contains_shell_or_java_path(query) is shell_or_path
    assert is_pure_direct_lookup(query) is direct


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        (None, "generator_failed"),
        ("not valid JSON", "invalid_model_json"),
        ('{"status":"invalid trace"}', "non_semantic_query"),
    ],
)
def test_mine_queries_isolates_generation_failures_without_retry(
    response: str | None, reason: str
) -> None:
    generator = _Generator(response)

    batch = mine_queries(_case(), generator)

    assert batch == MiningBatch((), (reason,), 1, 1)
    assert len(generator.prompts) == 1


def test_mine_queries_skips_trace_without_search_supervision_before_model_call() -> None:
    case = _case()
    no_search = TraceCase(
        case.repo,
        case.language,
        case.instance_id,
        case.trajectory_id,
        case.issue_statement,
        case.base_commit,
        case.events[:2],
        case.raw,
    )
    generator = _Generator("unused")

    batch = mine_queries(no_search, generator)

    assert batch.queries == ()
    assert batch.search_episode_count == 0
    assert batch.eligible_episode_count == 0
    assert generator.prompts == []
