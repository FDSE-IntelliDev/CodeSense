"""Running the semantic retrieval benchmark.

    python scripts/run_benchmark.py --index-dir DIR --queries evaluation/benchmark/queries.json \\
        --base-url https://api.openai.com/v1 --model gpt-4o-mini

Each query runs down several arms:

    literal    only the query's literal words (a lower bound)
    generic    the LLM derives terms from general knowledge, **without being
               shown the project vocabulary**
    grounded   the LLM **picks from the project vocabulary**, plus annotation
               signals
    graph      grounded, then reranked using call and containment relations
    planned    the structured pipeline: LLM proposes, statistics validates,
               the planner orders, then execute
    codegen    **generate the script directly**: hand the model the operator
               spec and a vocabulary with df, and it writes the script itself

`grounded` against `graph` tests something else: in a 40,000-symbol project,
ORing 25 terms grows the result set into the thousands and **weak signals in
aggregate drown out strong ones**. The graph contributes evidence independent
of lexical matching -- symbols structurally near strong hits are more likely
to be relevant.

`generic` against `grounded` is the crucial comparison. It tests
``docs/design/09-grounding.md`` section 6 directly: put the project
vocabulary in the prompt and the output terms land on the project's actual
spellings, so "the LLM says buffer while the project writes buf" cannot
happen.

The metric is **recall**, not precision: the gold set aims to be certain
rather than complete, so everything listed really is a correct answer but
what is unlisted is not necessarily wrong.

**Derived results are cached** (`--cache`). Not to save money: measured, the
same query with the same vocabulary at temperature=0 can differ by 20
percentage points of recall between runs -- the LLM's run-to-run variance is
the same size as the effect being measured. Without pinning it down, any
cross-run comparison is noise. `--repeat` runs several times and averages, to
quantify that variance.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from codesense.indexing import build_expansion_table
from codesense.lang.java import JavaLanguage
from codesense.ql import Edge, Element, Frag, IndexField
from codesense.ql.context import EvalContext
from codesense.ql.operators import eval_unit, reach, score_of
from codesense.ql.satisfiers import AnnotationSatisfier, LexicalSatisfier
from codesense.ql.store import (
    InMemoryEdgeStore,
    InMemoryPostingIndex,
    InMemorySymbolStore,
    Posting,
)
from codesense.ql.unit import QueryUnit, Term

#: How much project vocabulary goes into the prompt. A large project's full
#: vocabulary does not fit, so a middle band by ICF is taken -- the very
#: common words do not discriminate and the once-or-twice words are mostly
#: noise.
VOCAB_SAMPLE = 600

#: Cutoffs used in the report.
CUTOFFS = (10, 30, 100)

#: How many of the strongest hits seed the graph. Too many is no narrowing at
#: all; too few does not anchor.
GRAPH_SEEDS = 20

#: Boost for candidates in the seeds' neighbourhood. The graph is evidence
#: **independent of lexical matching**, so it multiplies rather than replaces
#: -- it must not lift things with no lexical basis at all.
GRAPH_BOOST = 0.6

#: Hop range for the seed neighbourhood. The more hops, the weaker the claim
#: that two things are related.
GRAPH_HOPS = (1, 2)

GENERIC_PROMPT = """\
You are expanding query terms for a code retrieval system.

Target codebase: {project}
User query: {query}

Give the English words you expect to appear in the identifiers of relevant
code. Output JSON:
{{"terms": ["word1", "word2", ...], "annotations": ["@AnnotationName", ...]}}

Requirements: at most 25 terms, all lowercase words (the form an identifier
splits into), and no generic words like get/set/value.
"""

PROMPT = """\
You are expanding query terms for a code retrieval system.

Target codebase: {project}
User query: {query}

Words occurring in this codebase, ordered by informativeness (you may only
pick from this list):
{vocab}

Pick the words from that list that relate to the query. Output JSON:
{{"terms": ["word1", ...], "annotations": ["@AnnotationName", ...], "reason": "one sentence"}}

Requirements:
- at most 25 terms, all of which **must** come from the list above; do not
  invent words
- pick domain words that genuinely point at the query's intent, not generic
  ones like get/set/value
- put relevant framework annotations (@PostMapping, say) in annotations; those
  may be absent from the list
"""


@dataclass
class Result:
    query_id: str
    project: str
    gold: list[str]
    ranks: dict[str, dict[str, int]] = field(default_factory=dict)
    terms: dict[str, list[str]] = field(default_factory=dict)
    annotations: list[str] = field(default_factory=list)

    def recall(self, arm: str, cutoff: int) -> float:
        ranks = self.ranks.get(arm, {})
        found = sum(1 for name in self.gold if 0 < ranks.get(name, 0) <= cutoff)
        return found / len(self.gold) if self.gold else 0.0


def load_index(path: Path) -> tuple[EvalContext, dict[str, list[int]]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    elements = [
        Element(
            symbol_id=s["symbol_id"],
            name=s["name"],
            kind=s["kind"],
            file=s["file"],
            span=tuple(s["span"]),
            signature=s.get("signature", ""),
            container=s.get("container", ""),
            doc=s.get("doc", ""),
            language="java",
            modifiers=frozenset(s.get("modifiers", ())),
        )
        for s in payload["symbols"]
    ]
    postings = {
        term: [Posting(p["symbol_id"], IndexField(p["field"]), p["tf"]) for p in entries]
        for term, entries in payload["postings"].items()
    }
    ctx = EvalContext(
        symbols=InMemorySymbolStore(elements),
        postings=InMemoryPostingIndex(postings, total_symbols=len(elements)),
        expansion=build_expansion_table(language=JavaLanguage()),
        edges=InMemoryEdgeStore(
            Edge(
                source_id=e["source_id"],
                target_id=e["target_id"],
                kind=e["kind"],
                confidence=e["confidence"],
                provenance=e["provenance"],
            )
            for e in payload.get("edges", ())
        ),
    )
    by_name: dict[str, list[int]] = {}
    for element in elements:
        by_name.setdefault(element.name, []).append(element.symbol_id)
    return ctx, by_name


def vocabulary(ctx: EvalContext, limit: int = VOCAB_SAMPLE) -> list[str]:
    """The project vocabulary that goes into the prompt. ``limit <= 0`` gives
    all of it.

    **How it narrows matters.** An early version took a fixed ICF band by
    ``abs(icf_ratio - 0.55)``, which on netty excluded `buf` (0.176),
    `allocator` (0.311), `pooled` and `chunk` entirely -- precisely because
    they are common in netty. And the query was about buffer allocation.
    **Query-independent static narrowing systematically drops the domain's
    central words**, as the benchmark measured.
    """
    usable = [
        term
        for term in ctx.postings.terms()
        if term.isalpha()
        and len(term) >= 3
        and (info := ctx.postings.term_info(term))
        and info.df >= 2
    ]
    if limit <= 0:
        return sorted(usable)
    scored = sorted(
        (abs((ctx.postings.term_info(t).icf_ratio if ctx.postings.term_info(t) else 0) - 0.55), t)
        for t in usable
    )
    return sorted(term for _, term in scored[:limit])


def literal_terms(query: str, ctx: EvalContext) -> list[str]:
    """Words from the query that match the index directly. The baseline."""
    words = {w.lower() for w in re.findall(r"[A-Za-z]+", query) if len(w) > 2}
    return sorted(w for w in words if ctx.postings.term_info(w) is not None)


def derive(
    query: str,
    project: str,
    vocab: Sequence[str] | None,
    judge_config: Any,
    cache: Path | None = None,
    attempt: int = 0,
) -> dict[str, Any]:
    """Have the LLM derive query terms. With ``vocab`` as None it has only
    general knowledge to go on."""
    import hashlib

    import requests

    key = hashlib.sha256(
        f"{judge_config.model}|{project}|{query}|{len(vocab or ())}|{attempt}".encode()
    ).hexdigest()[:16]
    slot = cache / f"{key}.json" if cache else None
    if slot is not None and slot.is_file():
        return json.loads(slot.read_text(encoding="utf-8"))

    prompt = (
        PROMPT.format(project=project, query=query, vocab=", ".join(vocab))
        if vocab is not None
        else GENERIC_PROMPT.format(project=project, query=query)
    )
    response = requests.post(
        f"{judge_config.base_url.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {judge_config.api_key}"},
        json={
            "model": judge_config.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
        },
        timeout=judge_config.timeout,
    )
    response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"]
    start, end = content.find("{"), content.rfind("}")
    answer = (
        json.loads(content[start : end + 1])
        if start >= 0 and end > start
        else {"terms": [], "annotations": []}
    )
    if slot is not None:
        slot.parent.mkdir(parents=True, exist_ok=True)
        slot.write_text(json.dumps(answer, ensure_ascii=False), encoding="utf-8")
    return answer


def graph_rerank(frag: Frag, ctx: EvalContext, unit: str) -> dict[str, int]:
    """Rerank lexical results by graph proximity.

    The strongest hits seed the walk, 1-2 hops out, and candidates landing in
    the neighbourhood get boosted. `reach` rather than `hop`: only "is it
    connected at all" matters here, the paths themselves are not needed, and
    enumerating paths over thousands of candidates is far more expensive.
    """
    if not frag:
        return {}
    ordered = sorted(frag.nodes, key=lambda sid: (-score_of(frag, sid, unit), sid))
    seeds = frag.induced(ordered[:GRAPH_SEEDS])
    neighbourhood = set(
        reach(seeds, ctx, edge=["calls", "contains"], direction="any", hops=GRAPH_HOPS).nodes
    )
    boosted = sorted(
        frag.nodes,
        key=lambda sid: (
            -score_of(frag, sid, unit) * (1 + GRAPH_BOOST * (sid in neighbourhood)),
            sid,
        ),
    )
    ranks: dict[str, int] = {}
    for position, symbol_id in enumerate(boosted, 1):
        ranks.setdefault(frag.nodes[symbol_id].name, position)
    return ranks


def run_planned(
    case: dict[str, Any],
    ctx: EvalContext,
    vocab: Sequence[str],
    config: Any,
    cache: Path | None = None,
    attempt: int = 0,
) -> tuple[dict[str, int], list[str]]:
    """The full route: the LLM interprets, statistics fixes the structure,
    cost decides the order, then execute.

    **The division of labour**: the model does NLP only (pick terms, score
    them, write a criterion); how many units, which kinds are preferred and
    in what order are all decided statistically.

    `intent` is skipped under a recall metric: it can only remove candidates,
    and what is being measured is whether what should be found was found.
    """
    import hashlib

    from codesense.llm import QueryUnderstanding
    from codesense.ql.compile import Intent, build_spec, plan

    # The model output must be cached: without pinning it down, the same
    # query can differ by tens of points of recall between runs, and the
    # comparison measures noise.
    key = hashlib.sha256(
        f"nlp|{config.model}|{case['project']}|{case['query']}|{len(vocab)}|{attempt}".encode()
    ).hexdigest()[:16]
    slot = cache / f"{key}.json" if cache else None
    if slot is not None and slot.is_file():
        understood = json.loads(slot.read_text(encoding="utf-8"))
    else:
        understood = QueryUnderstanding(config).understand(case["query"], case["project"], vocab)
        if understood is not None and slot is not None:
            slot.parent.mkdir(parents=True, exist_ok=True)
            slot.write_text(json.dumps(understood, ensure_ascii=False), encoding="utf-8")
    if understood is None:
        return {}, ["interpretation failed"]

    try:
        spec, notes = build_spec(
            case["query"],
            understood["terms"],
            ctx,
            concept=understood["concept"],
            annotations=understood["annotations"],
            groups=understood.get("groups"),
            relations=understood.get("relations", ()),
        )
    except ValueError as exc:
        return {}, [f"building the spec failed: {exc}"]

    execution = plan(spec, ctx)
    state = execution.run(ctx, skip=(Intent,))
    names: dict[str, int] = {}
    ordered = sorted(state.current.nodes, key=lambda sid: (-score_of(state.current, sid), sid))
    for position, symbol_id in enumerate(ordered, 1):
        names.setdefault(state.current.nodes[symbol_id].name, position)
    return names, notes + list(execution.reasoning)


def run_codegen(
    case: dict[str, Any],
    ctx: EvalContext,
    vocab_df: Sequence[tuple[str, int]],
    config: Any,
    cache: Path | None = None,
    attempt: int = 0,
) -> tuple[dict[str, int], list[str]]:
    """Have the model write the script, then run it after the whitelist check.

    Exactly one thing differs from `run_planned`: **who gets the statistics**.
    Here `df` goes to the model too, so it orders the steps itself.
    """
    import hashlib

    from codesense.llm import ScriptGenerator
    from codesense.ql import ScriptError, run_script
    from codesense.ql.operators import degree, eval_unit, hop, only, reach, top
    from codesense.ql.satisfiers import AnnotationSatisfier, LexicalSatisfier, ModifierSatisfier

    edges = sum(ctx.edges.degree(i) for i in range(1, min(ctx.symbols.count(), 300) + 1))
    key = hashlib.sha256(
        f"gen|{config.model}|{case['project']}|{case['query']}|{len(vocab_df)}|{attempt}".encode()
    ).hexdigest()[:16]
    slot = cache / f"{key}.py" if cache else None
    if slot is not None and slot.is_file():
        source = slot.read_text(encoding="utf-8")
    else:
        source = ScriptGenerator(config).generate(
            case["query"],
            case["project"],
            vocab_df,
            symbols=ctx.symbols.count(),
            edges=edges,
        )
        if source and slot is not None:
            slot.parent.mkdir(parents=True, exist_ok=True)
            slot.write_text(source, encoding="utf-8")
    if not source:
        return {}, ["generation failed"]

    namespace = {
        "ctx": ctx,
        "eval_unit": eval_unit,
        "hop": hop,
        "reach": reach,
        "degree": degree,
        "only": only,
        "top": top,
        "score_of": score_of,
        "intent": lambda frag, *a, **k: frag,  # judging skipped, as on the other arms
        "QueryUnit": QueryUnit,
        "Term": Term,
        "LexicalSatisfier": LexicalSatisfier,
        "AnnotationSatisfier": AnnotationSatisfier,
        "ModifierSatisfier": ModifierSatisfier,
    }
    try:
        answer = run_script(source, namespace)
    except ScriptError as exc:
        return {}, [f"script rejected or crashed: {exc}"]
    if not isinstance(answer, Frag):
        return {}, [f"the script produced a {type(answer).__name__}, not a Frag"]

    names: dict[str, int] = {}
    for position, symbol_id in enumerate(
        sorted(answer.nodes, key=lambda s: (-score_of(answer, s), s)), 1
    ):
        names.setdefault(answer.nodes[symbol_id].name, position)
    return names, [f"{source.count(chr(10)) + 1}-line script"]


def vocabulary_df(ctx: EvalContext, limit: int = 0) -> list[tuple[str, int]]:
    """The vocabulary with df -- `df` is what lets the model order the steps
    itself."""
    found: list[tuple[str, int]] = []
    for term in ctx.postings.terms():
        info = ctx.postings.term_info(term)
        if info is not None and info.df >= 2 and term.isalpha() and len(term) >= 3:
            found.append((term, info.df))
    found.sort()
    return found if limit <= 0 else found[:limit]


def rank(frag: Frag, unit: str) -> dict[str, int]:
    """Symbol name to rank, keeping the best rank per name."""
    ordered = sorted(frag.nodes, key=lambda sid: (-score_of(frag, sid, unit), sid))
    ranks: dict[str, int] = {}
    for position, symbol_id in enumerate(ordered, 1):
        ranks.setdefault(frag.nodes[symbol_id].name, position)
    return ranks


def search(ctx: EvalContext, terms: Sequence[str], annotations: Sequence[str]) -> Frag:
    satisfiers: list[Any] = []
    if terms:
        satisfiers.append(LexicalSatisfier(terms=tuple(Term(t) for t in terms), weight=0.5))
    if annotations:
        satisfiers.append(AnnotationSatisfier(names=tuple(annotations), weight=0.9))
    if not satisfiers:
        return Frag()
    return eval_unit(QueryUnit("q", satisfiers=tuple(satisfiers)), ctx)


def run(args: argparse.Namespace) -> list[Result]:
    from codesense.llm import LlmConfig

    config = LlmConfig.load(base_url=args.base_url, model=args.model)
    spec = json.loads(args.queries.read_text(encoding="utf-8"))
    contexts: dict[str, tuple[EvalContext, dict[str, list[int]]]] = {}
    results: list[Result] = []

    for case in spec["queries"]:
        project = case["project"]
        if project not in contexts:
            contexts[project] = load_index(args.index_dir / f"{project}.json")
        ctx, _ = contexts[project]

        result = Result(query_id=case["id"], project=project, gold=case["gold"])

        literal = literal_terms(case["query"], ctx)
        result.terms["literal"] = literal
        result.ranks["literal"] = rank(search(ctx, literal, ()), "q")

        for arm, vocab in (("generic", None), ("grounded", vocabulary(ctx, args.vocab_size))):
            answer = derive(case["query"], project, vocab, config, args.cache, args.attempt)
            terms = [t.lower() for t in answer.get("terms", []) if isinstance(t, str)][:25]
            annotations = [a for a in answer.get("annotations", []) if isinstance(a, str)]
            result.terms[arm] = terms
            if arm == "grounded":
                result.annotations = annotations
            found = search(ctx, sorted({*literal, *terms}), annotations)
            result.ranks[arm] = rank(found, "q")
            if arm == "grounded":
                result.ranks["graph"] = graph_rerank(found, ctx, "q")

        vocab = vocabulary(ctx, args.vocab_size)
        result.ranks["planned"], reasoning = run_planned(
            case, ctx, vocab, config, args.cache, args.attempt
        )
        result.ranks["codegen"], gen_notes = run_codegen(
            case, ctx, vocabulary_df(ctx), config, args.cache, args.attempt
        )
        result.terms["codegen_notes"] = gen_notes
        result.terms["plan_reasoning"] = reasoning

        in_vocab = sum(1 for t in result.terms["generic"] if ctx.postings.term_info(t) is not None)
        total = len(result.terms["generic"]) or 1
        results.append(result)
        print(
            f"  {case['id']:<22}generic {total} terms "
            f"({100 * in_vocab / total:.0f}% in the vocabulary)"
            f"  grounded {len(result.terms['grounded'])} terms"
            f"  planned {len(result.ranks['planned'])}"
            f"  codegen {len(result.ranks['codegen'])} · {gen_notes[0] if gen_notes else ''}"
        )
    return results


ARMS = ("literal", "generic", "grounded", "graph", "planned", "codegen")


def report(results: Sequence[Result]) -> None:
    for cutoff in CUTOFFS:
        print(f"\n{'=' * 98}\nrecall @{cutoff}")
        print(f"  {'query':<24}{'gold':>5}" + "".join(f"{a:>11}" for a in ARMS))
        print("  " + "-" * 94)
        totals: Counter[str] = Counter()
        for result in results:
            row = f"  {result.query_id:<24}{len(result.gold):>5}"
            for arm in ARMS:
                value = result.recall(arm, cutoff)
                totals[arm] += value
                row += f"{value:>10.0%} "
            print(row)
        n = len(results) or 1
        print("  " + "-" * 94)
        print(f"  {'mean':<24}{'':>5}" + "".join(f"{totals[a] / n:>10.0%} " for a in ARMS))
    print()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index-dir", type=Path, required=True)
    parser.add_argument("--queries", type=Path, required=True)
    parser.add_argument("--base-url", default="https://api.openai.com/v1")
    parser.add_argument("--model", default="gpt-4o-mini")
    parser.add_argument(
        "--vocab-size",
        type=int,
        default=VOCAB_SAMPLE,
        help="how much vocabulary goes into the prompt; <=0 for all of it",
    )
    parser.add_argument(
        "--cache", type=Path, help="cache directory for derived results, making runs reproducible"
    )
    parser.add_argument(
        "--attempt", type=int, default=0, help="which sample this is, for quantifying LLM variance"
    )
    parser.add_argument("--dump", type=Path, help="write the per-query detail here")
    args = parser.parse_args(argv)

    results = run(args)
    report(results)
    if args.dump:
        args.dump.write_text(
            json.dumps(
                [
                    {
                        "id": r.query_id,
                        "terms": r.terms,
                        "annotations": r.annotations,
                        "gold_ranks": {
                            g: {arm: r.ranks.get(arm, {}).get(g) for arm in ARMS} for g in r.gold
                        },
                    }
                    for r in results
                ],
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )
        print(f"detail -> {args.dump}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
