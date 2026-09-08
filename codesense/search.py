"""Turning a query into ranked results.

Three routes, and the choice between them is a real trade-off:

    codegen   the model writes a query script, given the operator spec and the
              project vocabulary **with df**. Best measured recall (R@100 58%
              over three samples) and the only route that can express control
              flow. Costs one LLM call.
    planned   the model does NLP only -- pick terms, group them, state
              relations -- and statistics decides structure and ordering
              (R@100 51%). More deterministic, and the plan is inspectable
              before it runs.
    lexical   no model at all. The query's own words, plus whatever the
              expansion table grounds them to. Weak on its own, but it is what
              runs when there is no API key, and it is the floor everything
              else is measured against.

Every route returns the same `SearchResult`, and every route carries the
script or plan that produced it. **The artifact is the point**: a result you
cannot inspect or re-run with one line changed is not much use in research.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from codesense.ql.compile.spec import normalise_target
from codesense.ql.context import EvalContext
from codesense.ql.frag import Evidence, Frag, UnitHit
from codesense.ql.operators import degree, eval_unit, hop, intent, reach, score_of, top
from codesense.ql.operators import project as project_frag
from codesense.ql.operators.select import only
from codesense.ql.satisfiers import AnnotationSatisfier, LexicalSatisfier, ModifierSatisfier
from codesense.ql.unit import QueryUnit, Term

__all__ = ["Hit", "ROUTES", "SearchResult", "search"]

_log = logging.getLogger(__name__)

ROUTES = ("codegen", "planned", "lexical")

#: How much vocabulary goes into the prompt. The whole thing does not fit for a
#: large project, and the tail is mostly typos and one-off locals.
VOCAB_FOR_PROMPT = 1200

#: Candidates handed to `intent`. It costs roughly 5000 lookups per call, so
#: everything cheap runs first and this is what survives.
INTENT_CAP = 60

_WORD = re.compile(r"[A-Za-z][A-Za-z0-9]*")

#: Words dropped from a query before lexical matching. Kept deliberately tiny:
#: these are the ones that carry no intent yet appear in real code (`and` is a
#: real Java identifier), so ICF alone will not push them down far enough.
#: Domain words are never listed here -- that is ICF's job, not a stoplist's.
_STOPWORDS = frozenset(
    # fmt: off
    [
        "a",
        "an",
        "the",
        "and",
        "or",
        "not",
        "of",
        "in",
        "on",
        "at",
        "to",
        "for",
        "from",
        "with",
        "by",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "that",
        "this",
        "these",
        "those",
        "it",
        "its",
        "as",
        "if",
        "then",
        "than",
        "when",
        "where",
        "which",
        "who",
        "whom",
        "what",
        "how",
        "why",
        "do",
        "does",
        "did",
        "done",
        "can",
        "could",
        "should",
        "would",
        "will",
        "shall",
        "may",
        "might",
        "must",
    ]
    # fmt: on
)


@dataclass(frozen=True, slots=True)
class Hit:
    """One result, with enough to act on and enough to doubt it."""

    rank: int
    symbol_id: int
    name: str
    kind: str
    file: str
    line: int
    score: float
    why: str = ""

    def __str__(self) -> str:
        where = f"{self.file}:{self.line}"
        return f"{self.rank:>3}. {self.kind:<11} {self.name:<38} {where:<52} {self.score:.3f}"


@dataclass
class SearchResult:
    """Results plus the artifact that produced them."""

    query: str
    hits: list[Hit] = field(default_factory=list)
    route: str = ""
    script: str = ""
    notes: list[str] = field(default_factory=list)
    target: tuple[str, ...] = ()
    elapsed: float = 0.0

    def __len__(self) -> int:
        return len(self.hits)

    def __iter__(self):  # type: ignore[no-untyped-def]
        return iter(self.hits)

    def explain(self) -> str:
        """Everything about this run: route, timing, the script, the results."""
        lines = [
            f"query : {self.query}",
            f"route : {self.route}   {len(self.hits)} hits in {self.elapsed:.2f}s",
            f"target: {'/'.join(self.target) if self.target else 'default (non-file)'}",
        ]
        lines += [f"note  : {n}" for n in self.notes]
        if self.script:
            lines += ["", "-- script " + "-" * 60, self.script.rstrip(), "-" * 70]
        lines += ["", *[str(h) for h in self.hits]]
        return "\n".join(lines)


def search(
    query: str,
    ctx: EvalContext,
    *,
    project: str = "",
    vocabulary: Sequence[tuple[str, int]] = (),
    llm: Any = None,
    route: str = "codegen",
    limit: int = 30,
    judge: bool = False,
    target: str | Sequence[str] | None = None,
) -> SearchResult:
    """Run a query.

    ``llm`` is a `codesense.llm.LlmConfig`; without one the route degrades to
    `lexical` rather than failing -- a search that works without a key is worth
    more than one that refuses.

    ``judge`` turns on the `intent` operator. Off by default because it is the
    one operator that costs money per result, and because under a recall metric
    it can only remove candidates.
    """
    if route not in ROUTES:
        raise ValueError(f"route must be one of {ROUTES}, got {route!r}")
    started = time.perf_counter()
    target_was_explicit = target is not None
    requested_target = normalise_target(target) if target_was_explicit else _query_target(query)
    if llm is None and route != "lexical":
        _log.info("no LLM configured; falling back to the lexical route")
        route = "lexical"

    runner = {"codegen": _codegen, "planned": _planned, "lexical": _lexical}[route]
    try:
        frag, script, notes, route_target = runner(
            query,
            ctx,
            project,
            vocabulary,
            llm,
            requested_target,
            target_was_explicit,
        )
    except Exception as exc:  # noqa: BLE001 -- a failed route degrades, never crashes
        _log.exception("route %s failed", route)
        frag, script, notes, route_target = _lexical(
            query,
            ctx,
            project,
            vocabulary,
            None,
            requested_target,
            target_was_explicit,
        )
        notes = [f"{route} failed ({type(exc).__name__}: {exc}); fell back to lexical", *notes]
        route = "lexical"

    effective_target = requested_target if target_was_explicit else route_target
    if judge and len(frag) and ctx.judge is not None:
        frag, notes = _judge(frag, query, ctx, notes)
    frag = _enforce_target(frag, ctx, effective_target)
    if route == "lexical" and effective_target:
        notes = [
            *notes,
            "file target is a lexical approximation; relation semantics were not verified",
        ]

    return SearchResult(
        query=query,
        hits=_rank(frag, limit),
        route=route,
        script=script,
        notes=notes,
        target=effective_target,
        elapsed=time.perf_counter() - started,
    )


def _query_target(query: str) -> tuple[str, ...]:
    """Infer only explicit file nouns; relation verbs do not imply a target."""
    return ("file",) if re.search(r"\bfiles?\b|文件", query, re.IGNORECASE) else ()


def _enforce_target(frag: Frag, ctx: EvalContext, target: tuple[str, ...]) -> Frag:
    """Apply the same hard output contract after every route and fallback."""
    if not target:
        return frag.induced(sid for sid, element in frag.nodes.items() if element.kind != "file")
    if all(element.kind in target for element in frag.nodes.values()):
        return frag
    return project_frag(frag, ctx, edge="in_file", kind=target, include_self=True)


def _returned_target(frag: Frag) -> tuple[str, ...]:
    """Recognise a generated script that explicitly returned only file nodes."""
    if frag and all(element.kind == "file" for element in frag.nodes.values()):
        return ("file",)
    return ()


def _rank(frag: Frag, limit: int) -> list[Hit]:
    ordered = sorted(frag.nodes, key=lambda s: (-score_of(frag, s), s))
    found: list[Hit] = []
    for rank, symbol_id in enumerate(ordered[:limit], 1):
        element = frag.nodes[symbol_id]
        found.append(
            Hit(
                rank=rank,
                symbol_id=symbol_id,
                name=element.name,
                kind=element.kind,
                file=element.file,
                line=element.span[0] if element.span else 0,
                score=round(score_of(frag, symbol_id), 4),
                why=_why(frag, symbol_id),
            )
        )
    return found


def _why(frag: Frag, symbol_id: int) -> str:
    """The strongest few pieces of evidence, as text.

    Truncated deliberately: a symbol matched by 25 terms would otherwise
    produce a line nobody reads.
    """
    evidence = frag.evidence_for(symbol_id)
    # Skip the summary hit: it repeats the unit's total, which is already the
    # score column, and would push out the detail that explains it.
    detailed = [h for h in evidence.unit_hits if h.signal != Evidence.COMBINED]
    parts = [f"{h.detail}@{h.field}" for h in sorted(detailed, key=lambda h: -h.score)[:3]]
    parts += [f"{v.label}:{v.reason}" for v in evidence.verdicts[:1]]
    return ", ".join(parts)


def _judge(frag: Frag, query: str, ctx: EvalContext, notes: list[str]) -> tuple[Frag, list[str]]:
    narrowed = top(frag, INTENT_CAP)
    judged = intent(narrowed, query, ctx, max_items=INTENT_CAP)
    return judged, [*notes, f"intent judged {len(narrowed)} candidates, kept {len(judged)}"]


def _namespace(ctx: EvalContext, judge: bool = False) -> dict[str, Any]:
    """What a generated script may reach.

    `intent` is stubbed out unless judging is on, so a script that calls it
    does not silently spend money during an ordinary search.
    """
    return {
        "ctx": ctx,
        "eval_unit": eval_unit,
        "hop": hop,
        "reach": reach,
        "project": project_frag,
        "degree": degree,
        "only": only,
        "top": top,
        "score_of": score_of,
        "intent": intent if judge else (lambda frag, *a, **k: frag),
        "QueryUnit": QueryUnit,
        "Term": Term,
        "LexicalSatisfier": LexicalSatisfier,
        "AnnotationSatisfier": AnnotationSatisfier,
        "ModifierSatisfier": ModifierSatisfier,
    }


def _codegen(
    query: str,
    ctx: EvalContext,
    project: str,
    vocabulary: Sequence[tuple[str, int]],
    llm: Any,
    target: tuple[str, ...],
    _target_was_explicit: bool,
) -> tuple[Frag, str, list[str], tuple[str, ...]]:
    """Have the model write the script, then run it behind the whitelist."""
    from codesense.llm import ScriptGenerator
    from codesense.ql import ScriptError, run_script

    vocab = list(vocabulary)[:VOCAB_FOR_PROMPT]
    source = ScriptGenerator(llm).generate(
        query,
        project or "the project",
        vocab,
        symbols=ctx.symbols.count(),
        edges=_edge_estimate(ctx),
    )
    if not source:
        raise RuntimeError("the model returned no script")
    try:
        answer = run_script(source, _namespace(ctx))
    except ScriptError as exc:
        raise RuntimeError(f"generated script rejected: {exc}") from exc
    if not isinstance(answer, Frag):
        raise TypeError(f"the script produced a {type(answer).__name__}, not a Frag")
    return (
        answer,
        source,
        [f"{source.count(chr(10)) + 1}-line generated script"],
        target or _returned_target(answer),
    )


def _planned(
    query: str,
    ctx: EvalContext,
    project: str,
    vocabulary: Sequence[tuple[str, int]],
    llm: Any,
    target: tuple[str, ...],
    target_was_explicit: bool,
) -> tuple[Frag, str, list[str], tuple[str, ...]]:
    """Model proposes, statistics validate, the planner orders."""
    from codesense.llm import QueryUnderstanding
    from codesense.ql.compile import build_spec, plan, to_script

    vocab = [term for term, _ in list(vocabulary)[:VOCAB_FOR_PROMPT]]
    understood = QueryUnderstanding(llm).understand(query, project or "the project", vocab)
    if understood is None:
        raise RuntimeError("the model could not interpret the query")
    understood_target = None if target_was_explicit else understood.get("target")
    spec, validation_notes = build_spec(
        query,
        understood["terms"],
        ctx,
        concept=understood.get("concept", ""),
        annotations=understood.get("annotations", ()),
        groups=understood.get("groups"),
        relations=understood.get("relations", ()),
        target=target or understood_target,
    )
    execution = plan(spec, ctx)
    state = execution.run(ctx)
    return (
        state.current,
        to_script(execution, spec),
        [*validation_notes, *execution.reasoning],
        spec.target,
    )


def _lexical(
    query: str,
    ctx: EvalContext,
    project: str,
    vocabulary: Sequence[tuple[str, int]],
    llm: Any,
    target: tuple[str, ...],
    _target_was_explicit: bool,
) -> tuple[Frag, str, list[str], tuple[str, ...]]:
    """The query's own words, grounded through the expansion table.

    No model, so nothing translates `backpressure` into `watermark`. What it
    does get is the abbreviation and vector grounding built at index time,
    which is exactly what that table is for.

    Graph proximity still applies. It needs no model and no relation stated in
    the query -- what sits structurally near a strong hit is more likely to be
    relevant, on every query.
    """
    words = _query_terms(query, ctx)
    if not words:
        return Frag(), "", ["no word in the query appears in this project's vocabulary"], target
    unit = QueryUnit(
        "query",
        concept=query,
        satisfiers=(LexicalSatisfier(terms=tuple(Term(w) for w in words), weight=0.5),),
    )
    frag = eval_unit(unit, ctx)
    if not len(frag):
        return frag, "", [f"no hits for {', '.join(words)}"], target
    frag = _cohere(frag, ctx)
    return frag, "", [f"matched on {', '.join(words)}"], target


def _query_terms(query: str, ctx: EvalContext) -> list[str]:
    """Words from the query that the index or the expansion table can reach.

    A word is kept when the project uses it **or** when grounding maps it onto
    something the project uses. Dropping the rest keeps the unit from carrying
    terms that can only ever contribute zero.
    """
    found: list[str] = []
    for raw in _WORD.findall(query):
        word = raw.lower()
        if word in found or len(word) < 2 or word in _STOPWORDS:
            continue
        if ctx.postings.term_info(word) is not None or ctx.expansion.expand(word):
            found.append(word)
    return found


def _cohere(frag: Frag, ctx: EvalContext, seeds: int = 20, boost: float = 0.6) -> Frag:
    """Weight up candidates structurally near the strongest hits.

    Weighting, not filtering -- `reach` produces no scores, so replacing the
    fragment with its neighbourhood would throw away every lexical score. The
    increment is evidence rather than temporary ordering so the final rank,
    displayed score and explanation all observe the same boost.
    """
    near = reach(top(frag, seeds), ctx, edge=["calls", "contains"], direction="any", hops=(1, 2))
    boosted = set(near.nodes) & set(frag.nodes)
    if not boosted:
        return frag

    # Persist the multiplier as structural evidence. `score_of` sums scores
    # across units, so adding `base * boost` makes the final score exactly
    # `base * (1 + boost)` while preserving every original lexical reason.
    evidence = dict(frag.evidence)
    for symbol_id in boosted:
        structural = UnitHit(
            unit="coherence",
            signal="structural",
            detail=f"within 1-2 calls/contains hops of a top-{seeds} hit",
            field="graph",
            score=score_of(frag, symbol_id) * boost,
        )
        evidence[symbol_id] = frag.evidence_for(symbol_id).merge(Evidence(unit_hits=(structural,)))

    return Frag(
        nodes=frag.nodes,
        edges=frag.edges,
        evidence=evidence,
        witnesses=frag.witnesses,
    )


def _edge_estimate(ctx: EvalContext, sample: int = 300) -> int:
    """Roughly how many edges the graph holds.

    Sampled rather than counted: the number goes into a prompt to give the
    model a sense of scale, and walking the whole store for a figure used that
    way is not worth the wait.
    """
    total = ctx.symbols.count()
    if total <= 0:
        return 0
    checked = min(total, sample)
    seen = sum(ctx.edges.degree(i) for i in range(1, checked + 1))
    return int(seen * total / checked)
