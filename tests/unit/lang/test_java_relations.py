from __future__ import annotations

from codesense.lang import Declaration, IndexedDeclaration, RelationContext
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
