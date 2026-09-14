import json

import pytest

from evaluation.models import CodeLocation, TraceCase, TraceEvent
from evaluation.query_mining import (
    MiningOutcome,
    build_prompt,
    is_search_event,
    mine_query,
    search_context,
)


def _case() -> TraceCase:
    events = (
        TraceEvent(0, "user", "Fix navigation mode state."),
        TraceEvent(1, "assistant", "I will inspect navigation transitions."),
        TraceEvent(2, "assistant", "", "rg", "rg cursor src/main/java", None),
        TraceEvent(3, "tool", "", "rg", None, "src/main/java/example/Navigation.java"),
        TraceEvent(4, "assistant", "I will compare page-based state."),
        TraceEvent(5, "assistant", "", "rg", "rg page src/main/java", None),
        TraceEvent(6, "tool", "", "rg", None, "src/main/java/example/PageState.java"),
        TraceEvent(7, "assistant", "Updated navigation state handling."),
    )
    return TraceCase(
        repo="acme/project",
        language="java",
        instance_id="issue-1",
        trajectory_id="trace-1",
        issue_statement=(
            "Switching between cursor-based and page-based navigation can retain stale state."
        ),
        base_commit="abc123",
        events=events,
        raw={
            "metadata": {
                "reference_patch": {"patch": "SECRET_PATCH src/main/java/example/Navigation.java"}
            }
        },
        answer=(CodeLocation("src/main/java/example/Navigation.java", ("afterCursor",)),),
        gold_error=None,
    )


class _Generator:
    model = "test-model"

    def __init__(self, answer: str | None) -> None:
        self.answer = answer
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> str | None:
        self.prompts.append(prompt)
        return self.answer


def _valid_response(query: str) -> str:
    return json.dumps(
        {
            "status": "valid",
            "query": query,
            "reason": "It describes a behavioral failure without exposing code identifiers.",
        }
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


def test_search_context_keeps_neighbors_and_excludes_user_events() -> None:
    context = search_context(_case().events)

    assert [event.index for event in context] == [1, 2, 3, 4, 5, 6]
    assert all(event.role in {"assistant", "tool"} for event in context)


def test_prompt_contains_full_issue_and_semantic_examples_but_not_patch() -> None:
    case = _case()

    prompt = build_prompt(case, prompt_version="semantic-query-v1")

    assert case.issue_statement in prompt
    assert "Find functions whose behavior can affect disk performance." in prompt
    assert "Find Java files that reference PageRequest." in prompt
    assert "Find the logic that can leave navigation state inconsistent" in prompt
    assert "rg cursor src/main/java" in prompt
    assert "SECRET_PATCH" not in prompt
    assert "reference_patch" not in prompt


def test_mine_query_returns_one_semantic_query_with_patch_gold() -> None:
    generator = _Generator(
        _valid_response(
            "Find the logic that can leave navigation state inconsistent when switching "
            "between cursor-based and page-based access."
        )
    )

    outcome = mine_query(_case(), generator)

    assert outcome.skip_reason is None
    assert outcome.query is not None
    assert outcome.query.answer == _case().answer
    assert outcome.query.source_event_indices == (1, 2, 3, 4, 5, 6)
    assert outcome.query.strategy == "semantic-generated"
    assert outcome.query.provenance["model"] == "test-model"
    assert len(generator.prompts) == 1


@pytest.mark.parametrize(
    "query",
    [
        "Find Java files that reference PageRequest.",
        "Find implementations of LoadBalance.",
        "Locate calls to isPoolLifo.",
        "Search for Navigation.java.",
        "rg Navigation src/main/java",
    ],
)
def test_mine_query_rejects_direct_lookup_or_gold_leakage(query: str) -> None:
    outcome = mine_query(_case(), _Generator(_valid_response(query)))

    assert outcome.query is None
    assert outcome.skip_reason in {"non_semantic_query", "query_leaks_gold_identifier"}


def test_mine_query_rejects_exact_gold_identifier_but_allows_plain_english_term() -> None:
    leaked = mine_query(
        _case(),
        _Generator(_valid_response("Find Navigation behavior that can retain stale state.")),
    )
    semantic = mine_query(
        _case(),
        _Generator(_valid_response("Find navigation behavior that can retain stale state.")),
    )

    assert leaked.skip_reason == "query_leaks_gold_identifier"
    assert semantic.query is not None


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        (None, "generator_failed"),
        ("not valid JSON", "invalid_model_json"),
        ('{"status":"invalid trace"}', "non_semantic_query"),
    ],
)
def test_mine_query_reports_generation_failures_without_retry(
    response: str | None, reason: str
) -> None:
    generator = _Generator(response)

    outcome = mine_query(_case(), generator)

    assert outcome == MiningOutcome(None, reason)
    assert len(generator.prompts) == 1


def test_mine_query_skips_invalid_gold_or_missing_search_without_calling_model() -> None:
    case = _case()
    missing_gold = TraceCase(
        repo=case.repo,
        language=case.language,
        instance_id=case.instance_id,
        trajectory_id=case.trajectory_id,
        issue_statement=case.issue_statement,
        base_commit=case.base_commit,
        events=case.events,
        raw=case.raw,
        answer=(),
        gold_error="missing_reference_patch",
    )
    no_search = TraceCase(
        repo=case.repo,
        language=case.language,
        instance_id=case.instance_id,
        trajectory_id=case.trajectory_id,
        issue_statement=case.issue_statement,
        base_commit=case.base_commit,
        events=case.events[:2],
        raw=case.raw,
        answer=case.answer,
        gold_error=None,
    )
    generator = _Generator("unused")

    assert mine_query(missing_gold, generator).skip_reason == "missing_reference_patch"
    assert mine_query(no_search, generator).skip_reason == "no_search_events"
    assert generator.prompts == []
