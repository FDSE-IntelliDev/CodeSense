from types import SimpleNamespace

from evaluation.query_mining import OpenAIQueryGenerator


class _Response:
    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict[str, object]:
        return {"choices": [{"message": {"content": "Find buffer backpressure code"}}]}


class _Session:
    def __init__(self, response: object) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    def post(self, url: str, **kwargs: object) -> object:
        self.calls.append({"url": url, **kwargs})
        return self.response


def test_openai_query_generator_sends_configured_model_and_prompt() -> None:
    config = SimpleNamespace(
        api_key="secret",
        base_url="https://llm.example/v1",
        model="qwen-plus",
        timeout=12.0,
    )
    session = _Session(_Response())

    answer = OpenAIQueryGenerator(config, session=session).generate("make a query")

    assert answer == "Find buffer backpressure code"
    assert session.calls == [
        {
            "url": "https://llm.example/v1/chat/completions",
            "headers": {"Authorization": "Bearer secret"},
            "json": {
                "model": "qwen-plus",
                "messages": [{"role": "user", "content": "make a query"}],
                "temperature": 0,
            },
            "timeout": 12.0,
        }
    ]


class _FailingSession:
    def post(self, *args: object, **kwargs: object) -> object:
        raise RuntimeError("request failed")


def test_openai_query_generator_degrades_on_request_failure() -> None:
    config = SimpleNamespace(
        api_key="secret",
        base_url="https://llm.example/v1",
        model="qwen-plus",
        timeout=12.0,
    )

    assert OpenAIQueryGenerator(config, session=_FailingSession()).generate("make a query") is None
