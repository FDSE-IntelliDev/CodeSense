"""Rendering an execution plan as a readable, editable, runnable script.

Chapter 06 argues the **artifact should be a script rather than a JSON
plan**: in research work "change one line and retry" happens constantly, and
a script can be breakpointed, have a line commented out to see the
difference, and be edited by hand before rerunning.

So `Plan` is the internal representation and the script is what ships. The
two must be equivalent -- `tests/unit/ql/compile/test_emit.py` actually runs
the emitted script and asserts it returns what executing the `Plan` returns.

Comments explain **why the steps are ordered this way**, never what the
operators mean; that belongs to chapter 05.
"""

from __future__ import annotations

import textwrap
from collections.abc import Sequence

from codesense.ql.compile.plan import (
    Boost,
    Cohere,
    EvalUnit,
    Intent,
    Narrow,
    Plan,
    ProjectTarget,
    Step,
)
from codesense.ql.compile.relation_endpoints import is_legacy_relation
from codesense.ql.compile.spec import QuerySpec
from codesense.ql.satisfiers.lexical import AnnotationSatisfier, LexicalSatisfier, ModifierSatisfier
from codesense.ql.unit import QueryUnit

__all__ = ["to_script"]

_HEADER = '''"""{title}

compiled from: {query}
index: {index}
"""
from codesense.ql.compile import Intent
from codesense.ql.compile.relation_endpoints import relation_destinations
from codesense.ql.operators import eval_unit, intent, project, reach, score_of, top
from codesense.ql.satisfiers import AnnotationSatisfier, LexicalSatisfier, ModifierSatisfier
from codesense.ql.unit import QueryUnit, Term
'''


def to_script(plan: Plan, spec: QuerySpec, *, index: str = "<unrecorded>") -> str:
    """Render to a script.

    ``index`` goes in the header: the same script gives different results on
    a different index, and without it nothing is reproducible.
    """
    lines = [
        _HEADER.format(
            title=spec.query or "query",
            query=spec.query or "(unrecorded)",
            index=index,
        )
    ]
    lines.append("\n# -- query units " + "-" * 46)
    for step in plan.steps:
        if isinstance(step, EvalUnit):
            lines.append(_unit_source(step.unit))

    lines.append("\n# -- orchestration " + "-" * 44)
    for why in plan.reasoning:
        lines.append(_comment(why))
    lines.append("")

    body: list[str] = ["boosted = set()"]
    for step in plan.steps:
        rendered = _step_source(step)
        if rendered:
            body.append(rendered)
    lines.append("\n".join(body))
    lines.append("\nanswer = frag")
    lines.append(
        "\n# Equivalent to the `Plan` -- tests/unit/ql/compile/test_emit.py runs\n"
        "# this script and asserts both return the same result."
    )
    return "\n".join(lines).rstrip() + "\n"


def _comment(text: str, width: int = 76) -> str:
    return "\n".join(f"# {line}" for line in textwrap.wrap(text, width) or [""])


def _unit_source(unit: QueryUnit) -> str:
    parts = [f'{_ident(unit.name)} = QueryUnit(\n    "{unit.name}",']
    if unit.concept:
        parts.append(f'    concept="{_escape(unit.concept)}",')
    parts.append("    satisfiers=(")
    for satisfier in unit.satisfiers:
        parts.append(_satisfier_source(satisfier))
    parts.append("    ),")
    parts.append(")")
    return "\n".join(parts)


def _satisfier_source(satisfier: object) -> str:
    if isinstance(satisfier, LexicalSatisfier):
        terms = ", ".join(
            f'Term("{t.value}", weight={t.weight:.2f})' if t.weight != 1.0 else f'Term("{t.value}")'
            for t in satisfier.terms
        )
        return _wrap(f"LexicalSatisfier(terms=({terms},), weight={satisfier.weight:.3f}),")
    if isinstance(satisfier, AnnotationSatisfier):
        names = ", ".join(f'"{n}"' for n in satisfier.names)
        units = ", ".join(f'Term("{t.value}")' for t in satisfier.units)
        args = []
        if units:
            args.append(f"units=({units},)")
        if names:
            args.append(f"names=({names},)")
        return _wrap(f"AnnotationSatisfier({', '.join(args)}, weight={satisfier.weight:g}),")
    if isinstance(satisfier, ModifierSatisfier):
        mods = ", ".join(f'"{m}"' for m in satisfier.modifiers)
        return _wrap(f"ModifierSatisfier(modifiers=({mods},), weight={satisfier.weight:g}),")
    return _wrap(f"{satisfier!r},")


def _wrap(text: str, indent: str = "        ") -> str:
    return "\n".join(
        textwrap.wrap(
            text,
            width=92,
            initial_indent=indent,
            subsequent_indent=indent + "    ",
            break_long_words=False,
            break_on_hyphens=False,
        )
    )


def _step_source(step: Step) -> str:
    if isinstance(step, EvalUnit):
        name = _ident(step.unit.name)
        matches = _matches_ident(step.unit.name)
        if step.seed:
            return f"{matches} = eval_unit({name}, ctx)\nfrag = {matches}"
        # Union, not intersection -- units land on different elements
        return f"{matches} = eval_unit({name}, ctx)\nfrag = frag | {matches}"
    if isinstance(step, Cohere):
        # Only mark the neighbourhood; ranking happens once, in `Narrow`,
        # matching `Plan` semantics (`Cohere` fills state.boosted, `Narrow`
        # consumes it)
        return "\n".join(
            [
                f"# structural coherence: top {step.seeds} as seeds, "
                f"{step.hops[0]}-{step.hops[1]} hops out",
                f"near = reach(top(frag, {step.seeds}), ctx,",
                f'             edge={list(step.edge)!r}, direction="any", hops={step.hops!r})',
                "boosted |= set(near.nodes) & set(frag.nodes)",
            ]
        )

    if isinstance(step, Boost):
        relation = "<->" if is_legacy_relation(step.edge) else "->"
        return (
            f"# graph constraint: {step.src_name} {relation} {step.dst_name} "
            f"(statistically validated)\n"
            f"boosted |= relation_destinations(\n"
            f"    {_matches_ident(step.src_name)}, {_matches_ident(step.dst_name)}, ctx,\n"
            f"    edge={step.edge!r}, hops={step.hops!r},\n"
            f") & set(frag.nodes)"
        )
    if isinstance(step, Narrow):
        if not step.limit:
            return ""
        lines = []
        multiplier = "(1 + 0.6 * (s in boosted))"
        if step.kind:
            lines.append(
                f"# kind is a preference, not a filter: the model's kinds are\n"
                f"# unreliable and hard filtering would delete the answers\n"
                f"preferred = {{s for s, e in frag.nodes.items() if e.kind in {list(step.kind)!r}}}"
            )
            multiplier += " * (1 + 0.3 * (s in preferred))"
        lines.append(
            f"frag = frag.induced(sorted(\n"
            f"    frag.nodes,\n"
            f"    key=lambda s: (-score_of(frag, s) * {multiplier}, s),\n"
            f")[:{step.limit}])"
        )
        return "\n".join(lines)
    if isinstance(step, Intent):
        return (
            f'frag = intent(frag, "{_escape(step.concept)}", ctx,\n'
            f"              threshold={step.threshold:g}, max_items={step.max_items})"
        )
    if isinstance(step, ProjectTarget):
        return f'frag = project(frag, ctx, edge="in_file", kind={step.target!r}, include_self=True)'
    return f"# unknown step: {step.label}"


def _ident(name: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in name)
    return f"unit_{cleaned}" if not cleaned[:1].isalpha() else cleaned


def _matches_ident(name: str) -> str:
    """Name the evaluated fragment without obscuring the editable unit."""
    return f"{_ident(name)}_matches"


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


def script_of(plan: Plan, spec: QuerySpec, index: str = "<unrecorded>") -> Sequence[str]:
    """Return lines, so tests can assert on them individually."""
    return to_script(plan, spec, index=index).splitlines()
