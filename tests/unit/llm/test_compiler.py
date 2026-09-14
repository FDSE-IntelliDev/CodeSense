"""Provider-boundary tests for structured query understanding."""

from __future__ import annotations

import json

import pytest

from codesense.llm.compiler import PROMPT, QueryUnderstanding
from codesense.llm.config import LlmConfig
from codesense.llm.schema import QueryUnderstandingResult, TargetKind


def valid_payload() -> dict[str, object]:
    return {
        "units": [
            {
                "name": "page_request",
                "concept": "the PageRequest type",
                "query_terms": ["PageRequest"],
                "terms": [
                    {
                        "value": "PageRequest",
                        "source": "literal",
                        "weight": 1.0,
                        "related_query_terms": ["PageRequest"],
                        "reason": "named by the query",
                    }
                ],
            }
        ],
        "relations": [
            {
                "source": {"kind": "result", "unit": None},
                "target": {"kind": "unit", "unit": "page_request"},
                "edges": ["references"],
            }
        ],
        "targets": ["file"],
        "annotations": [],
        "criterion": "A file whose code references PageRequest",
    }


class FakeResponse:
    def __init__(self, message: dict[str, object], *, error: Exception | None = None) -> None:
        self._message = message
        self._error = error

    def raise_for_status(self) -> None:
        if self._error is not None:
            raise self._error

    def json(self) -> dict[str, object]:
        return {"choices": [{"message": self._message}]}


class FakeSession:
    def __init__(self, message: dict[str, object], *, error: Exception | None = None) -> None:
        self.response = FakeResponse(message, error=error)
        self.calls: list[dict[str, object]] = []

    def post(self, url: str, **kwargs: object) -> FakeResponse:
        self.calls.append({"url": url, **kwargs})
        return self.response


class BrokenSession:
    def post(self, _url: str, **_kwargs: object) -> FakeResponse:
        raise ConnectionError("network unavailable")


def config() -> LlmConfig:
    return LlmConfig(api_key="test", base_url="https://example.invalid/v1", model="model")


def test_prompt_describes_semantic_sources_result_roles_and_json_output() -> None:
    prompt = " ".join(PROMPT.lower().split())

    assert all(source in prompt for source in ("literal", "synonym", "derived"))
    assert all(edge in prompt for edge in ("calls", "contains", "references", "imports", "in_file"))
    assert "result endpoint" in prompt
    assert "outside this sample" in prompt
    assert "json" in prompt


def test_understanding_posts_schema_and_returns_a_typed_model() -> None:
    session = FakeSession({"content": json.dumps(valid_payload())})

    understood = QueryUnderstanding(config(), session=session).understand(
        "Find files whose code references PageRequest",
        "demo",
        (("page", 20), ("request", 8)),
    )

    assert isinstance(understood, QueryUnderstandingResult)
    assert understood.targets == [TargetKind.FILE]
    call = session.calls[0]
    assert call["url"] == "https://example.invalid/v1/chat/completions"
    body = call["json"]
    assert body["response_format"]["type"] == "json_schema"  # type: ignore[index]
    assert body["response_format"]["json_schema"]["strict"] is True  # type: ignore[index]
    assert "page:20" in body["messages"][0]["content"]  # type: ignore[index]
    assert body["temperature"] == 0  # type: ignore[index]


@pytest.mark.parametrize(
    "message",
    [
        {"refusal": "cannot comply", "content": None},
        {},
        {"content": "not json"},
        {"content": json.dumps({"units": []})},
    ],
)
def test_unusable_provider_messages_return_none(message: dict[str, object]) -> None:
    session = FakeSession(message)

    assert QueryUnderstanding(config(), session=session).understand("query", "demo", ()) is None


def test_http_failure_returns_none() -> None:
    session = FakeSession({"content": "unused"}, error=RuntimeError("bad gateway"))

    assert QueryUnderstanding(config(), session=session).understand("query", "demo", ()) is None


def test_transport_failure_returns_none() -> None:
    assert (
        QueryUnderstanding(config(), session=BrokenSession()).understand("query", "demo", ())
        is None
    )


def test_complete_message_shape_is_consumed_without_loose_brace_extraction() -> None:
    content = f"prefix {json.dumps(valid_payload())} suffix"
    session = FakeSession({"content": content, "refusal": None})

    understood = QueryUnderstanding(config(), session=session).understand("query", "demo", ())

    assert understood is None
