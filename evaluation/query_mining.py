"""Turn Java coding-agent traces into one semantic code-search query."""

from __future__ import annotations

import ast
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Protocol

from evaluation.models import CodeLocation, PreparedQuery, TraceCase, TraceEvent

__all__ = [
    "MiningOutcome",
    "OpenAIQueryGenerator",
    "QueryGenerator",
    "build_prompt",
    "is_search_event",
    "mine_query",
    "search_context",
]

_SEARCH_TOOLS = {
    "code_search",
    "find",
    "grep",
    "ripgrep",
    "rg",
    "search",
    "search_code",
    "symbol_search",
}
_COMMAND_KEYS = ("command", "cmd", "shell")
_DIRECT_LOOKUP = re.compile(
    r"\b(?:files?\s+(?:that\s+)?(?:contain|reference)|implementations?\s+of|"
    r"calls?\s+to|(?:class|method)\s+named)\b",
    re.IGNORECASE,
)


class QueryGenerator(Protocol):
    def generate(self, prompt: str) -> str | None:
        """Return model text for one semantic-query prompt."""


@dataclass(frozen=True, slots=True)
class _GeneratedQuery:
    query: str
    reason: str


@dataclass(frozen=True, slots=True)
class MiningOutcome:
    query: PreparedQuery | None
    skip_reason: str | None = None


def is_search_event(event: TraceEvent) -> bool:
    """Return whether an assistant event invokes a code-search tool or command."""
    if event.role.lower() in {"system", "user"}:
        return False
    if event.tool_output and not event.tool_input and not event.text:
        return False
    tool = (event.tool_name or "").strip().lower()
    if tool in _SEARCH_TOOLS:
        return True
    return _contains_search_command(_action(event).lower())


def search_context(events: Sequence[TraceEvent]) -> tuple[TraceEvent, ...]:
    """Keep each search event and its immediate assistant/tool neighbors."""
    search_positions = {position for position, event in enumerate(events) if is_search_event(event)}
    positions = {
        neighbor
        for position in search_positions
        for neighbor in (position - 1, position, position + 1)
        if 0 <= neighbor < len(events)
    }
    return tuple(
        event
        for position, event in enumerate(events)
        if position in positions and event.role.lower() in {"assistant", "tool"}
    )


def build_prompt(case: TraceCase, *, prompt_version: str) -> str:
    """Build the one-call prompt without exposing the reference patch."""
    if not case.events:
        raise ValueError("cannot build a prompt for an empty trace")
    context = search_context(case.events)
    rendered_context = [rendered for event in context if (rendered := _render_context(event))]
    search_hints = [event.index for event in case.events if is_search_event(event)]
    return f"""You create one semantic code-search query for a Java repository.

Prompt version: {prompt_version}
Issue statement:
{case.issue_statement}

Search-related assistant/tool trace windows:
{chr(10).join(rendered_context) or "(none)"}

Search event hints: {search_hints or "(none)"}

A good semantic query describes behavior, responsibility, state transitions, side effects,
performance impact, or a failure mechanism. Relevant code should not be obtainable merely by
copying a path, class, method, reference, implementation, or call relation from the trace.

Good example:
Find the logic that can leave navigation state inconsistent when switching between
cursor-based and page-based access.
Why: it describes a state transition and failure mode without revealing code identifiers.

Good example:
Find functions whose behavior can affect disk performance.
Why: relevant code may say I/O, buffering, flushing, persistence, synchronization, or storage
without containing the words disk performance.

Bad examples:
Find Java files that reference PageRequest.
List Java files containing getDoubleEvaluation.
Find implementations of LoadBalance.
Locate calls to isPoolLifo.
Search for RetryUtils.java.

Return one JSON object only:
{{"status":"valid","query":"...","reason":"..."}}
If no semantic query can be supported by the issue and trace, return:
{{"status":"invalid trace"}}
Do not return files, functions, answers, shell commands, code fences, or multiple queries.
""".strip()


def mine_query(
    case: TraceCase,
    generator: QueryGenerator,
    *,
    prompt_version: str = "semantic-query-v1",
) -> MiningOutcome:
    """Generate and validate one semantic query, then attach deterministic patch gold."""
    if case.gold_error:
        return MiningOutcome(None, case.gold_error)
    if not case.answer:
        return MiningOutcome(None, "no_production_java_gold")
    context = search_context(case.events)
    if not context:
        return MiningOutcome(None, "no_search_events")

    prompt = build_prompt(case, prompt_version=prompt_version)
    generated, error = _parse_result(generator.generate(prompt))
    if generated is None:
        return MiningOutcome(None, error)
    if error := _query_error(generated.query, case.answer):
        return MiningOutcome(None, error)

    provenance: dict[str, object] = {
        "prompt_version": prompt_version,
        "prompt": prompt,
        "query_reason": generated.reason,
        "search_event_indices": [event.index for event in case.events if is_search_event(event)],
    }
    if model := getattr(generator, "model", None):
        provenance["model"] = model
    return MiningOutcome(
        PreparedQuery(
            query_id=case.trajectory_id,
            repo=case.repo,
            instance_id=case.instance_id,
            trajectory_id=case.trajectory_id,
            issue_statement=case.issue_statement,
            query=generated.query,
            answer=case.answer,
            source_event_indices=tuple(event.index for event in context),
            strategy="semantic-generated",
            source_events=case.events,
            provenance=provenance,
        )
    )


def _action(event: TraceEvent) -> str:
    return (event.tool_input or event.text or "").strip()


def _contains_search_command(action: str) -> bool:
    """Recognize search commands in raw, quoted, JSON, or Python-literal tool input."""
    pending = [action]
    seen: set[str] = set()
    while pending:
        candidate = pending.pop().strip()
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        if re.match(r"(?:rg|grep|find)\b", candidate):
            return True
        if len(candidate) >= 2 and candidate[0] == candidate[-1] == "'":
            pending.append(candidate[1:-1])
            continue
        parsed = _parse_action(candidate)
        if isinstance(parsed, str):
            pending.append(parsed)
        elif isinstance(parsed, Mapping):
            for key in _COMMAND_KEYS:
                value = parsed.get(key)
                if isinstance(value, str):
                    pending.append(value)
    return False


def _parse_action(value: str) -> object | None:
    for parser in (json.loads, ast.literal_eval):
        try:
            return parser(value)
        except (ValueError, SyntaxError, TypeError):
            continue
    return None


def _render_context(event: TraceEvent) -> str:
    label = f"[event {event.index} role={event.role}"
    if event.tool_name:
        label += f" tool={event.tool_name}"
    parts = [label + "]"]
    if event.text.strip():
        parts.append(f"text: {_clip(event.text)}")
    if event.tool_input:
        parts.append(f"input: {_clip(event.tool_input)}")
    if event.tool_output:
        parts.append(f"output: {_clip(event.tool_output)}")
    return " ".join(parts) if len(parts) > 1 else ""


def _clip(text: str, limit: int = 4000) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit] + "... [truncated]"


def _parse_result(raw: str | None) -> tuple[_GeneratedQuery | None, str | None]:
    if not isinstance(raw, str):
        return None, "generator_failed"
    payload = _json_payload(raw)
    if not isinstance(payload, Mapping):
        return None, "invalid_model_json"
    status = str(payload.get("status") or "").strip().lower().replace("_", " ")
    if status == "invalid trace":
        return None, "non_semantic_query"
    query = payload.get("query")
    reason = payload.get("reason")
    if status != "valid" or not isinstance(query, str):
        return None, "invalid_model_json"
    query = query.strip()
    reason = reason.strip() if reason else "No reson outputs"
    if not query or not reason:
        return None, "invalid_model_json"
    return _GeneratedQuery(query, reason), None


def _json_payload(raw: str) -> object | None:
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        body = lines[1:-1] if len(lines) > 1 and lines[-1].strip() == "```" else lines[1:]
        text = "\n".join(body)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _query_error(query: str, answer: Sequence[CodeLocation]) -> str | None:
    if "\n" in query or "`" in query or re.search(r"(?:^|\s)/[^\s]+", query):
        return "non_semantic_query"
    if re.match(r"\s*(?:rg|grep|git\s+grep)\b", query):
        return "non_semantic_query"
    if re.match(r"\s*find\s+(?:/|\.|-)", query):
        return "non_semantic_query"
    if re.search(r"\b[\w./-]+\.java\b", query, re.IGNORECASE) or _DIRECT_LOOKUP.search(query):
        return "non_semantic_query"

    identifiers = {
        identifier
        for location in answer
        for identifier in (PurePosixPath(location.file).stem, *location.functions)
        if identifier
    }
    for identifier in identifiers:
        # Lowercase prose such as "navigation" is not an exact Java identifier ``Navigation``.
        if re.search(rf"(?<![\w$]){re.escape(identifier)}(?![\w$])", query):
            return "query_leaks_gold_identifier"
    return None


class OpenAIQueryGenerator:
    """Minimal OpenAI-compatible query generator with an injectable session."""

    def __init__(self, config: Any, session: object | None = None) -> None:
        self._config = config
        self._session = session
        self.model = config.model

    def generate(self, prompt: str) -> str | None:
        session = self._session or _default_session()
        try:
            response = session.post(
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
        except Exception as exc:  # noqa: BLE001 -- one bad trace must not stop the batch
            print(exc)
            return None


def _default_session() -> object:
    import requests

    return requests.Session()
