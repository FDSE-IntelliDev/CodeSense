"""Unit tests for registry / combine / satisfiers / `eval_unit`."""

from __future__ import annotations

import json
from dataclasses import asdict

import pytest

from codesense.ql import Element, Evidence, IndexField, UnitHit, Verdict
from codesense.ql.combine import COMBINERS, combine
from codesense.ql.context import EvalContext
from codesense.ql.operators import eval_unit
from codesense.ql.registry import Registry
from codesense.ql.satisfiers import AnnotationSatisfier, LexicalSatisfier, ModifierSatisfier
from codesense.ql.store import (
    Expansion,
    InMemoryEdgeStore,
    InMemoryExpansionTable,
    InMemoryPostingIndex,
    InMemorySymbolStore,
    Posting,
)
from codesense.ql.unit import QueryUnit, Term

TOTAL = 1718


def make_element(symbol_id: int, name: str) -> Element:
    return Element(symbol_id=symbol_id, name=name, kind="method", file="A.java", span=(1, 2))


def make_context(
    *,
    postings: dict[str, list[Posting]] | None = None,
    expansion: dict[str, list[Expansion]] | None = None,
    elements: list[Element] | None = None,
    **kwargs: object,
) -> EvalContext:
    return EvalContext(
        symbols=InMemorySymbolStore(elements or [make_element(1, "flushBuf")]),
        postings=InMemoryPostingIndex(postings or {}, total_symbols=TOTAL),
        expansion=InMemoryExpansionTable(expansion or {}),
        edges=InMemoryEdgeStore([]),
        **kwargs,  # type: ignore[arg-type]
    )


class TestRegistry:
    def test_a_duplicate_registration_raises_rather_than_silently_overwriting(self) -> None:
        reg: Registry[int] = Registry("test entry")
        reg.register("a", 1)
        with pytest.raises(ValueError, match="already has an implementation named"):
            reg.register("a", 2)

    def test_an_unknown_name_lists_what_is_registered(self) -> None:
        reg: Registry[int] = Registry("test entry")
        reg.register("alpha", 1)
        with pytest.raises(KeyError, match="alpha"):
            reg.get("beta")

    def test_the_decorator_form(self) -> None:
        reg: Registry[object] = Registry("test entry")

        @reg.decorator("thing")
        class Thing:
            pass

        assert reg.get("thing") is Thing


class TestCombine:
    def test_all_three_strategies_are_registered(self) -> None:
        assert {"max", "sum", "noisy_or"} <= set(COMBINERS)

    def test_max(self) -> None:
        assert combine("max", [0.2, 0.9, 0.5]) == 0.9

    def test_sum(self) -> None:
        assert combine("sum", [0.2, 0.3]) == pytest.approx(0.5)

    def test_noisy_or_accumulates_several_weak_signals(self) -> None:
        assert combine("noisy_or", [0.5, 0.5]) == pytest.approx(0.75)

    def test_noisy_or_is_bounded_above_by_1(self) -> None:
        assert combine("noisy_or", [0.9] * 20) <= 1.0

    def test_noisy_or_clamps_out_of_range_components(self) -> None:
        """A component outside [0,1] makes the result meaningless, so clamp
        rather than let it through."""
        assert combine("noisy_or", [1.5]) == pytest.approx(1.0)
        assert combine("noisy_or", [-3.0]) == pytest.approx(0.0)

    def test_empty_input(self) -> None:
        assert combine("max", []) == 0.0
        assert combine("noisy_or", []) == 0.0

    def test_an_unknown_strategy_raises_immediately(self) -> None:
        with pytest.raises(KeyError, match="mean"):
            combine("mean", [0.5])


class TestTermAndUnit:
    def test_an_empty_term_raises(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            Term("")

    def test_an_empty_unit_name_raises(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            QueryUnit("")

    def test_the_default_combine_strategy_is_noisy_or(self) -> None:
        assert QueryUnit("io").combine == "noisy_or"


class TestLexicalSatisfier:
    def test_an_exact_hit(self) -> None:
        ctx = make_context(postings={"buf": [Posting(1, IndexField.NAME)]})
        assert 1 in LexicalSatisfier(terms=(Term("buf"),)).hits("io", ctx)

    def test_matches_the_projects_spelling_through_the_expansion_table(self) -> None:
        ctx = make_context(
            postings={"buf": [Posting(1, IndexField.NAME)]},
            expansion={"buffer": [Expansion("buf", 0.91, "prefix")]},
        )
        hits = LexicalSatisfier(terms=(Term("buffer"),)).hits("perf", ctx)
        assert "buf←buffer(prefix)" in hits[1][0].detail

    def test_derived_term_keeps_grounding_evidence(self) -> None:
        ctx = make_context(
            postings={"buf": [Posting(1, IndexField.NAME)]},
            expansion={"buffer": [Expansion("buf", 0.91, "prefix")]},
        )

        hits = LexicalSatisfier(terms=(Term("buffer", source="derived"),)).hits("perf", ctx)

        assert hits[1][0].detail == "buf←buffer(prefix)"

    def test_an_explicitly_requested_generic_word_is_down_weighted_not_excluded(self) -> None:
        """The ICF floor governs expansions only.

        A word the caller picked explicitly must not be silently dropped -- on
        netty exactly that discarded `buf` (on 15.4% of symbols) when the
        query was about buffers.
        """
        ctx = make_context(
            postings={
                "get": [Posting(i, IndexField.NAME) for i in range(207)],
                "login": [Posting(500, IndexField.NAME)],
            },
            elements=[make_element(i, f"m{i}") for i in range(600)],
        )
        generic = LexicalSatisfier(terms=(Term("get"),)).hits("io", ctx)
        rare = LexicalSatisfier(terms=(Term("login"),)).hits("io", ctx)
        assert generic, "an explicitly requested word must not be dropped"
        assert generic[0][0].score < rare[500][0].score, "but ICF should down-weight it"

    def test_a_generic_word_from_an_expansion_is_still_blocked(self) -> None:
        """The floor exists to block expansion noise; that must survive."""
        ctx = make_context(
            postings={"get": [Posting(i, IndexField.NAME) for i in range(207)]},
            expansion={"fetch": [Expansion("get", 0.9, "prefix")]},
            elements=[make_element(i, f"m{i}") for i in range(300)],
        )
        assert LexicalSatisfier(terms=(Term("fetch"),)).hits("io", ctx) == {}

    def test_a_rare_word_is_not_blocked(self) -> None:
        ctx = make_context(
            postings={"login": [Posting(1, IndexField.NAME)]},
        )
        assert LexicalSatisfier(terms=(Term("login"),)).hits("auth", ctx) != {}

    def test_the_search_can_be_restricted_to_certain_fields(self) -> None:
        ctx = make_context(
            postings={"buf": [Posting(1, IndexField.NAME), Posting(2, IndexField.DOC)]},
            elements=[make_element(1, "a"), make_element(2, "b")],
        )
        hits = LexicalSatisfier(terms=(Term("buf"),), fields=(IndexField.NAME,)).hits("io", ctx)
        assert set(hits) == {1}

    def test_a_name_hit_scores_higher_than_a_doc_hit(self) -> None:
        ctx = make_context(
            postings={"buf": [Posting(1, IndexField.NAME), Posting(2, IndexField.DOC)]},
            elements=[make_element(1, "a"), make_element(2, "b")],
        )
        hits = LexicalSatisfier(terms=(Term("buf"),)).hits("io", ctx)
        assert hits[1][0].score > hits[2][0].score

    def test_scoring_multiplies_so_every_hop_discounts(self) -> None:
        ctx = make_context(
            postings={"buf": [Posting(1, IndexField.DOC)]},
            expansion={"buffer": [Expansion("buf", 0.5, "prefix")]},
        )
        info = ctx.postings.term_info("buf")
        assert info is not None
        hit = LexicalSatisfier(terms=(Term("buffer", weight=0.5),), weight=0.5).hits("io", ctx)[1][
            0
        ]
        expected = 0.5 * 0.5 * 0.5 * ctx.field_weights.weight(IndexField.DOC) * info.icf_ratio
        assert hit.score == pytest.approx(expected)

    def test_a_word_with_no_hits_produces_no_evidence(self) -> None:
        assert LexicalSatisfier(terms=(Term("nope"),)).hits("io", make_context()) == {}


class TestAnnotationSatisfier:
    def test_searches_only_the_annotation_fields(self) -> None:
        ctx = make_context(
            postings={"cache": [Posting(1, IndexField.NAME), Posting(2, IndexField.ANNOTATION)]},
            elements=[make_element(1, "a"), make_element(2, "b")],
        )
        assert set(AnnotationSatisfier(units=(Term("cache"),)).hits("perf", ctx)) == {2}

    def test_searches_annotation_arguments_too(self) -> None:
        """The permission string in `@PreAuthorize` and the description in
        `@Schema` both live in the arguments."""
        ctx = make_context(postings={"query": [Posting(1, IndexField.ANNOTATION_ARG)]})
        assert set(AnnotationSatisfier(units=(Term("query"),)).hits("q", ctx)) == {1}

    def test_meta_annotation_expansion_is_fact_not_estimate(self) -> None:
        ctx = make_context(
            postings={"@GetMapping": [Posting(1, IndexField.ANNOTATION)]},
            expansion={"@RequestMapping": [Expansion("@GetMapping", 1.0, "meta")]},
        )
        hits = AnnotationSatisfier(names=("@RequestMapping",)).hits("http", ctx)
        assert "meta" in hits[1][0].detail

    def test_the_default_weight_exceeds_the_lexical_one(self) -> None:
        assert AnnotationSatisfier().weight > LexicalSatisfier(terms=()).weight


class TestModifierSatisfier:
    def test_searches_only_the_modifier_field(self) -> None:
        ctx = make_context(
            postings={"native": [Posting(1, IndexField.MODIFIER), Posting(2, IndexField.NAME)]},
            elements=[make_element(1, "a"), make_element(2, "nativeHelper")],
        )
        assert set(ModifierSatisfier(modifiers=("native",)).hits("perf", ctx)) == {1}

    def test_a_generic_modifier_is_down_weighted_not_excluded(self) -> None:
        """Nearly every symbol is `public`, so it barely discriminates -- but
        if it was asked for explicitly it should still come back."""
        ctx = make_context(
            postings={
                "public": [Posting(i, IndexField.MODIFIER) for i in range(1600)],
                "native": [Posting(1, IndexField.MODIFIER)],
            },
            elements=[make_element(i, f"m{i}") for i in range(1700)],
        )
        common = ModifierSatisfier(modifiers=("public",)).hits("u", ctx)
        rare = ModifierSatisfier(modifiers=("native",)).hits("u", ctx)
        assert common
        assert common[0][0].score < rare[1][0].score

    def test_a_rare_modifier_is_kept(self) -> None:
        ctx = make_context(postings={"volatile": [Posting(1, IndexField.MODIFIER)]})
        assert ModifierSatisfier(modifiers=("volatile",)).hits("u", ctx) != {}

    def test_the_weight_sits_between_lexical_and_annotation(self) -> None:
        """A modifier is a language-level fact and beats lexical matching,
        but an annotation carries more specific meaning."""
        assert (
            LexicalSatisfier(terms=()).weight
            < ModifierSatisfier().weight
            < AnnotationSatisfier().weight
        )


class TestEvalUnit:
    def _ctx(self) -> EvalContext:
        return make_context(
            postings={
                "buf": [Posting(1, IndexField.NAME)],
                "cache": [Posting(1, IndexField.ANNOTATION)],
            },
            expansion={"buffer": [Expansion("buf", 0.91, "prefix")]},
        )

    def test_the_resulting_fragment_has_no_edges(self) -> None:
        unit = QueryUnit("perf", satisfiers=(LexicalSatisfier(terms=(Term("buf"),)),))
        assert eval_unit(unit, self._ctx()).edges == {}

    def test_the_nodes_themselves_come_from_the_symbol_store(self) -> None:
        unit = QueryUnit("perf", satisfiers=(LexicalSatisfier(terms=(Term("buf"),)),))
        assert eval_unit(unit, self._ctx()).nodes[1].name == "flushBuf"

    def test_no_raw_evidence_is_removed_only_a_summary_appended(self) -> None:
        unit = QueryUnit(
            "perf",
            satisfiers=(
                LexicalSatisfier(terms=(Term("buf"),)),
                AnnotationSatisfier(units=(Term("cache"),)),
            ),
        )
        signals = [h.signal for h in eval_unit(unit, self._ctx()).evidence_for(1).unit_hits]
        assert signals.count("lexical") == 1
        assert signals.count("annotation") == 1
        assert signals.count(Evidence.COMBINED) == 1

    def test_the_summary_is_not_double_counted_in_the_total(self) -> None:
        """Regression: `scores` once added the summary into the sum too,
        doubling the score."""
        unit = QueryUnit("perf", satisfiers=(AnnotationSatisfier(units=(Term("cache"),)),))
        evidence = eval_unit(unit, self._ctx()).evidence_for(1)
        annotation_hit = next(h for h in evidence.unit_hits if h.signal == "annotation")
        assert evidence.scores["perf"] == pytest.approx(annotation_hit.score)

    def test_several_signals_combine_above_a_single_one(self) -> None:
        lexical_only = QueryUnit("perf", satisfiers=(LexicalSatisfier(terms=(Term("buf"),)),))
        both = QueryUnit(
            "perf",
            satisfiers=(
                LexicalSatisfier(terms=(Term("buf"),)),
                AnnotationSatisfier(units=(Term("cache"),)),
            ),
        )
        ctx = self._ctx()
        one = eval_unit(lexical_only, ctx).evidence_for(1).scores["perf"]
        two = eval_unit(both, ctx).evidence_for(1).scores["perf"]
        assert two > one

    def test_the_combine_strategy_is_selectable(self) -> None:
        satisfiers = (
            LexicalSatisfier(terms=(Term("buf"),)),
            AnnotationSatisfier(units=(Term("cache"),)),
        )
        ctx = self._ctx()
        by_max = eval_unit(QueryUnit("p", satisfiers=satisfiers, combine="max"), ctx)
        by_or = eval_unit(QueryUnit("p", satisfiers=satisfiers, combine="noisy_or"), ctx)
        assert by_or.evidence_for(1).scores["p"] > by_max.evidence_for(1).scores["p"]

    def test_no_hits_returns_an_empty_fragment(self) -> None:
        unit = QueryUnit("perf", satisfiers=(LexicalSatisfier(terms=(Term("nope"),)),))
        assert not eval_unit(unit, self._ctx())

    def test_an_id_absent_from_the_symbol_store_stays_out(self) -> None:
        ctx = make_context(
            postings={"buf": [Posting(999, IndexField.NAME)]},
            elements=[make_element(1, "flushBuf")],
        )
        unit = QueryUnit("perf", satisfiers=(LexicalSatisfier(terms=(Term("buf"),)),))
        assert not eval_unit(unit, ctx)

    def test_a_satisfier_of_the_wrong_type_raises(self) -> None:
        unit = QueryUnit("perf", satisfiers=("not a satisfier",))
        with pytest.raises(TypeError, match="satisfier of the wrong type"):
            eval_unit(unit, self._ctx())

    def test_the_resulting_fragment_takes_part_in_the_algebra(self) -> None:
        """Evaluating a unit gives a Frag directly, with no conversion."""
        left = eval_unit(
            QueryUnit("a", satisfiers=(LexicalSatisfier(terms=(Term("buf"),)),)), self._ctx()
        )
        right = eval_unit(
            QueryUnit("b", satisfiers=(AnnotationSatisfier(units=(Term("cache"),)),)), self._ctx()
        )
        merged = left & right
        units = {h.unit for h in merged.evidence_for(1).unit_hits}
        assert {"a", "b"} <= units


class TestEvidenceSerialisable:
    """The design doc requires evidence to be serialisable -- it is written
    to disk for people to inspect afterwards."""

    def test_evidence_round_trips_through_json(self) -> None:
        hit = UnitHit(unit="io", signal="lexical", detail="buf", field="name", score=0.5)
        restored = UnitHit(**json.loads(json.dumps(asdict(hit))))
        assert restored == hit

    def test_evidence_with_locations_round_trips_too(self) -> None:
        hit = UnitHit(unit="io", signal="lexical", detail="buf", span=(3, 9))
        payload = json.loads(json.dumps(asdict(hit)))
        assert UnitHit(**{**payload, "span": tuple(payload["span"])}) == hit

    def test_a_whole_evidence_record_serialises(self) -> None:
        ev = Evidence(
            unit_hits=(UnitHit("io", "lexical", "buf"),),
            verdicts=(Verdict("llm", "yes", "it is the login entry point"),),
        )
        assert (
            json.loads(json.dumps(asdict(ev)))["verdicts"][0]["reason"]
            == "it is the login entry point"
        )
