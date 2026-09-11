"""Building a query spec deterministically from scored terms.

The model **proposes** semantic structure -- which terms matter, how they
group, whether the groups relate. Statistics **validate** that against this
codebase (`validate`), and statistics **decide** what the model cannot know:

    kind preference   which kinds of symbol the terms' postings land on
    which fields      which fields those postings land in
    execution order   `df` makes selectivity estimable up front (`planner`)

The dividing line: **semantic questions go to the model, facts go to the
index.** Asked about validation on entity fields, the model omitted `field`
from its kinds -- that was never its question to answer.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from codesense.ql.compile.partition import Cluster, partition
from codesense.ql.compile.spec import GraphConstraint, QuerySpec, normalise_target
from codesense.ql.compile.validate import validate_groups, validate_relations
from codesense.ql.context import EvalContext
from codesense.ql.fields import IndexField
from codesense.ql.satisfiers import AnnotationSatisfier, LexicalSatisfier
from codesense.ql.term_resolution import TermResolver
from codesense.ql.unit import QueryUnit, Term

__all__ = ["KIND_PREFERENCE_FLOOR", "build_spec", "infer_kinds"]

#: Share a kind must reach before it counts as a preference. Too low is no
#: preference at all; too high misses cases where the answers really do
#: concentrate in fields.
KIND_PREFERENCE_FLOOR = 0.25

#: The same floor for field preference.
FIELD_FLOOR = 0.15


@dataclass(frozen=True, slots=True)
class ScoredTerm:
    """A term the LLM judged relevant, with the relevance it assigned."""

    value: str
    score: float = 1.0


def infer_kinds(
    terms: Sequence[str],
    ctx: EvalContext,
    *,
    resolver: TermResolver | None = None,
) -> tuple[str, ...]:
    """Infer which kinds of symbol to prefer, from where the postings land.

    Half of access-path selection. What kind a query is about is answered
    more reliably by what its terms actually hit than by asking the model:
    the model reports its impression of the wording, the statistics report a
    fact about this index.
    """
    term_resolver = resolver or TermResolver(ctx)
    counts: Counter[str] = Counter()
    for term in terms:
        for posting in term_resolver.postings(term):
            element = ctx.symbols.get(posting.symbol_id)
            if element is not None:
                counts[element.kind] += 1
    if not counts:
        return ()
    total = sum(counts.values())
    return tuple(
        kind for kind, count in counts.most_common() if count / total >= KIND_PREFERENCE_FLOOR
    )


def infer_fields(
    terms: Sequence[str],
    ctx: EvalContext,
    *,
    resolver: TermResolver | None = None,
) -> tuple[IndexField, ...]:
    """Infer which fields to probe.

    The other half of access-path selection. If a term's hits are almost all
    in `doc`, probing only `name` misses everything.
    """
    term_resolver = resolver or TermResolver(ctx)
    counts: Counter[str] = Counter()
    for term in terms:
        for posting in term_resolver.postings(term):
            counts[str(posting.field)] += 1
    if not counts:
        return ()
    total = sum(counts.values())
    chosen = [field for field, count in counts.most_common() if count / total >= FIELD_FLOOR]
    return tuple(IndexField(field) for field in chosen)


def build_spec(
    query: str,
    terms: Sequence[ScoredTerm | Term | str] | Mapping[str, float],
    ctx: EvalContext,
    *,
    concept: str = "",
    annotations: Sequence[str] = (),
    groups: Mapping[str, Sequence[str]] | None = None,
    relations: Sequence[tuple[str, str] | tuple[str, str, Sequence[str]]] = (),
    target: object = None,
    limit: int | None = None,
    resolver: TermResolver | None = None,
) -> tuple[QuerySpec, list[str]]:
    """Assemble the model's proposals into a spec, returning the validation
    record alongside it.

    ``groups`` and ``relations`` are **proposals**: they pass through
    statistical validation first, and anything that fails is merged away or
    discarded with the reason recorded in the second return value. Without
    groups, partitioning falls back to posting overlap.
    """
    term_resolver = resolver or TermResolver(ctx)
    normalized = _normalise(terms)
    known = [item for item in normalized if term_resolver.surfaces(item.value)]
    if not known:
        raise ValueError("not one term could be grounded in the index")

    notes = [
        f"ignored ungrounded term {item.value!r}"
        for item in normalized
        if not term_resolver.surfaces(item.value)
    ]
    values = [item.value for item in known]
    by_value: dict[str, Term] = {}
    for item in known:
        by_value.setdefault(item.value, item)
    if groups:
        checked, group_notes = validate_groups(dict(groups), ctx, resolver=term_resolver)
        notes += group_notes
        named_clusters = [
            (name, Cluster(tuple(sorted(members)), f"model group {name!r}, cohesion validated"))
            for name, members in checked.items()
        ]
        kept_relations, rejected = validate_relations(
            relations,
            checked,
            ctx,
            resolver=term_resolver,
        )
        notes += rejected
        notes += [f"accepted relation {r.src}->{r.dst}: {r.detail}" for r in kept_relations]
        constraints = tuple(
            GraphConstraint(src=r.src, dst=r.dst, edge=r.edge) for r in kept_relations
        )
    else:
        clusters = partition(values, ctx, resolver=term_resolver)
        named_clusters = [
            ("q" if len(clusters) == 1 else f"u{index}", cluster)
            for index, cluster in enumerate(clusters)
        ]
        constraints = ()
    units: list[QueryUnit] = []
    for index, (name, cluster) in enumerate(named_clusters):
        satisfiers: list[object] = [
            LexicalSatisfier(
                terms=tuple(by_value[term] for term in cluster.terms if term in by_value),
                weight=0.5,
            )
        ]
        # Annotations attach to the first unit only: they are independent
        # evidence, and repeating them would multiply their weight
        if annotations and index == 0:
            satisfiers.append(AnnotationSatisfier(names=tuple(annotations)))
        units.append(
            QueryUnit(
                name=name,
                concept=cluster.reason,
                satisfiers=tuple(satisfiers),
            )
        )

    return (
        QuerySpec(
            query=query,
            units=tuple(units),
            graph=constraints if len(units) > 1 else (),
            concept=concept,
            kinds=infer_kinds(values, ctx, resolver=term_resolver),
            target=normalise_target(target),
            limit=limit,
        ),
        notes,
    )


def _normalise(
    terms: Sequence[ScoredTerm | Term | str] | Mapping[str, float],
) -> list[Term]:
    if isinstance(terms, Mapping):
        return [Term(str(key), weight=float(value)) for key, value in terms.items()]
    found: list[Term] = []
    for item in terms:
        if isinstance(item, Term):
            found.append(item)
        elif isinstance(item, ScoredTerm):
            found.append(Term(item.value, weight=item.score))
        elif isinstance(item, str):
            found.append(Term(item.casefold()))
    return found
