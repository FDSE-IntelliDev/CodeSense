"""Unit tests for the meta-annotation table. Pure data derivation, needing no
third-party dependency."""

from __future__ import annotations

from codesense.lang.java import META_ANNOTATIONS, expansions_for, meta_expansion_table


class TestExpansionsFor:
    def test_direct_relations(self) -> None:
        assert "@RequestMapping" in expansions_for("@GetMapping")

    def test_a_direct_relation_scores_1_it_is_fact_not_estimate(self) -> None:
        assert expansions_for("@GetMapping")["@RequestMapping"] == 1.0

    def test_transitive_relations(self) -> None:
        """`@Service` → `@Component`，`@RestController` → `@Component`。"""
        assert "@Component" in expansions_for("@Service")

    def test_an_unknown_annotation_returns_nothing(self) -> None:
        assert expansions_for("@ProjectSpecific") == {}

    def test_does_not_include_itself(self) -> None:
        assert "@GetMapping" not in expansions_for("@GetMapping")

    def test_depth_is_capped_against_cycles_and_over_propagation(self) -> None:
        """`@SpringBootApplication` to `@Configuration` to `@Component` is
        two hops."""
        shallow = expansions_for("@SpringBootApplication", max_depth=1)
        deep = expansions_for("@SpringBootApplication", max_depth=3)
        assert "@Component" not in shallow
        assert "@Component" in deep

    def test_transitive_relations_are_weaker_than_direct_ones(self) -> None:
        deep = expansions_for("@SpringBootApplication", max_depth=3)
        assert deep["@Component"] < deep["@Configuration"]


class TestMetaExpansionTable:
    def test_the_direction_is_general_to_specific(self) -> None:
        """A query asking for "all HTTP entry points" must expand to
        GetMapping / PostMapping."""
        table = meta_expansion_table()
        targets = {name for name, _, _ in table["@RequestMapping"]}
        assert {"@GetMapping", "@PostMapping", "@DeleteMapping"} <= targets

    def test_component_expands_to_every_stereotype(self) -> None:
        targets = {name for name, _, _ in meta_expansion_table()["@Component"]}
        assert {"@Service", "@Repository", "@Controller", "@RestController"} <= targets

    def test_the_reason_is_labelled_meta(self) -> None:
        assert all(
            reason == "meta"
            for entries in meta_expansion_table().values()
            for _, _, reason in entries
        )

    def test_ordered_by_descending_score(self) -> None:
        for entries in meta_expansion_table().values():
            assert [s for _, s, _ in entries] == sorted((s for _, s, _ in entries), reverse=True)

    def test_the_table_has_no_self_loops(self) -> None:
        for key, entries in meta_expansion_table().items():
            assert key not in {name for name, _, _ in entries}

    def test_the_declaration_table_itself_has_no_self_loops(self) -> None:
        assert all(name not in parents for name, parents in META_ANNOTATIONS.items())
