"""真实 LLM 调用的验证。**要花钱**，所以标 slow，默认不跑。

    pytest tests/integration/test_llm_judge.py -m ""

需要 API key（环境变量 `CODESENSE_API_KEY` 或未被跟踪的 config.yml），
以及通过参数指定的端点与模型——它们是流水线参数，不从配置文件读。
"""

from __future__ import annotations

import os

import pytest

from codesense.ql import Element, Frag
from codesense.ql.context import EvalContext
from codesense.ql.operators import intent
from codesense.ql.store import (
    InMemoryEdgeStore,
    InMemoryExpansionTable,
    InMemoryPostingIndex,
    InMemorySymbolStore,
)

BASE_URL = os.environ.get("CODESENSE_LLM_BASE_URL", "https://api.openai.com/v1")
MODEL = os.environ.get("CODESENSE_LLM_MODEL", "gpt-4o-mini")

#: `formatTokenKey` 是**难负例**：名字里有 Token，纯词法必然误命中，
#: 但它只是拼 redis key，并不签发或校验令牌。
CANDIDATES = (
    (1, "generateToken", "(String username) : String", "为用户签发 JWT 访问令牌", True),
    (2, "formatTokenKey", "(String token) : String", "拼 redis key 前缀", False),
    (3, "validateToken", "(String token) : boolean", "校验令牌签名与有效期", True),
    (4, "getUserPage", "(PageQuery q) : Page<UserVO>", "分页查询用户列表", False),
)


@pytest.fixture(scope="module")
def ctx() -> EvalContext:
    from codesense.llm import LlmConfig, OpenAICompatibleJudge

    try:
        config = LlmConfig.load(base_url=BASE_URL, model=MODEL)
    except ValueError as exc:
        pytest.skip(f"没有可用的 API key: {exc}")
    return EvalContext(
        symbols=InMemorySymbolStore(
            [
                Element(
                    symbol_id=sid,
                    name=name,
                    kind="method",
                    file="RedisTokenManager.java",
                    span=(sid, sid),
                    signature=signature,
                    doc=doc,
                    container="RedisTokenManager",
                )
                for sid, name, signature, doc, _ in CANDIDATES
            ]
        ),
        postings=InMemoryPostingIndex({}, total_symbols=100),
        expansion=InMemoryExpansionTable({}),
        edges=InMemoryEdgeStore([]),
        judge=OpenAICompatibleJudge(config),
    )


@pytest.fixture(scope="module")
def judged(ctx: EvalContext) -> Frag:
    frag = Frag(nodes=dict(ctx.symbols.get_many(range(1, 5))))
    return intent(frag, "这段代码在签发或校验身份令牌", ctx, batch_size=4)


@pytest.mark.slow
class TestRealJudge:
    def test_保留了该保留的(self, judged: Frag) -> None:
        assert {"generateToken", "validateToken"} <= {e.name for e in judged}

    def test_筛掉了名字像但语义不符的(self, judged: Frag) -> None:
        """`formatTokenKey` 名字含 Token，纯词法必然误命中。"""
        assert "formatTokenKey" not in {e.name for e in judged}

    def test_筛掉了完全无关的(self, judged: Frag) -> None:
        assert "getUserPage" not in {e.name for e in judged}

    def test_每个结果都带理由(self, judged: Frag) -> None:
        for symbol_id in judged.nodes:
            verdict = judged.evidence_for(symbol_id).verdicts[0]
            assert verdict.source == "llm"
            assert verdict.reason
