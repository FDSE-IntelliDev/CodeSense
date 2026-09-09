"""Natural language to related terms plus a judging criterion.

**The division of labour**: the model does NLP, statistics does optimisation.

The model **proposes**, statistics **validates**, statistics
**parameterises** -- three stages, not a choice between two.

Here the model does what it is good at: read the query, pick relevant terms,
group them by meaning, say whether the query means "A-related code calls
B-related code", and write one falsifiable sentence of intent. All of that is
reading comprehension.

But its proposals **do not take effect directly**: groups must pass a
cohesion check (do the terms in a group really land on the same symbols?),
relations must pass an edge-density check (does the relation hold in this
codebase?), and kind preferences and execution order are never asked of it at
all -- those are statistical questions
(`codesense.ql.compile.validate` / `build` / `planner`).

Both extremes were tried and neither works: let the model decide everything
and R@100 is 21%; keep it away from structure entirely and it is 47% but the
graph goes unused. **Proposing is a semantic problem, validating is a
statistical one.**

This lives in `codesense.llm` rather than `codesense.ql` because the QL layer
is contractually standard-library only.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence

from codesense.llm.config import LlmConfig
from codesense.ql.compile.spec import normalise_target

__all__ = ["PROMPT", "QueryUnderstanding"]

_log = logging.getLogger(__name__)

PROMPT = """\
You are interpreting a query for a code retrieval system.

Codebase: {project}
Query: {query}

Words that occur in this codebase (**terms may only be drawn from this
list**):
{vocab}

Output JSON:
{{
  "terms": {{"word": relevance from 0 to 1, ...}},
  "groups": {{"group name": ["word1", "word2", ...], ...}},
  "relations": [{{"src": "group A", "dst": "group B", "edge": ["references"]}}, ...],
  "target": ["file"],
  "annotations": ["@AnnotationName", ...],
  "concept": "one sentence of criterion, used to judge each piece of code"
}}

Requirements:
- pick 15-30 terms, all from the list above, scored by relevance (0.8-1.0 for
  ones pointing directly at the query's intent, 0.3-0.6 for indirect ones)
- do not pick generic words like get/set/value
- group the terms by meaning. **If the query is about one thing, give one
  group**; split only when the query really is about two different things
  (for instance "performance" and "disk" are two things)
- propose relations whenever the query explicitly expresses a relation
  between groups (for example, A-related code calls, contains, references,
  imports, or in_file B-related code); use one or more supported edge kinds.
  Otherwise give an empty list. **These proposals are checked against the
  codebase's actual edges and fabricated ones are discarded**
- valid edge names are calls, contains, references, imports, and in_file;
  use the exact edge kind requested by the query
- set target to ["file"] only when the query explicitly asks for files. A
  relation verb such as references or imports alone does not imply a file
  target; otherwise use an empty list
- annotations may name framework annotations absent from the vocabulary; give
  an empty list if there are none
- concept must be specific and falsifiable, not a restatement of the query
"""


def _groups(raw: object, terms: dict[str, float]) -> dict[str, list[str]]:
    """The model's groups, keeping only words that are actually in terms."""
    if not isinstance(raw, dict):
        return {}
    found: dict[str, list[str]] = {}
    for name, members in raw.items():
        if not isinstance(members, list):
            continue
        kept = [str(m).lower() for m in members if isinstance(m, str) and str(m).lower() in terms]
        if kept:
            found[str(name)] = kept
    return found


_DEFAULT_EDGES = ("calls", "contains")
_VALID_EDGES = frozenset((*_DEFAULT_EDGES, "references", "imports", "in_file"))


def _relations(raw: object) -> list[tuple[str, str, tuple[str, ...]]]:
    if not isinstance(raw, list):
        return []
    found: list[tuple[str, str, tuple[str, ...]]] = []
    for item in raw:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            src, dst = item[0], item[1]
            edge = item[2] if len(item) >= 3 else None
        elif isinstance(item, dict) and "src" in item and "dst" in item:
            src, dst = item["src"], item["dst"]
            edge = item.get("edge")
        else:
            continue
        if not isinstance(src, str) or not isinstance(dst, str):
            continue
        if isinstance(edge, str):
            edge_values = (edge,)
        elif isinstance(edge, (list, tuple)):
            edge_values = edge
        else:
            edge_values = ()
        valid = tuple(
            name.strip().lower()
            for name in edge_values
            if isinstance(name, str) and name.strip().lower() in _VALID_EDGES
        )
        # Preserve order while avoiding duplicate edge filters. A wholly
        # malformed edge value falls back to the legacy broad relation.
        valid = tuple(dict.fromkeys(valid)) or _DEFAULT_EDGES
        found.append((src, dst, valid))
    return found


def _score(raw: object) -> float:
    try:
        return min(max(float(raw), 0.0), 1.0)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 1.0


class QueryUnderstanding:
    """Interpret the query. Structure and optimisation belong to
    `codesense.ql.compile`."""

    def __init__(self, config: LlmConfig, session: object | None = None) -> None:
        self._config = config
        self._session = session

    def understand(self, query: str, project: str, vocabulary: Sequence[str]) -> dict | None:
        """Interpret: pick terms, score them, group by meaning, state
        relations, write a criterion.

        **These are all proposals** -- groups and relations take effect only
        after passing `codesense.ql.compile.validate`'s statistical checks.

        Returns None on failure; the caller decides whether to degrade or
        give up.
        """
        content = self._ask(
            PROMPT.format(project=project, query=query, vocab=", ".join(vocabulary))
        )
        if content is None:
            return None
        start, end = content.find("{"), content.rfind("}")
        if start < 0 or end <= start:
            _log.warning("no JSON found in the compilation output")
            return None
        try:
            payload = json.loads(content[start : end + 1])
        except json.JSONDecodeError:
            _log.warning("compilation output is not valid JSON")
            return None
        terms = payload.get("terms")
        if isinstance(terms, list):  # the model occasionally gives a list, not a scored dict
            terms = {str(t): 1.0 for t in terms if isinstance(t, str)}
        if not isinstance(terms, dict) or not terms:
            _log.warning("the model gave no usable terms")
            return None
        scored = {str(k).lower(): _score(v) for k, v in terms.items()}
        understood = {
            "terms": scored,
            "groups": _groups(payload.get("groups"), scored),
            "relations": _relations(payload.get("relations")),
            "annotations": [a for a in payload.get("annotations", ()) if isinstance(a, str)],
            "concept": str(payload.get("concept") or ""),
        }
        # Missing means "the model did not decide", while an explicit empty
        # target is a decision that must suppress later query-text inference.
        if "target" in payload:
            understood["target"] = normalise_target(payload["target"])
        return understood

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
        except Exception:  # noqa: BLE001 -- compilation must degrade, not kill the query
            _log.exception("compilation request failed")
            return None
