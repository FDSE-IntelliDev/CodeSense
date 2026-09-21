"""Turning a query into ranked results.

Three routes, and the choice between them is a real trade-off:

    codegen   the model writes a query script from the query and operator spec.
              Best measured recall (R@100 58% over three samples) and the only
              route that can express control flow. Costs one LLM call.
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
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace
from functools import wraps
from typing import Any

from codesense.ql.compile.spec import normalise_target
from codesense.ql.context import EvalContext
from codesense.ql.frag import Evidence, Frag, UnitHit, Verdict
from codesense.ql.judge import Judge, JudgeItem, NullJudge
from codesense.ql.operators import degree, eval_unit, hop, intent, reach, score_of, top
from codesense.ql.operators import project as project_frag
from codesense.ql.operators.select import only
from codesense.ql.satisfiers import AnnotationSatisfier, LexicalSatisfier, ModifierSatisfier
from codesense.ql.unit import QueryUnit, Term

__all__ = [
    "Hit",
    "IntentFallbackPolicy",
    "ROUTES",
    "SearchResult",
    "representative_vocabulary",
    "search",
]

_log = logging.getLogger(__name__)

Progress = Callable[..., None]

ROUTES = ("codegen", "planned", "lexical")

#: How much vocabulary goes into the prompt. The whole thing does not fit for a
#: large project, and the tail is mostly typos and one-off locals.
VOCAB_FOR_PROMPT = 1200


def representative_vocabulary(
    vocabulary: Iterable[tuple[str, int]],
    *,
    limit: int,
    min_df: int,
) -> list[tuple[str, int]]:
    """Take a bounded repeated-term prefix from df-descending vocabulary."""
    if limit <= 0:
        return []
    selected: list[tuple[str, int]] = []
    for term, document_frequency in vocabulary:
        # Index.vocabulary is df-descending, so later entries cannot recover.
        if document_frequency < min_df:
            break
        selected.append((term, document_frequency))
        if len(selected) >= limit:
            break
    return selected


#: Candidates handed to `intent`. It costs roughly 5000 lookups per call, so
#: everything cheap runs first and this is what survives.
INTENT_CAP = 60


@dataclass(frozen=True, slots=True)
class IntentFallbackPolicy:
    """When the shared search pipeline should spend money on semantic judging."""

    min_candidates: int = 20
    tail_size: int = 5
    max_tail_ratio: float = 0.25
    max_items: int = INTENT_CAP

    def __post_init__(self) -> None:
        if self.min_candidates < 0:
            raise ValueError("min_candidates must be non-negative")
        if self.tail_size < 1:
            raise ValueError("tail_size must be positive")
        if not 0.0 <= self.max_tail_ratio <= 1.0:
            raise ValueError("max_tail_ratio must be between 0 and 1")
        if self.max_items < 1:
            raise ValueError("max_items must be positive")


DEFAULT_INTENT_FALLBACK = IntentFallbackPolicy()


class _CachingJudge:
    """Reuse identical decisions within one search, including undecided ones."""

    def __init__(self, delegate: Judge) -> None:
        self._delegate = delegate
        self._cache: dict[tuple[str, JudgeItem], Verdict | None] = {}
        self.cache_hits = 0
        self.submitted = 0

    def judge(self, concept: str, items: Sequence[JudgeItem]) -> dict[int, Verdict]:
        missing: list[JudgeItem] = []
        for item in items:
            if (concept, item) in self._cache:
                self.cache_hits += 1
            else:
                missing.append(item)
        if missing:
            self.submitted += len(missing)
            try:
                answered = self._delegate.judge(concept, missing)
            except Exception:
                # A failed batch is still an attempted decision. Cache it as
                # undecided so the public fallback does not repeat the charge.
                for item in missing:
                    self._cache[(concept, item)] = None
                raise
            for item in missing:
                self._cache[(concept, item)] = answered.get(item.symbol_id)
        return {
            item.symbol_id: verdict
            for item in items
            if (verdict := self._cache[(concept, item)]) is not None
        }


_WORD = re.compile(r"[A-Za-z][A-Za-z0-9]*")

# File output is intentionally recognised only in a few explicit result
# forms. A broad ``file`` noun match misreads object-position prose such as
# "methods that write a file" as a request to return files.
_FILE_RESULT_CLAUSE = r"(?:containing|with|matching|that|which|whose|where|for|named)\b"
_FILE_OUTPUT_REQUEST = re.compile(
    rf"^\s*(?:find|list|show|return)\s+(?:"
    rf"(?:(?:java|source)\s+)*files\b|"
    rf"(?:(?:a|an|the|java|source)\s+)+file\b(?=\s*(?:$|[,.?!:]|{_FILE_RESULT_CLAUSE}))|"
    rf"file\b(?=\s+{_FILE_RESULT_CLAUSE})"
    rf")",
    re.IGNORECASE,
)
_WHICH_FILES_REQUEST = re.compile(r"^\s*which\s+files?\b", re.IGNORECASE)
_CHINESE_WHICH_FILES_REQUEST = re.compile(r"^\s*哪些\s*(?:[A-Za-z][A-Za-z0-9._-]*\s*)?文件")
_CHINESE_FILE_OUTPUT_REQUEST = re.compile(
    r"^\s*(?:列出|查找|找出|显示|返回)\s*"
    r"(?:[A-Za-z][A-Za-z0-9._-]*\s*)?文件(?=\s*(?:中|里|包含|引用|导入|$|[,.?!，。？！]))"
)

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


class _PlannedFailure(RuntimeError):
    """Carry a structured target through planned-route degradation."""

    def __init__(self, target: tuple[str, ...] | None, cause: Exception) -> None:
        super().__init__(str(cause))
        self.target = target
        self.cause = cause


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
    intent_fallback: IntentFallbackPolicy = DEFAULT_INTENT_FALLBACK,
    target: str | Sequence[str] | None = None,
    trace: bool = True,
) -> SearchResult:
    """Run a query.

    ``llm`` is a `codesense.llm.LlmConfig`; without one the route degrades to
    `lexical` rather than failing -- a search that works without a key is worth
    more than one that refuses.

    ``judge`` lets generated/planned scripts execute the real `intent`
    operator and enables the shared low-confidence fallback. Off by default
    because it is the one operator that costs money per result, and because
    under a recall metric it can only remove candidates.

    ``trace`` prints compact progress events for route stages and operators.
    It is on by default so long-running searches remain observable; callers
    that require quiet stdout can pass ``trace=False``.
    """
    if route not in ROUTES:
        raise ValueError(f"route must be one of {ROUTES}, got {route!r}")
    started = time.perf_counter()
    progress: Progress | None = _write_progress if trace else None
    _notify(
        progress,
        "search.start",
        route=route,
        target=target if target is not None else "auto",
        limit=limit,
        query=_short_query(query),
    )
    target_was_explicit = target is not None
    # ``None`` means no route has decided yet; ``()`` is a deliberate empty
    # target and must not be replaced by lexical inference later.
    requested_target = normalise_target(target) if target_was_explicit else None
    judge_cache: _CachingJudge | None = None
    if judge and not isinstance(ctx.judge, NullJudge):
        judge_cache = _CachingJudge(ctx.judge)
        ctx = replace(ctx, judge=judge_cache)
    if llm is None and route != "lexical":
        _log.info("no LLM configured; falling back to the lexical route")
        _notify(progress, "route.fallback", route=route, fallback="lexical", reason="no LLM")
        route = "lexical"

    runner = {"codegen": _codegen, "planned": _planned, "lexical": _lexical}[route]
    _notify(progress, "route.start", route=route)
    try:
        frag, script, notes, route_target = runner(
            query,
            ctx,
            project,
            vocabulary,
            llm,
            requested_target,
            target_was_explicit,
            judge,
            limit,
            progress,
        )
    except _PlannedFailure as exc:
        _log.exception("route %s failed", route)
        _notify(
            progress,
            "route.fallback",
            route=route,
            fallback="lexical",
            reason=f"{type(exc.cause).__name__}: {exc.cause}",
        )
        fallback_target = requested_target if target_was_explicit else exc.target
        frag, script, notes, route_target = _lexical(
            query,
            ctx,
            project,
            vocabulary,
            None,
            fallback_target,
            target_was_explicit,
            judge,
            limit,
            progress,
        )
        notes = [
            f"{route} failed ({type(exc.cause).__name__}: {exc.cause}); fell back to lexical",
            *notes,
        ]
        route = "lexical"
    except Exception as exc:  # noqa: BLE001 -- a failed route degrades, never crashes
        _log.exception("route %s failed", route)
        _notify(
            progress,
            "route.fallback",
            route=route,
            fallback="lexical",
            reason=f"{type(exc).__name__}: {exc}",
        )
        frag, script, notes, route_target = _lexical(
            query,
            ctx,
            project,
            vocabulary,
            None,
            requested_target,
            target_was_explicit,
            judge,
            limit,
            progress,
        )
        notes = [f"{route} failed ({type(exc).__name__}: {exc}); fell back to lexical", *notes]
        route = "lexical"

    _notify(progress, "route.done", route=route, candidates=len(frag), target=route_target)

    effective_target = (
        requested_target
        if target_was_explicit
        else route_target
        if route_target is not None
        else _query_target(query)
    )
    _notify(progress, "target.resolved", target=effective_target or "default")
    before_target = len(frag)
    _notify(
        progress,
        "target.enforce.start",
        target=effective_target or "default",
        candidates=before_target,
    )
    frag = _enforce_target(frag, ctx, effective_target, progress=progress)
    _notify(
        progress,
        "target.enforce.done",
        target=effective_target or "default",
        candidates=before_target,
        actual=len(frag),
    )
    if judge:
        if isinstance(ctx.judge, NullJudge):
            _notify(progress, "intent.fallback.skip", reason="no semantic judge configured")
        else:
            decision = _intent_fallback_decision(frag, limit, intent_fallback)
            if decision.trigger:
                before_judge = len(frag)
                cache_hits_before = judge_cache.cache_hits if judge_cache is not None else 0
                submitted_before = judge_cache.submitted if judge_cache is not None else 0
                _notify(
                    progress,
                    "intent.fallback.trigger",
                    reason=decision.reason,
                    candidates=before_judge,
                )
                _notify(progress, "operator.start", name="intent", candidates=before_judge)
                judged_started = time.perf_counter()
                frag, notes = _judge(
                    frag,
                    query,
                    ctx,
                    notes,
                    reason=decision.reason,
                    max_items=intent_fallback.max_items,
                )
                _notify(
                    progress,
                    "operator.done",
                    name="intent",
                    candidates=before_judge,
                    actual=len(frag),
                    cache_hits=(
                        judge_cache.cache_hits - cache_hits_before if judge_cache is not None else 0
                    ),
                    submitted=(
                        judge_cache.submitted - submitted_before
                        if judge_cache is not None
                        else len(top(frag, intent_fallback.max_items))
                    ),
                    elapsed=time.perf_counter() - judged_started,
                )
            else:
                _notify(progress, "intent.fallback.skip", reason=decision.reason)
    if route == "lexical" and effective_target:
        notes = [
            *notes,
            "requested target is a lexical approximation; relation semantics were not verified",
        ]

    hits = _rank(frag, limit)
    elapsed = time.perf_counter() - started
    _notify(progress, "search.done", route=route, hits=len(hits), elapsed=elapsed)
    return SearchResult(
        query=query,
        hits=hits,
        route=route,
        script=script,
        notes=notes,
        target=effective_target,
        elapsed=elapsed,
    )


def _write_progress(stage: str, **fields: object) -> None:
    """Print one compact, immediately visible search progress event."""
    details = " ".join(f"{name}={_progress_value(value)}" for name, value in fields.items())
    suffix = f" {details}" if details else ""
    print(f"[codesense.search] {stage}{suffix}", flush=True)


def _notify(progress: Progress | None, stage: str, **fields: object) -> None:
    if progress is not None:
        progress(stage, **fields)


def _progress_value(value: object) -> str:
    if isinstance(value, float):
        return f"{value:.4f}s"
    return repr(value)


def _short_query(query: str, limit: int = 160) -> str:
    compact = " ".join(query.split())
    return compact if len(compact) <= limit else f"{compact[: limit - 1]}…"


def _query_target(query: str) -> tuple[str, ...]:
    """Infer a file result only from narrow, explicit output wording."""
    if (
        _FILE_OUTPUT_REQUEST.search(query)
        or _WHICH_FILES_REQUEST.search(query)
        or _CHINESE_WHICH_FILES_REQUEST.search(query)
        or _CHINESE_FILE_OUTPUT_REQUEST.search(query)
    ):
        return ("file",)
    return ()


def _enforce_target(
    frag: Frag,
    ctx: EvalContext,
    target: tuple[str, ...],
    *,
    progress: Progress | None = None,
) -> Frag:
    """Apply the same hard output contract after every route and fallback."""
    if not target:
        return frag.induced(sid for sid, element in frag.nodes.items() if element.kind != "file")
    # A mixed target containing ``file`` must still add owner files even when
    # every current declaration already satisfies another requested kind.
    if "file" not in target and all(element.kind in target for element in frag.nodes.values()):
        return frag
    if target == ("file",) and all(element.kind == "file" for element in frag.nodes.values()):
        return frag
    direct_kinds = tuple(kind for kind in target if kind != "file")
    direct = frag.induced(
        symbol_id for symbol_id, element in frag.nodes.items() if element.kind in direct_kinds
    )
    if "file" not in target:
        return direct
    traced_project = _traced_operator("project", project_frag, progress)
    files = traced_project(frag, ctx, edge="in_file", kind="file", include_self=True)
    return direct | files


def _returned_target(frag: Frag) -> tuple[str, ...] | None:
    """Recognise a generated script that explicitly returned only file nodes."""
    if frag and all(element.kind == "file" for element in frag.nodes.values()):
        return ("file",)
    return None


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


@dataclass(frozen=True, slots=True)
class _IntentFallbackDecision:
    trigger: bool
    reason: str


def _intent_fallback_decision(
    frag: Frag,
    limit: int,
    policy: IntentFallbackPolicy,
) -> _IntentFallbackDecision:
    """Use relative tail scores because aggregate QL scores are not probabilities."""
    if len(frag) <= policy.min_candidates:
        return _IntentFallbackDecision(
            False,
            f"candidate count {len(frag)} <= {policy.min_candidates}",
        )
    window_size = min(max(limit, 0), len(frag))
    if window_size < policy.tail_size:
        return _IntentFallbackDecision(
            False,
            f"return window {window_size} < tail size {policy.tail_size}",
        )

    window = top(frag, window_size)
    ordered = sorted(window.nodes, key=lambda sid: (-score_of(window, sid), sid))
    highest = score_of(window, ordered[0])
    tail_scores = [score_of(window, symbol_id) for symbol_id in ordered[-policy.tail_size :]]
    ratios = [score / highest if highest > 0.0 else 0.0 for score in tail_scores]
    rendered = ",".join(f"{ratio:.3f}" for ratio in ratios)
    if all(ratio < policy.max_tail_ratio for ratio in ratios):
        return _IntentFallbackDecision(
            True,
            f"low-score tail ratios [{rendered}] < {policy.max_tail_ratio:.3f}",
        )
    return _IntentFallbackDecision(
        False,
        f"tail ratios [{rendered}] are not all below {policy.max_tail_ratio:.3f}",
    )


def _judge(
    frag: Frag,
    query: str,
    ctx: EvalContext,
    notes: list[str],
    *,
    reason: str,
    max_items: int,
) -> tuple[Frag, list[str]]:
    """Judge a bounded head while retaining candidates outside the spend budget."""
    narrowed = top(frag, max_items)
    judged = intent(narrowed, query, ctx, max_items=max_items)
    untouched = frag.induced(
        symbol_id for symbol_id in frag.nodes if symbol_id not in narrowed.nodes
    )
    combined = judged | untouched
    return combined, [
        *notes,
        f"intent fallback ({reason}) judged {len(narrowed)} candidates, kept {len(combined)}",
    ]


def _namespace(
    ctx: EvalContext,
    judge: bool = False,
    progress: Progress | None = None,
) -> dict[str, Any]:
    """What a generated script may reach.

    `intent` is stubbed out unless judging is on, so a script that calls it
    does not silently spend money during an ordinary search.
    """
    namespace = {
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
    for name in ("eval_unit", "hop", "reach", "project", "degree", "only", "top", "intent"):
        namespace[name] = _traced_operator(name, namespace[name], progress)
    return namespace


def _traced_operator(name: str, operator: Any, progress: Progress | None) -> Any:
    """Wrap generated-script operators without tracing hot per-symbol helpers."""
    if progress is None:
        return operator

    @wraps(operator)
    def traced(*args: object, **kwargs: object) -> object:
        inputs = [len(value) for value in (*args, *kwargs.values()) if isinstance(value, Frag)]
        _notify(progress, "operator.start", name=name, inputs=inputs or "none")
        started = time.perf_counter()
        try:
            result = operator(*args, **kwargs)
        except Exception as exc:
            _notify(
                progress,
                "operator.failed",
                name=name,
                error=f"{type(exc).__name__}: {exc}",
                elapsed=time.perf_counter() - started,
            )
            raise
        _notify(
            progress,
            "operator.done",
            name=name,
            actual=len(result) if isinstance(result, Frag) else type(result).__name__,
            elapsed=time.perf_counter() - started,
        )
        return result

    return traced


def _codegen(
    query: str,
    ctx: EvalContext,
    project: str,
    _vocabulary: Sequence[tuple[str, int]],
    llm: Any,
    target: tuple[str, ...] | None,
    _target_was_explicit: bool,
    judge: bool,
    _limit: int,
    progress: Progress | None,
) -> tuple[Frag, str, list[str], tuple[str, ...] | None]:
    """Have the model write the script, then run it behind the whitelist."""
    from codesense.llm import ScriptGenerator
    from codesense.ql import ScriptError, run_script

    _notify(progress, "codegen.generate.start", symbols=ctx.symbols.count())
    generated_started = time.perf_counter()
    source = ScriptGenerator(llm).generate(
        query,
        project or "the project",
        symbols=ctx.symbols.count(),
        edges=_edge_estimate(ctx),
        judge_enabled=judge,
    )
    if not source:
        raise RuntimeError("the model returned no script")
    _notify(
        progress,
        "codegen.generate.done",
        lines=source.count("\n") + 1,
        elapsed=time.perf_counter() - generated_started,
    )
    _notify(progress, "codegen.execute.start")
    executed_started = time.perf_counter()
    try:
        answer = run_script(source, _namespace(ctx, judge=judge, progress=progress))
    except ScriptError as exc:
        raise RuntimeError(f"generated script rejected: {exc}") from exc
    if not isinstance(answer, Frag):
        raise TypeError(f"the script produced a {type(answer).__name__}, not a Frag")
    _notify(
        progress,
        "codegen.execute.done",
        candidates=len(answer),
        elapsed=time.perf_counter() - executed_started,
    )
    return (
        answer,
        source,
        [f"{source.count(chr(10)) + 1}-line generated script"],
        target if target is not None else _returned_target(answer),
    )


def _planned(
    query: str,
    ctx: EvalContext,
    project: str,
    vocabulary: Sequence[tuple[str, int]],
    llm: Any,
    target: tuple[str, ...] | None,
    target_was_explicit: bool,
    judge: bool,
    limit: int,
    progress: Progress | None,
) -> tuple[Frag, str, list[str], tuple[str, ...] | None]:
    """Model proposes, statistics validate, the planner orders."""
    from codesense.llm import EndpointKind, QueryUnderstanding
    from codesense.ql.compile import ResultRelation, build_spec, plan, to_script
    from codesense.ql.term_resolution import TermResolver

    vocab = list(vocabulary)[:VOCAB_FOR_PROMPT]
    _notify(progress, "planned.understand.start", vocabulary=len(vocab))
    understood_started = time.perf_counter()
    understood = QueryUnderstanding(llm).understand(query, project or "the project", vocab)
    if understood is None:
        raise RuntimeError("the model could not interpret the query")
    _notify(
        progress,
        "planned.understand.done",
        terms=sum(len(unit.terms) for unit in understood.units),
        relations=len(understood.relations),
        elapsed=time.perf_counter() - understood_started,
    )
    # Structured output always makes this decision explicitly. An empty list
    # suppresses the weaker query-text heuristic unless the caller overrides it.
    route_target = target if target_was_explicit else normalise_target(understood.targets)
    try:
        terms = tuple(
            Term(
                value=item.value.casefold(),
                source=item.source.value,
                weight=item.weight,
                reason=item.reason,
            )
            for unit in understood.units
            for item in unit.terms
        )
        groups = {
            unit.name: [item.value.casefold() for item in unit.terms] for unit in understood.units
        }
        relations: list[tuple[str, str, tuple[str, ...]]] = []
        result_relation: ResultRelation | None = None
        for relation in understood.relations:
            edges = tuple(edge.value for edge in relation.edges)
            if relation.source.kind is EndpointKind.RESULT:
                assert relation.target.unit is not None
                result_relation = ResultRelation(relation.target.unit, "source", edges)
            elif relation.target.kind is EndpointKind.RESULT:
                assert relation.source.unit is not None
                result_relation = ResultRelation(relation.source.unit, "target", edges)
            else:
                assert relation.source.unit is not None and relation.target.unit is not None
                relations.append((relation.source.unit, relation.target.unit, edges))

        # This is the route-local judgement. The shared pipeline may later
        # apply its conditional fallback to a low-confidence final target.
        # Disabling it here keeps judge=False from invoking the model.
        concept = understood.criterion if judge else ""
        _notify(progress, "planned.spec.start", target=route_target or "default")
        planned_started = time.perf_counter()
        resolver = TermResolver(ctx)
        spec, validation_notes = build_spec(
            query,
            terms,
            ctx,
            concept=concept,
            annotations=understood.annotations,
            groups=groups,
            relations=relations,
            result_relation=result_relation,
            target=route_target,
            limit=limit,
            resolver=resolver,
        )
        execution = plan(spec, ctx, resolver=resolver)
        _notify(
            progress,
            "planned.plan.ready",
            steps=len(execution.steps),
            elapsed=time.perf_counter() - planned_started,
        )
        _notify(progress, "planned.execute.start", steps=len(execution.steps))
        executed_started = time.perf_counter()
        state = execution.run(ctx, progress=progress)
        _notify(
            progress,
            "planned.execute.done",
            candidates=len(state.current),
            elapsed=time.perf_counter() - executed_started,
        )
        return (
            state.current,
            to_script(execution, spec),
            [*validation_notes, *execution.reasoning],
            route_target,
        )
    except Exception as exc:
        raise _PlannedFailure(route_target, exc) from exc


def _lexical(
    query: str,
    ctx: EvalContext,
    project: str,
    vocabulary: Sequence[tuple[str, int]],
    llm: Any,
    target: tuple[str, ...] | None,
    _target_was_explicit: bool,
    _judge: bool,
    _limit: int,
    progress: Progress | None,
) -> tuple[Frag, str, list[str], tuple[str, ...]]:
    """The query's own words, grounded through the expansion table.

    No model, so nothing translates `backpressure` into `watermark`. What it
    does get is the abbreviation and vector grounding built at index time,
    which is exactly what that table is for.

    Graph proximity still applies. It needs no model and no relation stated in
    the query -- what sits structurally near a strong hit is more likely to be
    relevant, on every query.
    """
    effective_target = target if target is not None else _query_target(query)
    words = _query_terms(query, ctx)
    _notify(progress, "lexical.terms", terms=words, target=effective_target or "default")
    if not words:
        return (
            Frag(),
            "",
            ["no word in the query appears in this project's vocabulary"],
            effective_target,
        )
    unit = QueryUnit(
        "query",
        concept=query,
        satisfiers=(LexicalSatisfier(terms=tuple(Term(w) for w in words), weight=0.5),),
    )
    _notify(progress, "operator.start", name="eval_unit", inputs="query")
    evaluated_started = time.perf_counter()
    frag = eval_unit(unit, ctx)
    _notify(
        progress,
        "operator.done",
        name="eval_unit",
        actual=len(frag),
        elapsed=time.perf_counter() - evaluated_started,
    )
    if not len(frag):
        return frag, "", [f"no hits for {', '.join(words)}"], effective_target
    _notify(progress, "operator.start", name="cohere", candidates=len(frag))
    coherence_started = time.perf_counter()
    frag = _cohere(frag, ctx, progress=progress)
    _notify(
        progress,
        "operator.done",
        name="cohere",
        actual=len(frag),
        elapsed=time.perf_counter() - coherence_started,
    )
    return frag, "", [f"matched on {', '.join(words)}"], effective_target


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


def _cohere(
    frag: Frag,
    ctx: EvalContext,
    seeds: int = 20,
    boost: float = 0.6,
    *,
    progress: Progress | None = None,
) -> Frag:
    """Weight up candidates structurally near the strongest hits.

    Weighting, not filtering -- `reach` produces no scores, so replacing the
    fragment with its neighbourhood would throw away every lexical score. The
    increment is evidence rather than temporary ordering so the final rank,
    displayed score and explanation all observe the same boost.
    """
    traced_top = _traced_operator("top", top, progress)
    traced_reach = _traced_operator("reach", reach, progress)
    seeds_frag = traced_top(frag, seeds)
    near = traced_reach(
        seeds_frag,
        ctx,
        edge=["calls", "contains"],
        direction="any",
        hops=(1, 2),
    )
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
