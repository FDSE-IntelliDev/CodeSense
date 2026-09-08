"""Contract tests for structured query understanding proposals."""

from __future__ import annotations

from typing import Any

from codesense.llm.compiler import QueryUnderstanding
from codesense.llm.config import LlmConfig


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
    assert understood["target"] == ()
