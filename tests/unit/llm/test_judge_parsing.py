"""LLM 适配器的解析与配置测试。不碰网络。"""

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
        raise ConnectionError("网络挂了")


def config() -> LlmConfig:
    return LlmConfig(api_key="sk-test", base_url="https://example.invalid/v1", model="m")


def items() -> list[JudgeItem]:
    return [JudgeItem(symbol_id=1, name="f", kind="method")]


class TestParse:
    def test_干净的_json(self) -> None:
        found = _parse('[{"id":1,"label":"yes","score":0.9,"reason":"r"}]', {1})
        assert found[1].label == "yes"
        assert found[1].score == 0.9

    def test_容忍_code_fence(self) -> None:
        """模型经常不听「只输出 JSON」。"""
        raw = '```json\n[{"id":1,"label":"yes","score":1,"reason":"r"}]\n```'
        assert 1 in _parse(raw, {1})

    def test_容忍前后废话(self) -> None:
        raw = '好的，我的判断如下：\n[{"id":1,"label":"no","score":1,"reason":"r"}]\n希望有帮助'
        assert _parse(raw, {1})[1].label == "no"

    def test_丢弃没问过的_id(self) -> None:
        assert _parse('[{"id":99,"label":"yes","score":1,"reason":"r"}]', {1}) == {}

    def test_非法_json_返回空而不是抛(self) -> None:
        assert _parse("[{不是 json}]", {1}) == {}

    def test_找不到数组返回空(self) -> None:
        assert _parse("我拒绝回答", {1}) == {}

    def test_跳过结构不对的条目(self) -> None:
        raw = '[{"id":1,"label":"yes","score":1,"reason":"r"}, "垃圾", {"没有id":true}]'
        assert set(_parse(raw, {1, 2})) == {1}

    def test_分数越界被夹紧(self) -> None:
        assert _parse('[{"id":1,"label":"yes","score":5,"reason":""}]', {1})[1].score == 1.0
        assert _parse('[{"id":1,"label":"yes","score":-3,"reason":""}]', {1})[1].score == 0.0

    def test_分数不是数字时记0(self) -> None:
        assert _parse('[{"id":1,"label":"yes","score":"高","reason":""}]', {1})[1].score == 0.0

    def test_缺少_label_记_unsure(self) -> None:
        assert _parse('[{"id":1,"score":1,"reason":""}]', {1})[1].label == "unsure"


class TestJudge:
    def test_空候选不发请求(self) -> None:
        session = FakeSession("[]")
        assert OpenAICompatibleJudge(config(), session).judge("c", []) == {}
        assert session.calls == []

    def test_请求打到_chat_completions(self) -> None:
        session = FakeSession('[{"id":1,"label":"yes","score":1,"reason":"r"}]')
        OpenAICompatibleJudge(config(), session).judge("c", items())
        assert session.calls[0]["url"].endswith("/chat/completions")  # type: ignore[union-attr]

    def test_意图写进提示词(self) -> None:
        session = FakeSession("[]")
        OpenAICompatibleJudge(config(), session).judge("判断它是否签发令牌", items())
        body = session.calls[0]["json"]
        assert "判断它是否签发令牌" in body["messages"][0]["content"]  # type: ignore[index]

    def test_温度为0_结果可复现(self) -> None:
        session = FakeSession("[]")
        OpenAICompatibleJudge(config(), session).judge("c", items())
        assert session.calls[0]["json"]["temperature"] == 0  # type: ignore[index]

    def test_网络异常时返回空而不是抛(self) -> None:
        """降级由 intent 决定，适配器不该炸掉整条查询。"""
        assert OpenAICompatibleJudge(config(), BrokenSession()).judge("c", items()) == {}


class TestConfig:
    def test_repr_不泄露密钥(self) -> None:
        """它会出现在日志、异常栈、pytest -v 里。"""
        assert "sk-test" not in repr(config())
        assert "已隐藏" in repr(config())

    def test_没有密钥时报错并说清怎么配(self) -> None:
        with pytest.raises(ValueError, match="CODESENSE_API_KEY"):
            LlmConfig(api_key="")

    def test_参数由调用方给(self) -> None:
        cfg = LlmConfig(api_key="k", base_url="https://x/v1", model="mymodel")
        assert (cfg.base_url, cfg.model) == ("https://x/v1", "mymodel")

    def test_环境变量优先于文件(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        secrets = tmp_path / "config.yml"
        secrets.write_text("LLM:\n  - api-key: from-file\n", encoding="utf-8")
        monkeypatch.setenv("CODESENSE_API_KEY", "from-env")
        assert LlmConfig.load(secrets_file=secrets).api_key == "from-env"

    def test_回落到未跟踪的配置文件(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CODESENSE_API_KEY", raising=False)
        secrets = tmp_path / "config.yml"
        secrets.write_text("LLM:\n  - api-key: from-file\n", encoding="utf-8")
        assert LlmConfig.load(secrets_file=secrets).api_key == "from-file"

    def test_也接受非列表写法(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CODESENSE_API_KEY", raising=False)
        secrets = tmp_path / "config.yml"
        secrets.write_text("LLM:\n  api_key: plain\n", encoding="utf-8")
        assert LlmConfig.load(secrets_file=secrets).api_key == "plain"

    def test_文件不存在且无环境变量时报错(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("CODESENSE_API_KEY", raising=False)
        with pytest.raises(ValueError, match="API key"):
            LlmConfig.load(secrets_file=tmp_path / "缺失.yml")
