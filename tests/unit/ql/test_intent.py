"""`intent` 算子的单元测试。

用假判定器，不碰网络也不需要 API key。真实调用的验证在
`tests/integration/test_llm_judge.py`（标 slow）。
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

import pytest

from codesense.ql import Edge, Element, Evidence, Frag, Judge, JudgeItem, NullJudge, UnitHit
from codesense.ql.context import EvalContext
from codesense.ql.frag import Verdict
from codesense.ql.operators import intent
from codesense.ql.store import (
    InMemoryEdgeStore,
    InMemoryExpansionTable,
    InMemoryPostingIndex,
    InMemorySymbolStore,
)


class ScriptedJudge(Judge):
    """按预设结果回答，并记录被问过几次。"""

    def __init__(self, verdicts: dict[int, Verdict]) -> None:
        self.verdicts = verdicts
        self.calls: list[tuple[str, tuple[int, ...]]] = []

    def judge(self, concept: str, items: Sequence[JudgeItem]) -> dict[int, Verdict]:
        self.calls.append((concept, tuple(i.symbol_id for i in items)))
        return {
            i.symbol_id: self.verdicts[i.symbol_id] for i in items if i.symbol_id in self.verdicts
        }


class ExplodingJudge(Judge):
    def judge(self, concept: str, items: Sequence[JudgeItem]) -> dict[int, Verdict]:
        raise RuntimeError("接口挂了")


class HallucinatingJudge(Judge):
    """返回没问过的 symbol_id——模型编号错乱时的真实情况。"""

    def judge(self, concept: str, items: Sequence[JudgeItem]) -> dict[int, Verdict]:
        return {999: Verdict(source="llm", label="yes", reason="凭空出现", score=1.0)}


def make_element(symbol_id: int) -> Element:
    return Element(
        symbol_id=symbol_id, name=f"s{symbol_id}", kind="method", file="A.java", span=(1, 2)
    )


def yes(score: float = 1.0) -> Verdict:
    return Verdict(source="llm", label="yes", reason="符合", score=score)


def no() -> Verdict:
    return Verdict(source="llm", label="no", reason="不符合", score=1.0)


def unsure() -> Verdict:
    return Verdict(source="llm", label="unsure", reason="判不出", score=1.0)


def make_context(judge: Judge | None = None) -> EvalContext:
    return EvalContext(
        symbols=InMemorySymbolStore([]),
        postings=InMemoryPostingIndex({}, total_symbols=100),
        expansion=InMemoryExpansionTable({}),
        edges=InMemoryEdgeStore([]),
        judge=judge or NullJudge(),
    )


def make_frag(*symbol_ids: int) -> Frag:
    return Frag(nodes={sid: make_element(sid) for sid in symbol_ids})


class TestFiltering:
    def test_保留判为是的(self) -> None:
        ctx = make_context(ScriptedJudge({1: yes(), 2: no()}))
        assert set(intent(make_frag(1, 2), "c", ctx).nodes) == {1}

    def test_判为_unsure_的不算通过(self) -> None:
        """判不出和判为是是两回事。"""
        ctx = make_context(ScriptedJudge({1: unsure()}))
        assert not intent(make_frag(1), "c", ctx, fallback="drop")

    def test_置信度低于阈值的筛掉(self) -> None:
        ctx = make_context(ScriptedJudge({1: yes(0.9), 2: yes(0.3)}))
        assert set(intent(make_frag(1, 2), "c", ctx, threshold=0.5).nodes) == {1}

    def test_空片段直接返回(self) -> None:
        judge = ScriptedJudge({})
        assert not intent(Frag(), "c", make_context(judge))
        assert judge.calls == []


class TestEvidence:
    def test_判定进证据(self) -> None:
        """否则用户无从判断该不该信。"""
        ctx = make_context(ScriptedJudge({1: yes()}))
        verdicts = intent(make_frag(1), "c", ctx).evidence_for(1).verdicts
        assert verdicts[0].label == "yes"

    def test_理由被保留(self) -> None:
        ctx = make_context(ScriptedJudge({1: Verdict("llm", "yes", "它签发令牌", 1.0)}))
        assert intent(make_frag(1), "c", ctx).evidence_for(1).verdicts[0].reason == "它签发令牌"

    def test_原有证据不被覆盖(self) -> None:
        """只追加，不覆盖——可解释性来自完整因果链。"""
        frag = Frag(
            nodes={1: make_element(1)},
            evidence={1: Evidence((UnitHit("io", "lexical", "read"),))},
        )
        ctx = make_context(ScriptedJudge({1: yes()}))
        result = intent(frag, "c", ctx).evidence_for(1)
        assert result.unit_hits[0].unit == "io"
        assert result.verdicts[0].label == "yes"


class TestBatching:
    def test_按_batch_size_分批(self) -> None:
        judge = ScriptedJudge({i: yes() for i in range(1, 6)})
        intent(make_frag(1, 2, 3, 4, 5), "c", make_context(judge), batch_size=2)
        assert [len(ids) for _, ids in judge.calls] == [2, 2, 1]

    def test_意图原样传给判定器(self) -> None:
        judge = ScriptedJudge({1: yes()})
        intent(make_frag(1), "这段代码影响磁盘 IO 的性能表现", make_context(judge))
        assert judge.calls[0][0] == "这段代码影响磁盘 IO 的性能表现"

    def test_batch_size_必须为正(self) -> None:
        with pytest.raises(ValueError, match="batch_size"):
            intent(make_frag(1), "c", make_context(), batch_size=0)


class TestFallback:
    def test_默认保留判不出的(self) -> None:
        assert set(intent(make_frag(1, 2), "c", make_context()).nodes) == {1, 2}

    def test_drop_丢弃判不出的(self) -> None:
        assert not intent(make_frag(1), "c", make_context(), fallback="drop")

    def test_error_直接失败(self) -> None:
        with pytest.raises(RuntimeError, match="判定失败"):
            intent(make_frag(1), "c", make_context(), fallback="error")

    def test_判定器抛异常时降级而不是炸掉整条查询(self) -> None:
        ctx = make_context(ExplodingJudge())
        assert set(intent(make_frag(1, 2), "c", ctx).nodes) == {1, 2}

    def test_降级也留下证据说明发生了什么(self) -> None:
        verdict = intent(make_frag(1), "c", make_context()).evidence_for(1).verdicts[0]
        assert verdict.source == "fallback"
        assert "降级" in verdict.reason

    def test_非法_fallback_报错(self) -> None:
        with pytest.raises(ValueError, match="fallback"):
            intent(make_frag(1), "c", make_context(), fallback="随便")

    def test_降级要_log_而不是静默(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING, logger="codesense.ql.operators.intent"):
            intent(make_frag(1), "c", make_context())
        assert any("没判出来" in r.getMessage() for r in caplog.records)


class TestCostDiscipline:
    def test_候选太多直接报错而不是默默烧钱(self) -> None:
        """「前面还有没用上的便宜约束」是编排错误。"""
        with pytest.raises(ValueError, match="max_items"):
            intent(make_frag(*range(1, 12)), "c", make_context(), max_items=10)

    def test_报错信息指出该怎么改(self) -> None:
        with pytest.raises(ValueError, match="hop / only / top"):
            intent(make_frag(*range(1, 12)), "c", make_context(), max_items=10)

    def test_可以显式关掉上限(self) -> None:
        ctx = make_context(ScriptedJudge({i: yes() for i in range(1, 12)}))
        assert len(intent(make_frag(*range(1, 12)), "c", ctx, max_items=None)) == 11


class TestRobustness:
    def test_丢弃凭空出现的_symbol_id(self) -> None:
        """模型编号错乱时不能让它往结果里塞东西。"""
        ctx = make_context(HallucinatingJudge())
        result = intent(make_frag(1), "c", ctx, fallback="drop")
        assert 999 not in result.nodes
        assert not result

    def test_边随节点裁剪(self) -> None:
        frag = Frag(
            nodes={1: make_element(1), 2: make_element(2)},
            edges={(1, 2, "calls"): Edge(1, 2, "calls")},
        )
        ctx = make_context(ScriptedJudge({1: yes(), 2: no()}))
        assert intent(frag, "c", ctx).edges == {}

    def test_默认判定器是空实现而不是_None(self) -> None:
        """这样降级路径在没配 LLM 的环境里也会被真正走到。"""
        assert isinstance(make_context().judge, NullJudge)
