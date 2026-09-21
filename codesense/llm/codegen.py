"""Generating a query script directly.

Unlike the `codesense.ql.compile` route, the model writes the executable query
script directly instead of first producing a structured intermediate form.
The prompt deliberately stays project-vocabulary-free: codegen gets the query,
operator contract and corpus size, while the planned route remains responsible
for vocabulary-grounded statistical planning.

A script expresses things the intermediate form cannot: temporaries,
conditionals and arbitrary composition -- which is why chapter 06 chose
scripts over JSON plans in the first place.

Output must pass `codesense.ql.script`'s whitelist check before it runs.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence

from codesense.llm.config import LlmConfig

__all__ = ["OPERATOR_SPEC", "PROMPT", "ScriptGenerator"]

OPERATOR_SPEC = """\
Available operators. Every operator returns a Frag, a code subgraph with evidence.

- eval_unit(unit, ctx): lexical/annotation/modifier retrieval with scores.
- only(frag, *, kind=None, file=None, language=None, where=None): attribute filter.
- top(frag, n, *, by=None): highest-scoring n; score_of(frag, id, by=None) reads a score.
- project(frag, ctx, *, edge, direction="forward", kind=None, include_self=False):
  evidence-preserving projection across exactly one edge. Important edges are
  references, imports, contains, calls, and in_file.
- reach(frag, ctx, *, edge, direction, hops): cheap reachable nodes, without paths.
- hop(src, dst, ctx, *, edge, direction, hops, avoid=None, min_confidence=0.0,
  max_paths=10000): matching paths; use only when the path itself is required.
- degree(frag, ctx, *, min_in=None, max_in=None, min_out=None, max_out=None,
  edge="calls"): graph-degree filter.
- intent(frag, criterion, ctx, *, threshold=0.5, max_items=60): expensive LLM
  judgement. It is real only when semantic judging is enabled for this search.

Frag supports |, &, -, len(), .nodes and .induced(ids). Build retrieval units with:
QueryUnit(name, concept=..., satisfiers=(...)); LexicalSatisfier(terms=(Term(...),));
AnnotationSatisfier(...); ModifierSatisfier(...).

Key semantics:
- eval_unit creates scored evidence. reach creates structural nodes, so never replace
  the scored candidate set with a reach result. project carries source evidence.
- Different concepts usually match different elements: retrieve them separately and
  connect them through graph edges instead of intersecting unrelated lexical hits.
- `direction="backward"` finds sources pointing at a matched target. For example,
  files referencing PageRequest are found by matching PageRequest, projecting
  backward over references/imports, then forward over in_file with kind="file".
"""

OPERATOR_SPEC += """\

Type and method hierarchy edges point from concrete to abstract: extends,
implements, and overrides. Use backward projection from an abstract type or
method to find implementations or overrides. These lightweight Java relations
may have confidence below 1.0.
"""

PROMPT = """\
You are generating a query script for a code retrieval system.

Codebase: {project} ({symbols} symbols, {edges} edges)
Query: {query}
Semantic judging: {judging}

{spec}

Search strategy and constraints:

1. Split the query into concepts. Use exact identifiers and concise query-derived
   synonyms, weighting the most discriminative terms more strongly.
2. Retrieve concepts broadly with eval_unit, then apply cheap kind/language filters.
3. Express stated relations with exact graph edges. Use project for one-hop result
   transformation, reach for neighbourhood signals, and hop only for required paths.
4. Preserve scored evidence and delay top() until cheap filtering/projection is done.
5. When judging is enabled and structural evidence still cannot decide correctness,
   call intent last with the full query-specific criterion and at most 60 candidates.
   When judging is disabled, do not call intent; return the best cheap result.

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
        vocabulary: Sequence[tuple[str, int]] = (),
        *,
        symbols: int,
        edges: int,
        judge_enabled: bool = False,
    ) -> str | None:
        """Generate a script without embedding the project vocabulary.

        ``vocabulary`` remains accepted for source compatibility with callers
        from before codegen stopped sending it to the model.
        """
        prompt = PROMPT.format(
            project=project,
            query=query,
            spec=OPERATOR_SPEC,
            symbols=f"{symbols:,}",
            edges=f"{edges:,}",
            judging="enabled" if judge_enabled else "disabled",
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
