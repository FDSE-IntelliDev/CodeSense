"""Contract tests for structured query understanding proposals."""

from __future__ import annotations

from typing import Any

from codesense.llm.compiler import PROMPT, QueryUnderstanding
from codesense.llm.config import LlmConfig


def test_prompt_allows_all_explicit_supported_relation_kinds() -> None:
    prompt = " ".join(PROMPT.lower().split())
    relation_rule = prompt[
        prompt.index("- propose relations whenever") : prompt.index("- valid edge names")
    ]

    assert "explicitly expresses a relation between groups" in relation_rule
    assert all(
        edge in relation_rule for edge in ("calls", "contains", "references", "imports", "in_file")
    )
    assert "otherwise give an empty list" in relation_rule
    assert "relation verb" in prompt and "does not imply a file" in prompt


class FakeResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return {
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"terms":{"page":1.0,"request":1.0},'
                            '"groups":{"page request":["page","request"]},'
                            '"relations":[{"src":"clients","dst":"page request",'
                            '"edge":["references"]}],"target":["file"],'
                            '"annotations":[],"concept":"Files whose code references PageRequest"}'
                        )
                    }
                }
            ]
        }


class FakeSession:
    def post(self, *_args: Any, **_kwargs: Any) -> FakeResponse:
        return FakeResponse()


def test_understanding_keeps_target_and_typed_relation() -> None:
    config = LlmConfig(api_key="test")

    understood = QueryUnderstanding(config, session=FakeSession()).understand(
        "Find files whose code references PageRequest", "demo", ("page", "request")
    )

    assert understood is not None
    assert understood["target"] == ("file",)
    assert understood["relations"] == [("clients", "page request", ("references",))]


def test_two_item_relations_keep_the_legacy_edge_default() -> None:
    class LegacySession(FakeSession):
        def post(self, *_args: Any, **_kwargs: Any) -> FakeResponse:
            response = FakeResponse()
            response.json = lambda: {
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"terms":{"page":1},"relations":[["clients","page request"]]}'
                            )
                        }
                    }
                ]
            }
            return response

    understood = QueryUnderstanding(LlmConfig(api_key="test"), session=LegacySession()).understand(
        "clients calls PageRequest", "demo", ("page", "request")
    )

    assert understood is not None
    assert understood["relations"] == [("clients", "page request", ("calls", "contains"))]
    assert "target" not in understood


def test_understanding_omits_target_when_the_model_omits_it() -> None:
    """Missing and explicitly empty targets have different precedence."""

    class MissingTargetSession(FakeSession):
        def post(self, *_args: Any, **_kwargs: Any) -> FakeResponse:
            response = FakeResponse()
            response.json = lambda: {
                "choices": [{"message": {"content": '{"terms":{"alloc":1},"relations":[]}'}}]
            }
            return response

    understood = QueryUnderstanding(
        LlmConfig(api_key="test"), session=MissingTargetSession()
    ).understand("Find Java files containing alloc", "demo", ("alloc",))

    assert understood is not None
    assert "target" not in understood


def test_understanding_preserves_an_explicit_empty_target() -> None:
    """An explicit empty model decision must suppress later text inference."""

    class EmptyTargetSession(FakeSession):
        def post(self, *_args: Any, **_kwargs: Any) -> FakeResponse:
            response = FakeResponse()
            response.json = lambda: {
                "choices": [
                    {"message": {"content": '{"terms":{"alloc":1},"relations":[],"target":[]}'}}
                ]
            }
            return response

    understood = QueryUnderstanding(
        LlmConfig(api_key="test"), session=EmptyTargetSession()
    ).understand("Find Java files containing alloc", "demo", ("alloc",))

    assert understood is not None
    assert understood["target"] == ()


def test_malformed_relation_edges_are_discarded_conservatively() -> None:
    class MalformedSession(FakeSession):
        def post(self, *_args: Any, **_kwargs: Any) -> FakeResponse:
            response = FakeResponse()
            response.json = lambda: {
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"terms":{"page":1},"relations":['
                                '{"src":"clients","dst":"page request",'
                                '"edge":["references",7,"not-a-real-edge"]},'
                                '{"src":"a","dst":"b","edge":null}]}'
                            )
                        }
                    }
                ]
            }
            return response

    understood = QueryUnderstanding(
        LlmConfig(api_key="test"), session=MalformedSession()
    ).understand("reference PageRequest", "demo", ("page", "request"))

    assert understood is not None
    assert understood["relations"] == [
        ("clients", "page request", ("references",)),
        ("a", "b", ("calls", "contains")),
    ]
    assert "target" not in understood
