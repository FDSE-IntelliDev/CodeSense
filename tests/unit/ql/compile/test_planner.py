"""Unit tests for the planner and cost estimation.

The focus is **ordering** -- the one place the compiler optimises.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from codesense.ql import Edge, Element, Frag, IndexField
from codesense.ql.compile import (
    Boost,
    EvalUnit,
    Intent,
    Narrow,
    ProjectTarget,
    QuerySpec,
    estimate_unit,
    plan,
)
from codesense.ql.compile.partition import partition
from codesense.ql.compile.plan import State
from codesense.ql.compile.spec import normalise_hops, normalise_kinds
from codesense.ql.compile.validate import relation_lift, validate_relations
from codesense.ql.context import EvalContext
from codesense.ql.store import (
    InMemoryEdgeStore,
    InMemoryExpansionTable,
    InMemoryPostingIndex,
    InMemorySymbolStore,
    Posting,
)

TOTAL = 1000


def make_context(
    postings: dict[str, int | Sequence[int]],
    edges: tuple[Edge, ...] = (),
    *,
    file_count: int = 0,
    declaration_count: int | None = None,
) -> EvalContext:
    """``postings`` gives how many symbols each term matches."""
    built: dict[str, list[Posting]] = {}
    for term, count_or_ids in postings.items():
        symbol_ids = range(1, count_or_ids + 1) if isinstance(count_or_ids, int) else count_or_ids
        built[term] = [Posting(i, IndexField.NAME) for i in symbol_ids]
    declarations = [
        Element(symbol_id=i, name=f"s{i}", kind="method", file="A.java", span=(1, 2))
        for i in range(1, TOTAL + 1)
    ]
    files = [
        Element(
            symbol_id=TOTAL + i,
            name=f"F{i}.java",
            kind="file",
            file=f"F{i}.java",
            span=(1, 2),
        )
        for i in range(1, file_count + 1)
    ]
    population = TOTAL if declaration_count is None else declaration_count
    return EvalContext(
        symbols=InMemorySymbolStore([*declarations, *files]),
        postings=InMemoryPostingIndex(built, total_symbols=population),
        expansion=InMemoryExpansionTable({}),
        edges=InMemoryEdgeStore(edges),
        declaration_count=declaration_count,
    )


def spec(**overrides: object) -> QuerySpec:
    payload: dict[str, object] = {
        "query": "q",
        "units": [
            {"name": "wide", "terms": ["common"]},
            {"name": "narrow", "terms": ["rare"]},
        ],
    }
    payload.update(overrides)
    return QuerySpec.from_dict(payload)


class TestEstimate:
    def test_size_can_be_estimated_without_executing(self) -> None:
        """From the df in the inverted index -- the premise that makes
        ordering optimisable."""
        ctx = make_context({"rare": 10})
        unit = spec().units[1]
        assert estimate_unit(unit, ctx).rows == pytest.approx(10, abs=1)

    def test_several_terms_union_under_independence(self) -> None:
        ctx = make_context({"a": 100, "b": 100})
        unit = QuerySpec.from_dict(
            {"query": "q", "units": [{"name": "u", "terms": ["a", "b"]}]}
        ).units[0]
        # 1000·(1 − 0.9²) = 190
        assert 150 < estimate_unit(unit, ctx).rows < 220

    def test_a_term_with_no_hits_contributes_no_size(self) -> None:
        ctx = make_context({})
        unit = spec().units[0]
        assert estimate_unit(unit, ctx).rows == 0

    def test_context_without_an_injected_declaration_count_counts_all_elements(self) -> None:
        assert make_context({}).population == TOTAL

    def test_union_estimate_uses_declaration_population(self) -> None:
        ctx = make_context({"common": 100}, file_count=200, declaration_count=TOTAL)
        state = State(projected=500)
        assert EvalUnit(spec().units[0]).estimate(ctx, state).rows == 550

    def test_multi_term_estimate_uses_declaration_population(self) -> None:
        ctx = make_context({"a": 100, "b": 100}, file_count=200, declaration_count=TOTAL)
        unit = QuerySpec.from_dict(
            {"query": "q", "units": [{"name": "u", "terms": ["a", "b"]}]}
        ).units[0]

        assert estimate_unit(unit, ctx).rows == 190

    def test_partition_hub_ratio_uses_declaration_population(self) -> None:
        ctx = make_context(
            {
                "hub": tuple(range(1, 261)),
                "a1": tuple(range(1, 31)),
                "a2": tuple(range(1, 31)),
                "b1": tuple(range(100, 131)),
                "b2": tuple(range(100, 131)),
            },
            file_count=200,
            declaration_count=TOTAL,
        )

        assert len(partition(["hub", "a1", "a2", "b1", "b2"], ctx)) == 2


class TestOrdering:
    def test_the_narrowest_unit_goes_first(self) -> None:
        """The sooner the working set shrinks, the cheaper every later
        step."""
        ctx = make_context({"common": 400, "rare": 10})
        steps = plan(spec(), ctx).steps
        assert isinstance(steps[0], EvalUnit)
        assert steps[0].unit.name == "narrow"
        assert steps[0].seed

    def test_only_a_unit_matching_nearly_the_whole_table_is_dropped(self) -> None:
        """The threshold is high for a reason: 0.5 dropped the only unit
        containing the answer."""
        ctx = make_context({"common": 980, "rare": 10})
        planned = plan(spec(), ctx)
        assert [s.unit.name for s in planned.steps if isinstance(s, EvalUnit)] == ["narrow"]
        assert any("dropping unit" in why for why in planned.reasoning)

    def test_a_merely_broad_unit_is_kept_but_down_weighted(self) -> None:
        """Covering half is not useless -- down-weighting suffices, and
        dropping costs the answers."""
        ctx = make_context({"common": 500, "rare": 10})
        names = [s.unit.name for s in plan(spec(), ctx).steps if isinstance(s, EvalUnit)]
        assert set(names) == {"narrow", "wide"}

    def test_the_narrowest_is_kept_when_all_are_too_broad(self) -> None:
        """All being broad is no reason to emit an empty plan."""
        ctx = make_context({"common": 900, "rare": 800})
        planned = plan(spec(), ctx)
        assert any(isinstance(s, EvalUnit) for s in planned.steps)

    def test_file_nodes_do_not_dilute_planner_specificity(self) -> None:
        ctx = make_context({"common": 900, "rare": 10}, file_count=200, declaration_count=TOTAL)
        planned = plan(spec(), ctx)

        assert ctx.symbols.count() == 1200
        assert any("'wide' (est. 900 rows, 90%" in why for why in planned.reasoning)

    def test_intent_always_comes_last(self) -> None:
        ctx = make_context({"common": 100, "rare": 10})
        steps = plan(spec(concept="decide whether it is relevant"), ctx).steps
        assert isinstance(steps[-1], Intent)

    def test_narrowing_happens_before_intent(self) -> None:
        """It costs thousands of lookups, so its input must be capped
        first."""
        ctx = make_context({"common": 100, "rare": 10})
        steps = plan(spec(concept="judge"), ctx).steps
        assert isinstance(steps[-2], Narrow)
        assert steps[-2].limit is not None

    def test_no_intent_step_without_a_concept(self) -> None:
        ctx = make_context({"common": 100, "rare": 10})
        assert not any(isinstance(s, Intent) for s in plan(spec(), ctx).steps)

    def test_file_target_projects_before_the_public_result_limit(self) -> None:
        ctx = make_context(
            {"rare": (1,)},
            (Edge(1, TOTAL + 1, "in_file"),),
            file_count=1,
        )

        steps = plan(spec(target=["file"], limit=1), ctx).steps

        assert isinstance(steps[-2], ProjectTarget)
        assert isinstance(steps[-1], Narrow)
        assert steps[-1].limit == 1

    def test_file_target_follows_intent_and_precedes_the_public_limit(self) -> None:
        ctx = make_context(
            {"rare": (1,)},
            (Edge(1, TOTAL + 1, "in_file"),),
            file_count=1,
        )

        steps = plan(spec(target="file", concept="judge", limit=1), ctx).steps

        assert isinstance(steps[-4], Narrow)
        assert steps[-4].limit == 60
        assert isinstance(steps[-3], Intent)
        assert isinstance(steps[-2], ProjectTarget)
        assert isinstance(steps[-1], Narrow)
        assert steps[-1].limit == 1

    def test_file_target_execution_returns_only_files(self) -> None:
        ctx = make_context(
            {"rare": (1,)},
            (Edge(1, TOTAL + 1, "in_file"),),
            file_count=1,
        )

        current = plan(spec(target=["file"]), ctx).run(ctx).current

        assert {element.kind for element in current} == {"file"}


class TestGraphDirection:
    def test_starts_from_the_smaller_side(self) -> None:
        """`hop` costs scale linearly with the number of seeds."""
        ctx = make_context({"common": 400, "rare": 10}, (Edge(1, 2, "calls"),))
        planned = plan(spec(graph=[{"src": "wide", "dst": "narrow"}]), ctx)
        boosts = [s for s in planned.steps if isinstance(s, Boost)]
        assert boosts[0].src_name == "narrow"
        assert any("reversed" in why for why in planned.reasoning)

    def test_the_direction_is_kept_when_the_sides_are_close_in_size(self) -> None:
        ctx = make_context({"common": 100, "rare": 90}, (Edge(1, 2, "calls"),))
        planned = plan(spec(graph=[{"src": "wide", "dst": "narrow"}]), ctx)
        boosts = [s for s in planned.steps if isinstance(s, Boost)]
        assert boosts[0].src_name == "wide"

    @pytest.mark.parametrize(
        "edge",
        [
            ["imports"],
            ["in_file"],
            ["references"],
            ["calls", "imports"],
        ],
    )
    def test_typed_endpoint_roles_are_not_reversed_for_asymmetric_units(
        self, edge: list[str]
    ) -> None:
        ctx = make_context({"common": 400, "rare": 10})

        planned = plan(
            spec(graph=[{"src": "wide", "dst": "narrow", "edge": edge}]),
            ctx,
        )
        boost = next(step for step in planned.steps if isinstance(step, Boost))

        assert boost.src_name == "wide"
        assert boost.dst_name == "narrow"
        assert not any("constraint reversed" in why for why in planned.reasoning)

    def test_a_graph_constraint_is_skipped_when_its_unit_was_dropped(self) -> None:
        ctx = make_context({"common": 980, "rare": 10}, (Edge(1, 2, "calls"),))
        planned = plan(spec(graph=[{"src": "wide", "dst": "narrow"}]), ctx)
        assert not any(isinstance(s, Boost) for s in planned.steps)
        assert any("skipping constraint" in why for why in planned.reasoning)

    def test_relation_validation_uses_declaration_population(self) -> None:
        ctx = make_context(
            {"left": 1, "right": 2},
            (Edge(1, 2, "calls"),),
            file_count=200,
            declaration_count=TOTAL,
        )

        lift, _ = relation_lift(["left"], ["right"], ctx)

        assert lift == pytest.approx(500_000)


class TestExecution:
    @staticmethod
    def _relation_context(edges: tuple[Edge, ...]) -> EvalContext:
        elements = [
            Element(1, "source", "method", "Source.java", (1, 2)),
            Element(2, "related", "method", "Related.java", (1, 2)),
            Element(3, "competitor", "method", "Other.java", (1, 2)),
            Element(10, "Source.java", "file", "Source.java", (1, 4)),
            Element(11, "Other.java", "file", "Other.java", (1, 4)),
        ]
        return EvalContext(
            symbols=InMemorySymbolStore(elements),
            postings=InMemoryPostingIndex({}, total_symbols=3),
            expansion=InMemoryExpansionTable({}),
            edges=InMemoryEdgeStore(edges),
            declaration_count=3,
        )

    @staticmethod
    def _frag(ctx: EvalContext, *symbol_ids: int) -> Frag:
        return Frag(nodes=ctx.symbols.get_many(symbol_ids))

    @staticmethod
    def _ranked_frag(ctx: EvalContext) -> Frag:
        from codesense.ql import Evidence, Frag, UnitHit

        return Frag(
            nodes=ctx.symbols.get_many((2, 3)),
            evidence={
                2: Evidence(unit_hits=(UnitHit("q", "combined", "", score=0.7),)),
                3: Evidence(unit_hits=(UnitHit("q", "combined", "", score=0.9),)),
            },
        )

    def test_imports_boosts_logical_destination_and_changes_narrow_ranking(self) -> None:
        ctx = self._relation_context((Edge(1, 10, "in_file"), Edge(10, 2, "imports")))
        accepted, rejected = validate_relations(
            [("src", "dst", ("imports",))],
            {"src": ["source"], "dst": ["related"]},
            EvalContext(
                symbols=ctx.symbols,
                postings=InMemoryPostingIndex(
                    {
                        "source": [Posting(1, IndexField.NAME)],
                        "related": [Posting(2, IndexField.NAME)],
                    },
                    total_symbols=3,
                ),
                expansion=ctx.expansion,
                edges=ctx.edges,
                declaration_count=3,
            ),
        )
        state = State(
            units={"src": self._frag(ctx, 1), "dst": self._frag(ctx, 2, 3)},
            current=self._ranked_frag(ctx),
        )

        assert accepted and not rejected
        Boost("src", "dst", edge=("imports",), hops=(1, 1)).apply(ctx, state)
        Narrow(limit=1).apply(ctx, state)

        assert state.boosted == {2}
        assert set(state.current.nodes) == {2}

    def test_in_file_maps_reached_file_back_to_destination_declaration(self) -> None:
        ctx = self._relation_context(
            (Edge(1, 10, "in_file"), Edge(2, 10, "in_file"), Edge(3, 11, "in_file"))
        )
        state = State(
            units={"src": self._frag(ctx, 1), "dst": self._frag(ctx, 2, 3)},
            current=self._frag(ctx, 2, 3),
        )

        Boost("src", "dst", edge=("in_file",), hops=(1, 1)).apply(ctx, state)

        assert state.boosted == {2}

    def test_import_derived_reference_starts_from_source_owner_file(self) -> None:
        ctx = self._relation_context((Edge(1, 10, "in_file"), Edge(10, 2, "references")))
        state = State(
            units={"src": self._frag(ctx, 1), "dst": self._frag(ctx, 2, 3)},
            current=self._frag(ctx, 2, 3),
        )

        Boost("src", "dst", edge=("references",), hops=(1, 1)).apply(ctx, state)

        assert state.boosted == {2}

    def test_mixed_calls_and_imports_union_both_endpoint_roles(self) -> None:
        ctx = self._relation_context(
            (
                Edge(1, 10, "in_file"),
                Edge(1, 2, "calls"),
                Edge(10, 3, "imports"),
            )
        )
        state = State(
            units={"src": self._frag(ctx, 1), "dst": self._frag(ctx, 2, 3)},
            current=self._frag(ctx, 2, 3),
        )

        Boost("src", "dst", edge=("calls", "imports"), hops=(1, 1)).apply(ctx, state)

        assert state.boosted == {2, 3}

    def test_legacy_boost_keeps_historical_any_direction_semantics(self) -> None:
        ctx = self._relation_context((Edge(1, 2, "calls"),))
        state = State(
            units={"smaller": self._frag(ctx, 2), "larger": self._frag(ctx, 1)},
            current=self._frag(ctx, 1, 2),
        )

        Boost("smaller", "larger", edge=("calls",), hops=(1, 1)).apply(ctx, state)

        assert state.boosted == {1}

    def test_units_union_rather_than_intersect(self) -> None:
        """Intersection kills the answers -- units land on different
        elements."""
        ctx = make_context({"common": 100, "rare": 10})
        state = plan(spec(), ctx).run(ctx)
        assert len(state.current) > 10

    def test_a_dry_run_can_skip_intent(self) -> None:
        ctx = make_context({"common": 100, "rare": 10})
        state = plan(spec(concept="judge"), ctx).run(ctx, skip=(Intent,))
        assert any(t.skipped == "skipped (dry run)" for t in state.trace)

    def test_the_trace_records_prediction_and_reality(self) -> None:
        ctx = make_context({"common": 100, "rare": 10})
        state = plan(spec(), ctx).run(ctx)
        assert all(t.estimated >= 0 and t.actual >= 0 for t in state.trace if not t.skipped)

    def test_the_plan_can_be_printed(self) -> None:
        ctx = make_context({"common": 100, "rare": 10})
        text = plan(spec(concept="judge"), ctx).explain(ctx)
        assert "execution plan" in text and "intent" in text


class TestSpecRobustness:
    """The spec comes from an LLM, so the invariants must be enforced here."""

    def test_a_single_hop_value(self) -> None:
        assert normalise_hops([2]) == (1, 2)

    def test_hops_left_empty(self) -> None:
        assert normalise_hops([]) == (1, 2)

    def test_hops_given_as_an_integer(self) -> None:
        assert normalise_hops(3) == (3, 3)

    def test_reversed_hop_bounds_are_corrected(self) -> None:
        assert normalise_hops([3, 1]) == (1, 3)

    def test_class_maps_to_interface_and_enum(self) -> None:
        """When the model says class it means interfaces too -- without the
        mapping, results are silently lost."""
        assert "interface" in normalise_kinds(["class"])

    def test_method_maps_to_constructor(self) -> None:
        assert "constructor" in normalise_kinds(["method"])

    def test_an_unknown_kind_is_kept_as_is(self) -> None:
        assert normalise_kinds(["widget"]) == ("widget",)

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("file", ("file",)),
            (["FILE", "unknown"], ("file",)),
            (None, ()),
        ],
    )
    def test_target_accepts_only_the_supported_file_contract(
        self, raw: object, expected: tuple[str, ...]
    ) -> None:
        payload = {
            "query": "q",
            "units": [{"name": "a", "terms": ["x"]}],
        }
        if raw is not None:
            payload["target"] = raw

        assert QuerySpec.from_dict(payload).target == expected

    def test_duplicate_unit_names_raise(self) -> None:
        with pytest.raises(ValueError, match="duplicate"):
            QuerySpec.from_dict(
                {
                    "query": "q",
                    "units": [{"name": "a", "terms": ["x"]}, {"name": "a", "terms": ["y"]}],
                }
            )

    def test_a_graph_constraint_naming_an_unknown_unit_raises(self) -> None:
        with pytest.raises(ValueError, match="unknown units"):
            QuerySpec.from_dict(
                {
                    "query": "q",
                    "units": [{"name": "a", "terms": ["x"]}],
                    "graph": [{"src": "a", "dst": "missing"}],
                }
            )

    def test_a_unit_with_no_executable_condition_raises(self) -> None:
        with pytest.raises(ValueError, match="no executable condition"):
            QuerySpec.from_dict({"query": "q", "units": [{"name": "a"}]})

    def test_no_units_raises(self) -> None:
        with pytest.raises(ValueError, match="at least one unit"):
            QuerySpec.from_dict({"query": "q", "units": []})
