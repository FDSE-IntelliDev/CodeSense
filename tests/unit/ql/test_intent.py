"""Unit tests for the `intent` operator.

A fake judge, so no network and no API key. Verification against a real call
lives in `tests/integration/test_llm_judge.py` (marked slow).
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
    """Answers from a script, and records how often it was asked."""

    def __init__(self, verdicts: dict[int, Verdict]) -> None:
        self.verdicts = verdicts
        self.calls: list[tuple[str, tuple[int, ...]]] = []
        self.items: list[JudgeItem] = []

    def judge(self, concept: str, items: Sequence[JudgeItem]) -> dict[int, Verdict]:
        self.calls.append((concept, tuple(i.symbol_id for i in items)))
        self.items.extend(items)
        return {
            i.symbol_id: self.verdicts[i.symbol_id] for i in items if i.symbol_id in self.verdicts
        }


class ExplodingJudge(Judge):
    def judge(self, concept: str, items: Sequence[JudgeItem]) -> dict[int, Verdict]:
        raise RuntimeError("the endpoint is down")


class HallucinatingJudge(Judge):
    """Returns a symbol_id nobody asked about -- what really happens when a
    model garbles its numbering."""

    def judge(self, concept: str, items: Sequence[JudgeItem]) -> dict[int, Verdict]:
        return {999: Verdict(source="llm", label="yes", reason="out of nowhere", score=1.0)}


def make_element(symbol_id: int) -> Element:
    return Element(
        symbol_id=symbol_id, name=f"s{symbol_id}", kind="method", file="A.java", span=(1, 2)
    )


def yes(score: float = 1.0) -> Verdict:
    return Verdict(source="llm", label="yes", reason="matches", score=score)


def no() -> Verdict:
    return Verdict(source="llm", label="no", reason="does not match", score=1.0)


def unsure() -> Verdict:
    return Verdict(source="llm", label="unsure", reason="cannot tell", score=1.0)


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
    def test_keeps_what_was_judged_yes(self) -> None:
        ctx = make_context(ScriptedJudge({1: yes(), 2: no()}))
        assert set(intent(make_frag(1, 2), "c", ctx).nodes) == {1}

    def test_unsure_does_not_count_as_passing(self) -> None:
        """Undecided and decided-yes are different things."""
        ctx = make_context(ScriptedJudge({1: unsure()}))
        assert not intent(make_frag(1), "c", ctx, fallback="drop")

    def test_confidence_below_the_threshold_is_filtered_out(self) -> None:
        ctx = make_context(ScriptedJudge({1: yes(0.9), 2: yes(0.3)}))
        assert set(intent(make_frag(1, 2), "c", ctx, threshold=0.5).nodes) == {1}

    def test_an_empty_fragment_returns_immediately(self) -> None:
        judge = ScriptedJudge({})
        assert not intent(Frag(), "c", make_context(judge))
        assert judge.calls == []


class TestEvidence:
    def test_file_candidates_carry_path_and_search_evidence_to_the_judge(self) -> None:
        element = Element(1, "PageRecord.java", "file", "src/PageRecord.java", (1, 80))
        frag = Frag(
            nodes={1: element},
            evidence={
                1: Evidence(
                    unit_hits=(
                        UnitHit("page request", "lexical", "PageRequest", score=1.0),
                        UnitHit("projection", "graph", "backward references", score=0.0),
                    )
                )
            },
        )
        judge = ScriptedJudge({1: yes()})

        intent(frag, "files referencing PageRequest", make_context(judge))

        assert judge.items[0].file == "src/PageRecord.java"
        assert judge.items[0].evidence == ("PageRequest", "backward references")

    def test_the_verdict_goes_into_the_evidence(self) -> None:
        """Otherwise a user has no way to judge whether to trust it."""
        ctx = make_context(ScriptedJudge({1: yes()}))
        verdicts = intent(make_frag(1), "c", ctx).evidence_for(1).verdicts
        assert verdicts[0].label == "yes"

    def test_the_reason_is_preserved(self) -> None:
        ctx = make_context(ScriptedJudge({1: Verdict("llm", "yes", "it issues tokens", 1.0)}))
        assert (
            intent(make_frag(1), "c", ctx).evidence_for(1).verdicts[0].reason == "it issues tokens"
        )

    def test_existing_evidence_is_not_overwritten(self) -> None:
        """Append, never overwrite -- explainability comes from the whole
        causal chain."""
        frag = Frag(
            nodes={1: make_element(1)},
            evidence={1: Evidence((UnitHit("io", "lexical", "read"),))},
        )
        ctx = make_context(ScriptedJudge({1: yes()}))
        result = intent(frag, "c", ctx).evidence_for(1)
        assert result.unit_hits[0].unit == "io"
        assert result.verdicts[0].label == "yes"


class TestBatching:
    def test_batches_by_batch_size(self) -> None:
        judge = ScriptedJudge({i: yes() for i in range(1, 6)})
        intent(make_frag(1, 2, 3, 4, 5), "c", make_context(judge), batch_size=2)
        assert [len(ids) for _, ids in judge.calls] == [2, 2, 1]

    def test_the_intent_reaches_the_judge_unchanged(self) -> None:
        judge = ScriptedJudge({1: yes()})
        intent(make_frag(1), "this code affects disk IO performance", make_context(judge))
        assert judge.calls[0][0] == "this code affects disk IO performance"

    def test_batch_size_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="batch_size"):
            intent(make_frag(1), "c", make_context(), batch_size=0)


class TestFallback:
    def test_the_undecided_are_kept_by_default(self) -> None:
        assert set(intent(make_frag(1, 2), "c", make_context()).nodes) == {1, 2}

    def test_drop_discards_the_undecided(self) -> None:
        assert not intent(make_frag(1), "c", make_context(), fallback="drop")

    def test_error_fails_outright(self) -> None:
        with pytest.raises(RuntimeError, match="could not be judged"):
            intent(make_frag(1), "c", make_context(), fallback="error")

    def test_a_raising_judge_degrades_rather_than_killing_the_query(self) -> None:
        ctx = make_context(ExplodingJudge())
        assert set(intent(make_frag(1, 2), "c", ctx).nodes) == {1, 2}

    def test_degrading_still_leaves_evidence_of_what_happened(self) -> None:
        verdict = intent(make_frag(1), "c", make_context()).evidence_for(1).verdicts[0]
        assert verdict.source == "fallback"
        assert "fallback" in verdict.reason

    def test_an_invalid_fallback_raises(self) -> None:
        with pytest.raises(ValueError, match="fallback"):
            intent(make_frag(1), "c", make_context(), fallback="whatever")

    def test_degrading_must_log_rather_than_stay_silent(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING, logger="codesense.ql.operators.intent"):
            intent(make_frag(1), "c", make_context())
        assert any("undecided" in r.getMessage() for r in caplog.records)


class TestCostDiscipline:
    def test_too_many_candidates_raises_rather_than_quietly_burning_money(self) -> None:
        """ "There is a cheap constraint still unused upstream" is an
        orchestration error."""
        with pytest.raises(ValueError, match="max_items"):
            intent(make_frag(*range(1, 12)), "c", make_context(), max_items=10)

    def test_the_error_says_how_to_fix_it(self) -> None:
        with pytest.raises(ValueError, match="hop / only / top"):
            intent(make_frag(*range(1, 12)), "c", make_context(), max_items=10)

    def test_the_cap_can_be_turned_off_explicitly(self) -> None:
        ctx = make_context(ScriptedJudge({i: yes() for i in range(1, 12)}))
        assert len(intent(make_frag(*range(1, 12)), "c", ctx, max_items=None)) == 11


class TestRobustness:
    def test_discards_symbol_ids_that_came_from_nowhere(self) -> None:
        """When the model garbles its numbering, keep it out of the result."""
        ctx = make_context(HallucinatingJudge())
        result = intent(make_frag(1), "c", ctx, fallback="drop")
        assert 999 not in result.nodes
        assert not result

    def test_edges_are_pruned_along_with_nodes(self) -> None:
        frag = Frag(
            nodes={1: make_element(1), 2: make_element(2)},
            edges={(1, 2, "calls"): Edge(1, 2, "calls")},
        )
        ctx = make_context(ScriptedJudge({1: yes(), 2: no()}))
        assert intent(frag, "c", ctx).edges == {}

    def test_the_default_judge_is_a_null_implementation_not_None(self) -> None:
        """So the fallback path is genuinely exercised in environments with
        no LLM configured."""
        assert isinstance(make_context().judge, NullJudge)
