from evaluation.models import TraceCase, TraceEvent
from evaluation.query_mining import build_prompt, find_episodes, mine_queries


def _case() -> TraceCase:
    events = (
        TraceEvent(0, "user", "Fix write buffer backpressure."),
        TraceEvent(1, "assistant", "I will inspect the buffer code."),
        TraceEvent(
            2,
            "assistant",
            "",
            "rg",
            "rg --glob '*.java' backpressure src/main/java",
            None,
        ),
        TraceEvent(3, "tool", "", "rg", None, "src/main/java/Buffer.java"),
        TraceEvent(
            4,
            "assistant",
            "",
            "rg",
            "rg --glob '*.java' backpressure watermark src/main/java",
            None,
        ),
        TraceEvent(5, "tool", "", "rg", None, "src/main/java/Watermark.java"),
        TraceEvent(
            6,
            "assistant",
            "",
            "rg",
            "rg --glob '*.java' timeout src/main/java",
            None,
        ),
        TraceEvent(7, "assistant", "Later discovery: src/main/java/Gold.java"),
    )
    return TraceCase(
        repo="acme/project",
        language="java",
        instance_id="issue-1",
        trajectory_id="trace-1",
        issue_statement="Fix write buffer backpressure.",
        base_commit="abc123",
        events=events,
        raw={},
    )


def test_find_episodes_merges_overlapping_search_actions_and_splits_target_change() -> None:
    episodes = find_episodes(_case())

    assert len(episodes) == 2
    assert [event.index for event in episodes[0]] == [2, 4]
    assert [event.index for event in episodes[1]] == [6]


def test_build_prompt_is_prefix_only_and_hides_tool_outputs_and_future_events() -> None:
    prompt = build_prompt(_case(), find_episodes(_case())[0], prompt_version="test-v1")

    assert "Fix write buffer backpressure." in prompt
    assert "I will inspect the buffer code." in prompt
    assert "rg --glob '*.java' backpressure src/main/java" in prompt
    assert "src/main/java/Buffer.java" not in prompt
    assert "src/main/java/Watermark.java" not in prompt
    assert "Gold.java" not in prompt


class _Generator:
    def __init__(self, answer: str | None) -> None:
        self.answer = answer
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> str | None:
        self.prompts.append(prompt)
        return self.answer


def test_mine_queries_uses_generated_query_for_shell_search_actions() -> None:
    generator = _Generator('{"query": "Find code that applies backpressure when a buffer fills"}')

    queries = mine_queries(_case(), generator)

    assert len(queries) == 2
    assert queries[0].strategy == "generated"
    assert queries[0].query == "Find code that applies backpressure when a buffer fills"
    assert queries[0].raw_action.startswith("rg ")
    assert len(generator.prompts) == 2


def test_trace_without_search_actions_produces_no_queries() -> None:
    case = _case()
    case = TraceCase(
        repo=case.repo,
        language=case.language,
        instance_id=case.instance_id,
        trajectory_id=case.trajectory_id,
        issue_statement=case.issue_statement,
        base_commit=case.base_commit,
        events=(case.events[0], case.events[1]),
        raw=case.raw,
    )

    assert mine_queries(case, _Generator("unused")) == ()


class _SequenceGenerator:
    model = "test-model"

    def __init__(self, answers: list[str | None]) -> None:
        self.answers = answers

    def generate(self, prompt: str) -> str | None:
        del prompt
        return self.answers.pop(0)


def test_mine_queries_retries_once_when_generated_query_is_invalid() -> None:
    generator = _SequenceGenerator(
        [
            "rg src/main/java/Buffer.java",
            "Find code responsible for buffer backpressure",
            "Find code responsible for timeout handling",
        ]
    )

    queries = mine_queries(_case(), generator)

    assert len(queries) == 2
    assert queries[0].query == "Find code responsible for buffer backpressure"
    assert queries[0].provenance["model"] == "test-model"
