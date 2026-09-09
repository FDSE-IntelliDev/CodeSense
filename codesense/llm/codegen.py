"""Generating a query script directly.

There is exactly one difference from the `codesense.ql.compile` route, and it
is fundamental: **the statistics go to the model too**, so it orders the steps
itself instead of being distilled into a structured intermediate form that a
planner then orders.

That route rests on the premise that the model does not know `buffer` matches
2365 symbols in netty. But that is a prompting problem, not an architectural
necessity -- hand it the vocabulary with `df` attached and it has exactly what
the planner had. And a script expresses things the intermediate form cannot:
temporaries, conditionals, arbitrary composition -- which is why chapter 06
chose scripts over JSON plans in the first place.

Output must pass `codesense.ql.script`'s whitelist check before it runs.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence

from codesense.llm.config import LlmConfig

__all__ = ["OPERATOR_SPEC", "PROMPT", "ScriptGenerator"]

OPERATOR_SPEC = """\
Available operators (all return a Frag -- a code subgraph carrying evidence):

  eval_unit(unit, ctx) -> Frag
      Evaluate a query unit. A unit is a set of satisfiers; the more that
      match, the higher the score.

  reach(frag, ctx, *, edge=["calls"], direction="forward"|"backward"|"any",
        hops=(lo, hi)) -> Frag
      Symbols reachable from frag. Nodes only, no paths. Cheap.

  project(frag, ctx, *, edge="in_file", direction="forward",
          kind=None, include_self=False, min_confidence=0.0) -> Frag
      Project evidence-preserving one-hop results across an exact edge kind.
      Edge kinds include references, imports, and in_file. Use
      direction="backward" for referencers, then edge="in_file" to return
      their owning files.

  hop(src, dst, ctx, *, edge, direction, hops, avoid=None,
      min_confidence=0.0, max_paths=10000) -> Frag
      The **paths** between src and dst satisfying a graph constraint. Much
      more expensive than reach; use it only when you need the paths.

  only(frag, *, kind=None, file=None, where=None) -> Frag   filter by attribute
  top(frag, n, *, by=None) -> Frag                          take the top n
  score_of(frag, symbol_id, by=None) -> float               read a score
  degree(frag, ctx, *, min_in=None, max_in=None, edge=...) -> Frag

  intent(frag, "<criterion>", ctx, *, threshold=0.5, max_items=60) -> Frag
      The LLM judges candidates one by one. **Roughly 5000x the cost of one
      index lookup** -- it must come last, with its input capped below 60.

Frag supports `|` (union), `&` (intersection), `-` (difference), plus
.nodes / .induced(ids) / .roots() / .leaves().

**Where scores come from -- the easiest thing to get wrong:**
Only Frags produced by `eval_unit` carry lexical scores. Frags from `reach`
and `hop` **have no scores** -- calling `top` on one is the same as taking an
arbitrary slice. So graph information is for **weighting**, not replacement:
keep the `eval_unit` result and use the graph only to adjust its ranking.

Building units:

  QueryUnit("name", concept="description for intent", satisfiers=(...))
  LexicalSatisfier(terms=(Term("word", weight=0.8), ...), weight=0.5)
  AnnotationSatisfier(units=(Term("cache"),), names=("@Cacheable",), weight=0.9)
  ModifierSatisfier(modifiers=("static", "native"), weight=0.6)

The standard shape (adapt it; do not invent your own):

```python
# One unit, terms weighted by relevance; prefer more terms -- ICF down-weights
# the useless ones automatically
q = QueryUnit("q", satisfiers=(
    LexicalSatisfier(terms=(
        Term("pool", weight=0.9), Term("arena", weight=0.9), Term("chunk", weight=0.8),
        Term("recycler", weight=0.8), Term("alloc", weight=0.6), ...   # 15-30 of them
    ), weight=0.5),
))
frag = eval_unit(q, ctx)

# Graph proximity: strongest hits as seeds, candidates in the neighbourhood get
# **weighted** -- note that frag itself is not replaced
near = reach(top(frag, 20), ctx, edge=["calls", "contains"], direction="any", hops=(1, 2))
boosted = set(near.nodes) & set(frag.nodes)
frag = frag.induced(sorted(
    frag.nodes,
    key=lambda s: (-score_of(frag, s) * (1 + 0.6 * (s in boosted)), s),
)[:60])

answer = intent(frag, "<one sentence stating what makes an element an answer>", ctx, max_items=60)
```

**The script is ordinary Python; use control flow freely.** That is the whole
point of a script over a fixed pipeline -- temporaries, branches and loops are
all allowed, so operators can be composed into a computation graph rather than
a straight line. For instance, a narrow-then-widen adaptive shape:

```python
# Try narrow first; widen only if too few candidates -- no need to guess right
# on the first attempt
core = QueryUnit("core", satisfiers=(LexicalSatisfier(terms=NARROW, weight=0.5),))
wide = QueryUnit("wide", satisfiers=(LexicalSatisfier(terms=BROAD, weight=0.4),))

frag = eval_unit(core, ctx)
if len(frag) < 30:                    # too narrow, fold the peripheral terms in
    frag = frag | eval_unit(wide, ctx)
```

Or probe different edge kinds separately and combine:

```python
by_call = reach(seeds, ctx, edge=["calls"], direction="any", hops=(1, 2))
by_type = reach(seeds, ctx, edge=["contains"], direction="any", hops=(1, 1))
strong = set(by_call.nodes) & set(by_type.nodes)   # connected both ways: stronger

# For "files containing references to PageRequest", retain lexical evidence
# while moving from the referenced type to its referencers and then to files.
page_request = eval_unit(page_request_unit, ctx)
referencers = project(page_request, ctx, edge="references", direction="backward")
answer = project(referencers, ctx, edge="in_file", kind="file", include_self=True)
```
"""

PROMPT = """\
You are generating a query script for a code retrieval system.

The operator reference includes the evidence-preserving `project` operator
and the `references`, `imports`, and `in_file` edge vocabulary.

Codebase: {project} ({symbols} symbols, {edges} edges)
Query: {query}

{spec}

The vocabulary of this codebase, as `word:how many symbols it matches`
(**terms may only be drawn from this list**):
{vocab}

Points to keep in mind:

- **Look at df before deciding the ordering.** A word matching tens of
  thousands of symbols and one matching a few dozen are entirely different
  things inside the same OR.
- **Union units, do not intersect them.** Elements containing both keyword A
  and keyword B barely exist; two concepts usually land on **different
  elements** and have to be connected through the graph.
- **Supply enough terms: 15-30.** Three or five will hurt recall badly. When
  unsure whether a term belongs, include it -- ICF will down-weight it.
- **Graph proximity always helps**, but it **weights** rather than replaces:
  do not use a `reach` result directly as the candidate set, or every lexical
  score is lost.
- **Do not truncate early.** Use `top` once, at the end, before `intent`.
- **`intent` goes last**, with its input capped below 60. Write its criterion
  out in full -- one concrete sentence about *this* query. Never pass a
  placeholder; `"<criterion>"` above is a slot to fill, not text to copy.

Output the script only, with no explanation. The script must:
- assign its result to a variable named `answer`
- use `ctx` and the operators listed above directly (**write no imports**)
- use comments to say **why the steps are ordered this way**, not what the
  operators do

`for` / `while` / `if` / `def` are allowed, but loops must terminate (there is
a step limit). No imports, and nothing beyond the operators and Frag.
"""

_FENCE = re.compile(r"```(?:python)?\s*(.*?)```", re.S)

_log = logging.getLogger(__name__)


class ScriptGenerator:
    """Have the model write the query script itself."""

    def __init__(self, config: LlmConfig, session: object | None = None) -> None:
        self._config = config
        self._session = session

    def generate(
        self,
        query: str,
        project: str,
        vocabulary: Sequence[tuple[str, int]],
        *,
        symbols: int,
        edges: int,
    ) -> str | None:
        """``vocabulary`` is (term, df) pairs -- **df is the crucial part**;
        without it the model has no basis for ordering."""
        prompt = PROMPT.format(
            project=project,
            query=query,
            spec=OPERATOR_SPEC,
            symbols=f"{symbols:,}",
            edges=f"{edges:,}",
            vocab=" ".join(f"{term}:{df}" for term, df in vocabulary),
        )
        content = self._ask(prompt)
        if content is None:
            return None
        fenced = _FENCE.search(content)
        return (fenced.group(1) if fenced else content).strip()

    def _ask(self, prompt: str) -> str | None:
        session = self._session
        if session is None:
            import requests

            session = requests.Session()
        try:
            response = session.post(  # type: ignore[attr-defined]
                f"{self._config.base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {self._config.api_key}"},
                json={
                    "model": self._config.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0,
                },
                timeout=self._config.timeout,
            )
            response.raise_for_status()
            return str(response.json()["choices"][0]["message"]["content"])
        except Exception:  # noqa: BLE001 -- generation failures must degrade
            _log.exception("code generation request failed")
            return None
