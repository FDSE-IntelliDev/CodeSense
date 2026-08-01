"""An intent judge over an OpenAI-compatible endpoint.

Implements the `codesense.ql.judge.Judge` port. The QL layer does not know
it exists -- the dependency runs llm to ql, and the reverse would break QL's
standard-library-only contract.

It hits ``/chat/completions`` with `requests` rather than pulling in the
openai SDK: dashscope, vLLM and Ollama all speak this interface, and one
fewer dependency is one fewer version conflict.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Sequence

from codesense.llm.config import LlmConfig
from codesense.ql.frag import Verdict
from codesense.ql.judge import UNSURE, Judge, JudgeItem

__all__ = ["PROMPT", "OpenAICompatibleJudge"]

_log = logging.getLogger(__name__)

PROMPT = """\
You are judging whether code elements satisfy an intent.

Intent: {concept}

Candidate elements:
{items}

Judge each candidate. Output a JSON array and nothing else:
[{{"id": <the candidate's id>, "label": "yes|no|unsure",
  "score": <confidence from 0 to 1>, "reason": "<one sentence>"}}]

Requirements:
- give "unsure" when you cannot tell; **do not** guess just to have an answer
- reason must cite specific evidence (name, signature, enclosing class), not
  restate the intent
- exactly one row per candidate, using the ids given above
"""

#: Models often wrap JSON in a ```json fence even when told not to.
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


class OpenAICompatibleJudge(Judge):
    """Calls an OpenAI-compatible chat/completions endpoint.

    ``session`` is injectable, so tests can pass a fake and need no network.
    """

    def __init__(self, config: LlmConfig, session: object | None = None) -> None:
        self._config = config
        self._session = session

    def judge(self, concept: str, items: Sequence[JudgeItem]) -> dict[int, Verdict]:
        if not items:
            return {}
        content = self._ask(PROMPT.format(concept=concept, items=_render(items)))
        if content is None:
            return {}
        return _parse(content, {item.symbol_id for item in items})

    def _ask(self, prompt: str) -> str | None:
        session = self._session or _default_session()
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
        except Exception:  # noqa: BLE001 -- judging must degrade; intent decides how
            # Do not put the exception text in the log body: the headers carry
            # the key and some libraries copy them into the exception.
            _log.exception("judge request failed")
            return None


def _default_session() -> object:
    import requests

    return requests.Session()


def _render(items: Sequence[JudgeItem]) -> str:
    lines = []
    for item in items:
        parts = [f"id={item.symbol_id}", f"{item.kind} {item.name}"]
        if item.container:
            parts.append(f"in {item.container}")
        if item.signature:
            parts.append(f"signature {item.signature}")
        if item.doc:
            parts.append(f"doc {item.doc[:200]}")
        lines.append("- " + "; ".join(parts))
    return "\n".join(lines)


def _parse(content: str, known: set[int]) -> dict[int, Verdict]:
    """Dig the verdicts out of the model's output.

    Tolerates ```json fences and surrounding chatter -- models routinely
    ignore "output JSON only". A parse failure returns nothing and lets
    `intent` degrade, rather than raising and killing the whole query.
    """
    payload = _FENCE.search(content)
    text = payload.group(1) if payload else content
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end <= start:
        _log.warning("no JSON array found in the judge output")
        return {}
    try:
        rows = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        _log.warning("judge output is not valid JSON")
        return {}

    found: dict[int, Verdict] = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        try:
            symbol_id = int(row["id"])
        except (KeyError, TypeError, ValueError):
            continue
        if symbol_id not in known:
            continue  # when the model garbles ids, keep it out of the result
        found[symbol_id] = Verdict(
            source="llm",
            label=str(row.get("label") or UNSURE).strip().lower(),
            reason=str(row.get("reason") or ""),
            score=_score(row.get("score")),
        )
    return found


def _score(raw: object) -> float:
    try:
        return min(max(float(raw), 0.0), 1.0)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
