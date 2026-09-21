"""Parsing and configuration tests for the LLM adapters. No network."""

from __future__ import annotations

from pathlib import Path

import pytest

from codesense.llm import LlmConfig
from codesense.llm.judge import OpenAICompatibleJudge, _parse
from codesense.ql.judge import JudgeItem


class FakeResponse:
    def __init__(self, content: str) -> None:
        self._content = content

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return {"choices": [{"message": {"content": self._content}}]}


class FakeSession:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[dict[str, object]] = []

    def post(self, url: str, **kwargs: object) -> FakeResponse:
        self.calls.append({"url": url, **kwargs})
        return FakeResponse(self.content)


class BrokenSession:
    def post(self, url: str, **kwargs: object) -> FakeResponse:
        raise ConnectionError("the network is down")


def config() -> LlmConfig:
    return LlmConfig(api_key="sk-test", base_url="https://example.invalid/v1", model="m")


def items() -> list[JudgeItem]:
    return [JudgeItem(symbol_id=1, name="f", kind="method")]


class TestParse:
    def test_clean_json(self) -> None:
        found = _parse('[{"id":1,"label":"yes","score":0.9,"reason":"r"}]', {1})
        assert found[1].label == "yes"
        assert found[1].score == 0.9

    def test_tolerates_a_code_fence(self) -> None:
        """Models routinely ignore "output JSON only"."""
        raw = '```json\n[{"id":1,"label":"yes","score":1,"reason":"r"}]\n```'
        assert 1 in _parse(raw, {1})

    def test_tolerates_surrounding_chatter(self) -> None:
        raw = (
            "Sure, here are my judgements:\n"
            '[{"id":1,"label":"no","score":1,"reason":"r"}]\nHope that helps'
        )
        assert _parse(raw, {1})[1].label == "no"

    def test_discards_ids_that_were_never_asked_about(self) -> None:
        assert _parse('[{"id":99,"label":"yes","score":1,"reason":"r"}]', {1}) == {}

    def test_invalid_json_returns_nothing_rather_than_raising(self) -> None:
        assert _parse("[{not json}]", {1}) == {}

    def test_returns_nothing_when_no_array_is_found(self) -> None:
        assert _parse("I decline to answer", {1}) == {}

    def test_skips_malformed_entries(self) -> None:
        raw = '[{"id":1,"label":"yes","score":1,"reason":"r"}, "junk", {"no_id":true}]'
        assert set(_parse(raw, {1, 2})) == {1}

    def test_out_of_range_scores_are_clamped(self) -> None:
        assert _parse('[{"id":1,"label":"yes","score":5,"reason":""}]', {1})[1].score == 1.0
        assert _parse('[{"id":1,"label":"yes","score":-3,"reason":""}]', {1})[1].score == 0.0

    def test_a_non_numeric_score_becomes_0(self) -> None:
        assert _parse('[{"id":1,"label":"yes","score":"high","reason":""}]', {1})[1].score == 0.0

    def test_a_missing_label_becomes_unsure(self) -> None:
        assert _parse('[{"id":1,"score":1,"reason":""}]', {1})[1].label == "unsure"


class TestJudge:
    def test_no_request_is_sent_for_an_empty_candidate_list(self) -> None:
        session = FakeSession("[]")
        assert OpenAICompatibleJudge(config(), session).judge("c", []) == {}
        assert session.calls == []

    def test_the_request_goes_to_chat_completions(self) -> None:
        session = FakeSession('[{"id":1,"label":"yes","score":1,"reason":"r"}]')
        OpenAICompatibleJudge(config(), session).judge("c", items())
        assert session.calls[0]["url"].endswith("/chat/completions")  # type: ignore[union-attr]

    def test_the_intent_reaches_the_prompt(self) -> None:
        session = FakeSession("[]")
        OpenAICompatibleJudge(config(), session).judge("decide whether it issues a token", items())
        body = session.calls[0]["json"]
        assert "decide whether it issues a token" in body["messages"][0]["content"]  # type: ignore[index]

    def test_file_path_and_retrieval_evidence_reach_the_prompt(self) -> None:
        session = FakeSession("[]")
        candidate = JudgeItem(
            symbol_id=1,
            name="PageRecord.java",
            kind="file",
            file="src/PageRecord.java",
            evidence=("PageRequest", "backward references"),
        )

        OpenAICompatibleJudge(config(), session).judge("files referencing PageRequest", [candidate])

        body = session.calls[0]["json"]
        prompt = body["messages"][0]["content"]  # type: ignore[index]
        assert "src/PageRecord.java" in prompt
        assert "PageRequest" in prompt
        assert "backward references" in prompt

    def test_temperature_0_so_results_reproduce(self) -> None:
        session = FakeSession("[]")
        OpenAICompatibleJudge(config(), session).judge("c", items())
        assert session.calls[0]["json"]["temperature"] == 0  # type: ignore[index]

    def test_a_network_error_returns_nothing_rather_than_raising(self) -> None:
        """Degrading is intent's decision; the adapter must not kill the
        whole query."""
        assert OpenAICompatibleJudge(config(), BrokenSession()).judge("c", items()) == {}


class TestConfig:
    def test_default_model_is_qwen_3_7_plus(self) -> None:
        assert LlmConfig(api_key="k").model == "qwen3.7-plus"

    def test_repr_does_not_leak_the_key(self) -> None:
        """It shows up in logs, tracebacks and pytest -v."""
        assert "sk-test" not in repr(config())
        assert "hidden" in repr(config())

    def test_a_missing_key_raises_and_says_how_to_configure_it(self) -> None:
        with pytest.raises(ValueError, match="CODESENSE_API_KEY"):
            LlmConfig(api_key="")

    def test_parameters_come_from_the_caller(self) -> None:
        cfg = LlmConfig(api_key="k", base_url="https://x/v1", model="mymodel")
        assert (cfg.base_url, cfg.model) == ("https://x/v1", "mymodel")

    def test_the_environment_wins_over_the_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        secrets = tmp_path / "config.yml"
        secrets.write_text("LLM:\n  - api-key: from-file\n", encoding="utf-8")
        monkeypatch.setenv("CODESENSE_API_KEY", "from-env")
        assert LlmConfig.load(secrets_file=secrets).api_key == "from-env"

    def test_falls_back_to_the_untracked_config_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("CODESENSE_API_KEY", raising=False)
        secrets = tmp_path / "config.yml"
        secrets.write_text("LLM:\n  - api-key: from-file\n", encoding="utf-8")
        assert LlmConfig.load(secrets_file=secrets).api_key == "from-file"

    def test_the_non_list_form_is_accepted_too(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("CODESENSE_API_KEY", raising=False)
        secrets = tmp_path / "config.yml"
        secrets.write_text("LLM:\n  api_key: plain\n", encoding="utf-8")
        assert LlmConfig.load(secrets_file=secrets).api_key == "plain"

    def test_raises_when_the_file_is_absent_and_the_environment_is_unset(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("CODESENSE_API_KEY", raising=False)
        with pytest.raises(ValueError, match="API key"):
            LlmConfig.load(secrets_file=tmp_path / "missing.yml")
