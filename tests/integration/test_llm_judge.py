"""Verification against a real LLM call. **This costs money**, so it is
marked slow and does not run by default.

    pytest tests/integration/test_llm_judge.py -m ""

Needs an API key (the `CODESENSE_API_KEY` environment variable or the
untracked config.yml) plus an endpoint and model given as parameters -- those
are pipeline parameters and are not read from a config file.
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

#: `formatTokenKey` is the **hard negative**: its name contains Token, so
#: pure lexical matching is bound to hit it, but it only builds a redis key
#: and neither issues nor validates a token.
CANDIDATES = (
    (
        1,
        "generateToken",
        "(String username) : String",
        "issues a JWT access token for a user",
        True,
    ),
    (2, "formatTokenKey", "(String token) : String", "builds a redis key prefix", False),
    (
        3,
        "validateToken",
        "(String token) : boolean",
        "validates a token's signature and expiry",
        True,
    ),
    (4, "getUserPage", "(PageQuery q) : Page<UserVO>", "paginated query over the user list", False),
)


@pytest.fixture(scope="module")
def ctx() -> EvalContext:
    from codesense.llm import LlmConfig, OpenAICompatibleJudge

    try:
        config = LlmConfig.load(base_url=BASE_URL, model=MODEL)
    except ValueError as exc:
        pytest.skip(f"no usable API key: {exc}")
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
    return intent(frag, "this code issues or validates an identity token", ctx, batch_size=4)


@pytest.mark.slow
class TestRealJudge:
    def test_keeps_what_should_be_kept(self, judged: Frag) -> None:
        assert {"generateToken", "validateToken"} <= {e.name for e in judged}

    def test_drops_a_lookalike_name_that_does_not_match_semantically(self, judged: Frag) -> None:
        """`formatTokenKey` has Token in its name, so pure lexical matching
        is bound to hit it."""
        assert "formatTokenKey" not in {e.name for e in judged}

    def test_drops_the_entirely_unrelated(self, judged: Frag) -> None:
        assert "getUserPage" not in {e.name for e in judged}

    def test_every_result_carries_a_reason(self, judged: Frag) -> None:
        for symbol_id in judged.nodes:
            verdict = judged.evidence_for(symbol_id).verdicts[0]
            assert verdict.source == "llm"
            assert verdict.reason
