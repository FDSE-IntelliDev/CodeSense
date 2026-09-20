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

import logging
from collections.abc import Sequence

from pydantic import ValidationError

from codesense.llm.config import LlmConfig
from codesense.llm.schema import QueryUnderstandingResult, query_understanding_response_format

__all__ = ["PROMPT", "QueryUnderstanding"]

_log = logging.getLogger(__name__)

PROMPT = """\
Interpret this query for a code retrieval system.
Return exactly one JSON object matching the supplied response schema.

Codebase: {project}
Query: {query}

Representative terms repeatedly used in this project. The number after each
term is how many code elements contain it. Prefer project terminology when
useful, but you may emit terms outside this sample; later grounding maps
semantic terms to project spellings:
{vocab}

Requirements:
- Divide the query into named semantic units. Keep one unit when the query is
  about one thing; split only when it genuinely names distinct concepts.
- Each unit must include the query phrases it represents and useful semantic
  terms. Mark a term literal when the query says it directly, synonym when it
  is interchangeable, and derived when it is project- or code-related but not
  interchangeable. Give every term a relevance weight and concise reason.
- Avoid generic terms such as get, set, and value.
- Use relations only when the query explicitly means calls, contains,
  references, imports, in_file, extends, implements, or overrides. Hierarchy
  edges point from the concrete declaration to its abstract declaration. Both
  endpoints normally name units. When
  the requested answer is the unknown side of a relation, use exactly one
  result endpoint to state whether the returned objects are the source or the
  target of that relation.
- Targets are hard result kinds. Supported meanings are file, type, class,
  interface, enum, record, function, method, constructor, field, and
  annotation. Use an empty target list when the query does not request a kind.
- Annotations may name framework annotations absent from the vocabulary.
- The criterion must be specific and falsifiable for optional result judging.
"""


class QueryUnderstanding:
    """Interpret the query. Structure and optimisation belong to
    `codesense.ql.compile`."""

    def __init__(self, config: LlmConfig, session: object | None = None) -> None:
        self._config = config
        self._session = session

    def understand(
        self,
        query: str,
        project: str,
        vocabulary: Sequence[tuple[str, int]],
    ) -> QueryUnderstandingResult | None:
        """Interpret: pick terms, score them, group by meaning, state
        relations, write a criterion.

        **These are all proposals** -- groups and relations take effect only
        after passing `codesense.ql.compile.validate`'s statistical checks.

        Returns None on failure; the caller decides whether to degrade or
        give up.
        """
        message = self._ask(
            PROMPT.format(project=project, query=query, vocab=_format_vocabulary(vocabulary))
        )
        if message is None:
            return None
        if message.get("refusal"):
            _log.warning("query understanding was refused")
            return None
        content = message.get("content")
        if not isinstance(content, str) or not content:
            _log.warning("query understanding response has no content")
            return None
        try:
            return QueryUnderstandingResult.model_validate_json(content)
        except ValidationError:
            _log.warning("query understanding schema validation failed")
            return None

    def _ask(self, prompt: str) -> dict[str, object] | None:
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
                    "response_format": query_understanding_response_format(),
                },
                timeout=self._config.timeout,
            )
            response.raise_for_status()
            message = response.json()["choices"][0]["message"]
            if not isinstance(message, dict):
                raise TypeError("response message is not an object")
            return message
        except Exception:  # noqa: BLE001 -- compilation must degrade, not kill the query
            _log.exception("compilation request failed")
            return None


def _format_vocabulary(vocabulary: Sequence[tuple[str, int]]) -> str:
    """Render bounded project context while tolerating old in-process callers."""
    rendered: list[str] = []
    for item in vocabulary:
        if isinstance(item, str):
            rendered.append(item)
        else:
            term, document_frequency = item
            rendered.append(f"{term}:{document_frequency}")
    return ", ".join(rendered)
