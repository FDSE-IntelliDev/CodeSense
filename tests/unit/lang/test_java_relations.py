from __future__ import annotations

from codesense.lang import AnnotationUse, Declaration, IndexedDeclaration, RelationContext
from codesense.lang.java.relations import derive_java_relations


def indexed(
    symbol_id: int,
    name: str,
    kind: str,
    *,
    package: str = "demo",
    supertypes: tuple[str, ...] = (),
) -> IndexedDeclaration:
    qualified = f"{package}.{name}" if package else name
    return IndexedDeclaration(
        symbol_id,
        f"{name}.java",
        Declaration(
            name,
            kind,
            line=symbol_id,
            column=2,
            end_line=symbol_id + 1,
            qualified_name=qualified,
            supertypes=supertypes,
        ),
    )


def method(
    symbol_id: int,
    owner: str,
    name: str,
    parameters: tuple[str, ...],
    *,
    package: str = "demo",
    annotations: tuple[AnnotationUse, ...] = (),
    modifiers: frozenset[str] = frozenset(),
) -> IndexedDeclaration:
    return IndexedDeclaration(
        symbol_id,
        f"{owner}.java",
        Declaration(
            name,
            "method",
            line=symbol_id,
            end_line=symbol_id,
            container=owner,
            qualified_name=f"{package}.{owner}.{name}",
            parameter_types=parameters,
            annotations=annotations,
            modifiers=modifiers,
        ),
    )


def test_classifies_type_relations_from_source_and_target_kinds() -> None:
    context = RelationContext(
        (
            indexed(1, "Base", "class"),
            indexed(2, "Port", "interface"),
            indexed(3, "Child", "class", supertypes=("Base", "Port")),
            indexed(4, "SubPort", "interface", supertypes=("Port",)),
            indexed(5, "Mode", "enum", supertypes=("Port",)),
        )
    )

    batch = derive_java_relations(context)

    assert {(fact.source_id, fact.target_id, fact.kind) for fact in batch.facts} == {
        (3, 1, "extends"),
        (3, 2, "implements"),
        (4, 2, "extends"),
        (5, 2, "implements"),
    }


def test_same_package_wins_but_global_simple_name_ambiguity_is_skipped() -> None:
    same_package = RelationContext(
        (
            indexed(1, "Base", "class", package="a"),
            indexed(2, "Base", "class", package="b"),
            indexed(3, "Child", "class", package="b", supertypes=("Base",)),
        )
    )
    ambiguous = RelationContext(
        (
            indexed(1, "Base", "class", package="a"),
            indexed(2, "Base", "class", package="b"),
            indexed(3, "Child", "class", package="c", supertypes=("Base",)),
        )
    )

    facts = derive_java_relations(same_package).facts
    assert {(fact.source_id, fact.target_id) for fact in facts} == {(3, 2)}
    assert facts[0].confidence == 0.8
    assert facts[0].provenance == "java_supertypes_same_package"

    result = derive_java_relations(ambiguous)
    assert result.facts == ()
    assert result.diagnostics.ambiguous == 1


def test_unique_global_parent_keeps_source_site_and_lower_confidence() -> None:
    context = RelationContext(
        (
            indexed(1, "Base", "class", package="a"),
            indexed(3, "Child", "class", package="b", supertypes=("Base",)),
        )
    )

    fact = derive_java_relations(context).facts[0]

    assert fact.site == (3, 2)
    assert fact.confidence == 0.7
    assert fact.provenance == "java_supertypes_simple"


def test_unresolved_and_unsupported_type_pairs_are_diagnosed() -> None:
    context = RelationContext(
        (
            indexed(1, "Parent", "enum"),
            indexed(2, "Child", "class", supertypes=("Parent", "External")),
        )
    )

    result = derive_java_relations(context)

    assert result.facts == ()
    assert result.diagnostics.unresolved == 1
    assert result.diagnostics.skipped == 1


def test_method_can_override_a_class_and_implement_an_interface() -> None:
    context = RelationContext(
        (
            indexed(1, "Base", "class"),
            indexed(2, "Port", "interface"),
            indexed(3, "Child", "class", supertypes=("Base", "Port")),
            method(10, "Base", "run", ("String",)),
            method(11, "Port", "run", ("String",)),
            method(
                12,
                "Child",
                "run",
                ("String",),
                annotations=(AnnotationUse("Override"),),
            ),
        )
    )

    facts = derive_java_relations(context).facts

    assert {(fact.source_id, fact.target_id, fact.kind) for fact in facts} >= {
        (12, 10, "overrides"),
        (12, 11, "implements"),
    }
    method_facts = [fact for fact in facts if fact.source_id == 12]
    assert {fact.confidence for fact in method_facts} == {0.95}
    assert {fact.provenance for fact in method_facts} == {"java_override_exact"}


def test_exact_parameter_types_select_one_same_arity_overload() -> None:
    context = RelationContext(
        (
            indexed(1, "Base", "class"),
            indexed(2, "Child", "class", supertypes=("Base",)),
            method(10, "Base", "save", ("String",)),
            method(11, "Base", "save", ("int",)),
            method(12, "Child", "save", ("String",)),
        )
    )

    method_facts = [fact for fact in derive_java_relations(context).facts if fact.source_id == 12]

    assert [(fact.target_id, fact.kind, fact.confidence) for fact in method_facts] == [
        (10, "overrides", 0.9)
    ]


def test_multiple_indistinguishable_overloads_are_ambiguous() -> None:
    context = RelationContext(
        (
            indexed(1, "Base", "class"),
            indexed(2, "Child", "class", supertypes=("Base",)),
            method(10, "Base", "run", ()),
            method(11, "Base", "run", ()),
            method(12, "Child", "run", ()),
        )
    )

    result = derive_java_relations(context)

    assert [fact for fact in result.facts if fact.source_id == 12] == []
    assert result.diagnostics.ambiguous == 1


def test_arity_fallback_is_lower_confidence() -> None:
    context = RelationContext(
        (
            indexed(1, "Base", "class"),
            indexed(2, "Child", "class", supertypes=("Base",)),
            method(10, "Base", "save", ("String",)),
            method(12, "Child", "save", ("Object",)),
        )
    )

    fact = next(fact for fact in derive_java_relations(context).facts if fact.source_id == 12)

    assert (fact.target_id, fact.confidence, fact.provenance) == (
        10,
        0.65,
        "java_name_arity",
    )


def test_nearest_class_method_wins_over_farther_ancestor() -> None:
    context = RelationContext(
        (
            indexed(1, "Base", "class"),
            indexed(2, "Middle", "class", supertypes=("Base",)),
            indexed(3, "Child", "class", supertypes=("Middle",)),
            method(10, "Base", "run", ()),
            method(11, "Middle", "run", ()),
            method(12, "Child", "run", ()),
        )
    )

    method_facts = [fact for fact in derive_java_relations(context).facts if fact.source_id == 12]

    assert [(fact.target_id, fact.kind) for fact in method_facts] == [(11, "overrides")]


def test_interface_method_extending_parent_is_an_override() -> None:
    context = RelationContext(
        (
            indexed(1, "Parent", "interface"),
            indexed(2, "Child", "interface", supertypes=("Parent",)),
            method(10, "Parent", "run", ()),
            method(12, "Child", "run", ()),
        )
    )

    fact = next(fact for fact in derive_java_relations(context).facts if fact.source_id == 12)

    assert (fact.target_id, fact.kind) == (10, "overrides")


def test_static_private_and_final_methods_do_not_form_virtual_relations() -> None:
    context = RelationContext(
        (
            indexed(1, "Base", "class"),
            indexed(2, "Child", "class", supertypes=("Base",)),
            method(10, "Base", "staticRun", (), modifiers=frozenset({"static"})),
            method(11, "Base", "privateRun", (), modifiers=frozenset({"private"})),
            method(12, "Base", "finalRun", (), modifiers=frozenset({"final"})),
            method(20, "Child", "staticRun", (), modifiers=frozenset({"static"})),
            method(21, "Child", "privateRun", (), modifiers=frozenset({"private"})),
            method(22, "Child", "finalRun", ()),
        )
    )

    facts = derive_java_relations(context).facts

    assert not [fact for fact in facts if fact.source_id in {20, 21, 22}]
