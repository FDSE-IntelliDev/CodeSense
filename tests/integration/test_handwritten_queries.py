"""Phase A acceptance: hand-written QL across query types, testing whether the
operator set suffices.

``docs/design/07-mapping-to-current.md`` puts it this way:

    **Hand-write** 3-5 pieces of QL across different query types and check
    whether the operator set suffices. Wherever it cannot be written is a
    missing operator -- this step is cheap (a few hundred lines) and stops
    the compiler being built on the wrong operator set.

So this file holds two kinds of test:

- ``TestQueryN`` -- what can be written, doubling as regression tests
- ``TestGaps`` -- what **cannot**, pinning each gap as an executable
  assertion, so that filling the capability makes the test fail and calls
  attention back here

The data comes from a real project (``scripts/build_ql_fixture.py`` extracts
it from youlai-boot) rather than an invented topology -- the sparsity and
naming habits of real data are what the operators will meet.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from codesense.ql import Edge, Element, Frag, IndexField
from codesense.ql.context import EvalContext
from codesense.ql.operators import degree, eval_unit, hop, intent, only, reach, top
from codesense.ql.satisfiers import AnnotationSatisfier, LexicalSatisfier
from codesense.ql.store import (
    InMemoryEdgeStore,
    InMemoryExpansionTable,
    InMemoryPostingIndex,
    InMemorySymbolStore,
    Posting,
)
from codesense.ql.unit import QueryUnit, Term

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "ql" / "mini_index.json"


@pytest.fixture(scope="module")
def ctx() -> EvalContext:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    elements = [
        Element(
            symbol_id=s["symbol_id"],
            name=s["name"],
            kind=s["kind"],
            file=s["file"],
            span=tuple(s["span"]),
            signature=s["signature"],
            container=s["container"],
            language=s["language"],
        )
        for s in payload["symbols"]
    ]
    return EvalContext(
        symbols=InMemorySymbolStore(elements),
        postings=InMemoryPostingIndex(
            {
                term: [Posting(p["symbol_id"], IndexField(p["field"]), p["tf"]) for p in entries]
                for term, entries in payload["postings"].items()
            },
            total_symbols=len(elements),
        ),
        expansion=InMemoryExpansionTable({}),
        edges=InMemoryEdgeStore(
            [
                Edge(
                    source_id=e["source_id"],
                    target_id=e["target_id"],
                    kind=e["kind"],
                    confidence=e["confidence"],
                    provenance=e["provenance"],
                )
                for e in payload["edges"]
            ]
        ),
    )


def names(frag: Frag) -> set[str]:
    return {e.name for e in frag}


def ranked(frag: Frag, unit: str) -> list[tuple[str, float]]:
    """Sort by score."""
    scored = [
        (frag.nodes[sid].name, frag.evidence_for(sid).scores.get(unit, 0.0)) for sid in frag.nodes
    ]
    return sorted(scored, key=lambda x: (-x[1], x[0]))


class TestQuery1LexicalOnly:
    """`find the token manager` -- pure entity location, lexical only."""

    def test_locates_the_token_manager(self, ctx: EvalContext) -> None:
        unit = QueryUnit(
            "token_manager",
            concept="the component managing tokens",
            satisfiers=(LexicalSatisfier(terms=(Term("token"), Term("manager")), weight=1.0),),
        )
        top = ranked(eval_unit(unit, ctx), "token_manager")
        assert top[0][0] == "RedisTokenManager"

    def test_matching_both_words_ranks_above_matching_one(self, ctx: EvalContext) -> None:
        unit = QueryUnit(
            "token_manager",
            satisfiers=(
                LexicalSatisfier(terms=(Term("token"),), weight=1.0),
                LexicalSatisfier(terms=(Term("manager"),), weight=1.0),
            ),
        )
        scores = dict(ranked(eval_unit(unit, ctx), "token_manager"))
        assert scores["RedisTokenManager"] > scores["generateToken"]


class TestQuery2GraphOnly:
    """`what methods does RedisTokenManager have` -- pure graph constraint,
    with no discriminating keyword.

    The design doc's own example: the existing three-stage pipeline would run
    a full lexical match first, and this query has no meaningful keyword at
    all.
    """

    def test_lists_a_classs_members_through_contains_edges(self, ctx: EvalContext) -> None:
        cls = _by_name(ctx, "RedisTokenManager", kind="class")
        members = reach(cls, ctx, edge="contains", hops=1)
        assert {"generateToken", "parseToken", "validateToken"} <= names(members)

    def test_materialising_contains_takes_orphans_from_most_to_almost_none(
        self, ctx: EvalContext
    ) -> None:
        """Chapter 10's claim: materialise contains and the orphan problem
        largely disappears.

        Measured, 33% to 98%. The remaining 2% (7 records) have an empty
        container in the symbol table -- an upstream parsing debt, not a
        shortfall in materialising contains.
        """
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        total = len(payload["symbols"])
        by_kind = {
            kind: {e["source_id"] for e in payload["edges"] if e["kind"] == kind}
            | {e["target_id"] for e in payload["edges"] if e["kind"] == kind}
            for kind in ("calls", "contains")
        }
        assert len(by_kind["calls"]) / total < 0.4
        assert len(by_kind["calls"] | by_kind["contains"]) / total > 0.95

    def test_with_calls_edges_alone_many_symbols_are_unreachable(self, ctx: EvalContext) -> None:
        """The converse: without materialising contains, the same query is
        useless."""
        cls = _by_name(ctx, "RedisTokenManager", kind="class")
        assert not reach(cls, ctx, edge="calls", hops=(1, 3))


class TestQuery3MultiUnitHop:
    """`token-related code calls redis-related code` -- two units connected
    through the graph.

    This cannot be expressed as "elements containing both token and redis
    keywords" -- such elements barely exist.
    """

    def test_the_call_paths_between_two_units(self, ctx: EvalContext) -> None:
        token = eval_unit(
            QueryUnit("token", satisfiers=(LexicalSatisfier(terms=(Term("token"),)),)), ctx
        )
        redis = eval_unit(
            QueryUnit("redis", satisfiers=(LexicalSatisfier(terms=(Term("redis"),)),)), ctx
        )
        linked = hop(token, redis, ctx, edge=["calls", "contains"], hops=(1, 2))
        assert linked.witnesses

    def test_the_result_keeps_the_paths_not_just_the_endpoints(self, ctx: EvalContext) -> None:
        token = eval_unit(
            QueryUnit("token", satisfiers=(LexicalSatisfier(terms=(Term("token"),)),)), ctx
        )
        redis = eval_unit(
            QueryUnit("redis", satisfiers=(LexicalSatisfier(terms=(Term("redis"),)),)), ctx
        )
        linked = hop(token, redis, ctx, edge=["calls", "contains"], hops=(1, 2))
        assert all(len(p.nodes) == len(p.edges) + 1 for p in linked.witnesses)

    def test_unit_evidence_from_both_ends_reaches_the_result(self, ctx: EvalContext) -> None:
        token = eval_unit(
            QueryUnit("token", satisfiers=(LexicalSatisfier(terms=(Term("token"),)),)), ctx
        )
        redis = eval_unit(
            QueryUnit("redis", satisfiers=(LexicalSatisfier(terms=(Term("redis"),)),)), ctx
        )
        linked = hop(token, redis, ctx, edge=["calls", "contains"], hops=(1, 2))
        units = {hit.unit for sid in linked.nodes for hit in linked.evidence_for(sid).unit_hits}
        assert {"token", "redis"} <= units


def _unit(name: str, *terms: str, weight: float = 1.0) -> QueryUnit:
    return QueryUnit(
        name, satisfiers=(LexicalSatisfier(terms=tuple(Term(t) for t in terms), weight=weight),)
    )


class TestQuery4Algebra:
    """`related to both token and redis` -- the fragment algebra."""

    def test_intersection(self, ctx: EvalContext) -> None:
        both = eval_unit(_unit("token", "token"), ctx) & eval_unit(_unit("redis", "redis"), ctx)
        assert "RedisTokenManager" in names(both)

    def test_difference(self, ctx: EvalContext) -> None:
        """Compared on symbol_id, not name -- different symbols may share a
        name."""
        token = eval_unit(_unit("token", "token"), ctx)
        redis = eval_unit(_unit("redis", "redis"), ctx)
        assert not set((token - redis).nodes) & set(redis.nodes)
        assert set((token - redis).nodes) < set(token.nodes)

    def test_intersection_merges_evidence_from_both_sides(self, ctx: EvalContext) -> None:
        both = eval_unit(_unit("token", "token"), ctx) & eval_unit(_unit("redis", "redis"), ctx)
        sid = next(sid for sid in both.nodes if both.nodes[sid].name == "RedisTokenManager")
        assert {"token", "redis"} <= {h.unit for h in both.evidence_for(sid).unit_hits}


class TestFindingIcfIsRelativeToTheIndex:
    """A finding: ICF is sensitive to what the index contains, so the same
    word is strong or weak depending on the index.

    Across the whole project `user` is 125/1718, an icf_ratio of 0.352; in
    this 375-symbol sample it is 65/375, or 0.296.

    Originally that difference made words get **silently dropped**; the
    benchmark magnified the design problem on netty past ignoring (`buf`, on
    15.4% of symbols, was dropped when the query was about buffers). Hence
    the change: the floor governs expansions only, and query terms are always
    kept but down-weighted by ICF.
    """

    def test_a_frequent_domain_word_scores_far_below_a_rare_one(self, ctx: EvalContext) -> None:
        """It is still findable -- the floor governs expansions only -- but
        ranks far down."""
        common = eval_unit(_unit("user", "user"), ctx)
        rare = eval_unit(_unit("token", "token"), ctx)
        assert common, "an explicitly requested word must not be dropped"
        best_common = max(common.evidence_for(s).scores["user"] for s in common.nodes)
        best_rare = max(rare.evidence_for(s).scores["token"] for s in rare.nodes)
        assert best_common < best_rare

    def test_expansions_remain_subject_to_the_floor(self, ctx: EvalContext) -> None:
        """Otherwise one noisy expansion floods the result with generic
        words."""
        from dataclasses import replace

        from codesense.ql.store import Expansion, InMemoryExpansionTable

        noisy = replace(
            ctx, expansion=InMemoryExpansionTable({"person": [Expansion("user", 0.9, "prefix")]})
        )
        assert not eval_unit(_unit("person", "person"), noisy)


class TestQuery6Intent:
    """`token-related methods that really do manage tokens` -- the most
    expensive operator last.

    The default null judge is used here, so this runs the **fallback path**:
    what it verifies is the orchestration itself -- that the candidates
    reaching `intent` have already been narrowed by the cheap constraints
    upstream. Real judging is verified in
    `tests/integration/test_llm_judge.py`.
    """

    def _narrowed(self, ctx: EvalContext) -> Frag:
        return top(only(eval_unit(_unit("token", "token"), ctx), kind="method"), 5, by="token")

    def test_intent_comes_only_after_narrowing(self, ctx: EvalContext) -> None:
        narrowed = self._narrowed(ctx)
        assert len(narrowed) <= 5
        assert len(intent(narrowed, "manages a token lifecycle", ctx, max_items=10)) <= 5

    def test_unnarrowed_candidates_raise_outright(self, ctx: EvalContext) -> None:
        """Handing an unnarrowed fragment to intent is an orchestration
        error, and money should not be what absorbs it."""
        wide = eval_unit(_unit("token", "token"), ctx)
        with pytest.raises(ValueError, match="max_items"):
            intent(wide, "manages a token lifecycle", ctx, max_items=3)

    def test_degrades_rather_than_failing_when_no_LLM_is_configured(self, ctx: EvalContext) -> None:
        narrowed = self._narrowed(ctx)
        result = intent(narrowed, "manages a token lifecycle", ctx, max_items=10)
        assert len(result) == len(narrowed)
        assert all(result.evidence_for(s).verdicts[0].source == "fallback" for s in result.nodes)


class TestQuery5Narrowing:
    """`the 5 most relevant token-related methods` -- the narrowing
    operators.

    Gaps 4 and 5 are filled here: `only` and `top` are implemented.
    """

    def test_narrows_by_element_kind(self, ctx: EvalContext) -> None:
        found = eval_unit(_unit("token", "token"), ctx)
        methods = only(found, kind="method")
        assert methods and len(methods) < len(found)
        assert {e.kind for e in methods} == {"method"}

    def test_takes_the_top_5(self, ctx: EvalContext) -> None:
        found = only(eval_unit(_unit("token", "token"), ctx), kind="method")
        assert len(top(found, 5, by="token")) == 5

    def test_top_k_preserves_evidence(self, ctx: EvalContext) -> None:
        found = eval_unit(_unit("token", "token"), ctx)
        best = top(found, 3, by="token")
        assert all(best.evidence_for(sid).unit_hits for sid in best.nodes)

    def test_entry_points_are_selected_by_degree(self, ctx: EvalContext) -> None:
        """`the ones nobody calls` -- in-degree 0."""
        found = eval_unit(_unit("token", "token"), ctx)
        entries = degree(found, ctx, edge="calls", max_in=0)
        assert entries and len(entries) < len(found)

    def test_the_operators_compose(self, ctx: EvalContext) -> None:
        """This is what a script should look like: a chain of Frag -> Frag."""
        result = top(
            only(eval_unit(_unit("token", "token"), ctx), kind="method"),
            3,
            by="token",
        )
        assert len(result) == 3


class TestGaps:
    """Where it cannot be written. **Each one is a capability still owed.**"""

    def test_gap1_the_real_index_has_no_annotations_yet(self, ctx: EvalContext) -> None:
        """Extraction is implemented (`codesense.lang.java.annotations`) but
        **has not been fed into the real index**.

        Two things block it: the sample project's Java source is not on this
        machine, and the index build does not yet call the extractor.
        Annotation queries themselves work -- see
        `tests/integration/test_annotation_queries.py`.
        """
        unit = QueryUnit(
            "transactional", satisfiers=(AnnotationSatisfier(names=("@Transactional",)),)
        )
        assert not eval_unit(unit, ctx), "annotations are indexed now; delete this gap assertion"

    def test_gap2_there_are_no_field_read_write_edges(self, ctx: EvalContext) -> None:
        """`who modifies this field` cannot be written -- there are no reads
        or writes edges."""
        field = _any_of_kind(ctx, "variable")
        assert not reach(field, ctx, edge="writes", direction="backward"), (
            "writes edges exist now; delete this gap assertion"
        )

    def test_gap3_the_real_index_has_no_modifiers_yet(self, ctx: EvalContext) -> None:
        """Extraction is implemented (`JavaDeclarationScanner`) but **has not
        been fed into the real index**.

        Blocked on the same things as annotations: the source is not on this
        machine and the index build does not yet call the scanner.
        """
        assert all(not e.modifiers for e in ctx.symbols.get_many(range(1, 200)).values()), (
            "modifiers exist now; delete this gap assertion"
        )

    def test_gap4_the_real_index_has_no_dataflow_edges_yet(self, ctx: EvalContext) -> None:
        """`where does this parameter's value come from` cannot be written --
        there are no flows_to edges.

        It is the only item needing **new analysis capability** (chapter 10,
        section 6); the rest are rearrangements of data already held.
        """
        field = _any_of_kind(ctx, "variable")
        assert not reach(field, ctx, edge="flows_to", direction="backward"), (
            "flows_to edges exist now; delete this gap assertion"
        )


def _any_of_kind(ctx: EvalContext, kind: str) -> Frag:
    found = {
        sid: element
        for sid, element in ctx.symbols.get_many(range(1, 2000)).items()
        if element.kind == kind
    }
    assert found, f"no {kind} in the fixture"
    return Frag(nodes=dict(sorted(found.items())[:1]))


def _by_name(ctx: EvalContext, name: str, *, kind: str) -> Frag:
    found = {
        sid: element
        for sid, element in ctx.symbols.get_many(range(1, 2000)).items()
        if element.name == name and element.kind == kind
    }
    assert found, f"no {kind} {name} in the fixture"
    return Frag(nodes=found)
