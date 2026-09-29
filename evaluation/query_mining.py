"""Generate semantic code-search queries from useful trace search episodes."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from evaluation.models import PreparedQuery, TraceCase, TraceEvent
from evaluation.trace_search import (
    SupervisedEpisode,
    extract_trace_answer,
    is_search_event,
    supervise_search_episodes,
)

__all__ = [
    "GeneratedQuery",
    "MiningBatch",
    "MiningOutcome",
    "OpenAIQueryGenerator",
    "QueryGenerator",
    "build_prompt",
    "contains_shell_or_java_path",
    "is_pure_direct_lookup",
    "is_search_event",
    "mine_queries",
    "mine_query",
    "search_context",
]

_SHELL_OR_PATH = re.compile(r"(?:^|\s)(?:rg|grep|git\s+grep)\b|[\w./-]+\.java\b", re.IGNORECASE)
_PURE_DIRECT_LOOKUP = re.compile(
    r"\s*(?:find|list|locate|search for)\s+(?:all\s+)?(?:java\s+)?"
    r"(?:files?\s+(?:that\s+)?(?:contain|reference)|implementations?\s+of|"
    r"calls?\s+to|(?:class|method)\s+named)\b[^,.]*[.]?\s*",
    re.IGNORECASE,
)


class QueryGenerator(Protocol):
    def generate(self, prompt: str) -> str | None:
        """Return model text for one semantic-query prompt."""


@dataclass(frozen=True, slots=True)
class GeneratedQuery:
    query: str
    reason: str
    anchor_terms: tuple[str, ...]
    semantic_constraints: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MiningBatch:
    queries: tuple[PreparedQuery, ...]
    skip_reasons: tuple[str, ...]
    search_episode_count: int
    eligible_episode_count: int


@dataclass(frozen=True, slots=True)
class MiningOutcome:
    """Compatibility view for callers that still consume one result."""

    query: PreparedQuery | None
    skip_reason: str | None = None


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


def build_prompt(
    case: TraceCase,
    episode: SupervisedEpisode | None = None,
    *,
    prompt_version: str,
) -> str:
    """Expose issue, pre-search reasoning, and action but no answer-side event."""
    if not case.events:
        raise ValueError("cannot build a prompt for an empty trace")
    context = episode.episode.context_events if episode is not None else search_context(case.events)
    rendered = [text for event in context if (text := _render_prompt_event(event))]
    return f"""You create one semantic code-search query for a Java repository.

Prompt version: {prompt_version}
Issue statement:
{case.issue_statement}

Reasoning immediately before this search and the current search action:
{chr(10).join(rendered) or "(none)"}

A good query describes behavior, responsibility, state transitions, side effects,
performance impact, or a failure mechanism. Exact technical, class, or method anchors
from the action are allowed, but the query must also say what behavior or effect matters.
A good query should be concise (less than 20 words): use one short sentence that keeps
only the core search intent, without repeating issue background, explanations, or
implementation steps.

The following examples are fictional. Learn the distinction, but do not reuse their names
or wording in the generated query.

Good: Find where ZephyrQueue delays producers to keep buffered tasks within its memory budget.
Good: Find the logic that can leave lunar-map tiles stale after an ObservatorySession changes
regions.
Good: Find code that batches telemetry envelopes to reduce radio wake-ups without delaying
urgent signals.

Bad: Find Java files that reference ZephyrQueue.
Bad: Find implementations of LunarTileStore.
Bad: Locate calls to flushTelemetry.
Bad: Search for ObservatorySession.java.

Return one JSON object only:
{{"status":"valid","query":"...","reason":"...","anchor_terms":["..."],
"semantic_constraints":["..."]}}
`anchor_terms` may be empty. `semantic_constraints` must contain at least one behavioral,
state, failure, performance, responsibility, or side-effect constraint.
If this search cannot support such a query, return {{"status":"invalid trace"}}.
Do not return a result path, shell command, code fence, answer, or future-trace information.
""".strip()


def mine_queries(
    case: TraceCase,
    generator: QueryGenerator,
    *,
    prompt_version: str = "trace-search-v3",
) -> MiningBatch:
    """Generate one query per supervised episode and isolate episode failures."""
    supervision = supervise_search_episodes(case)
    trace_answer = extract_trace_answer(case)
    queries: list[PreparedQuery] = []
    skipped = list(supervision.skip_reasons)
    for supervised in supervision.episodes:
        episode = supervised.episode
        prompt = build_prompt(case, supervised, prompt_version=prompt_version)
        generated, error = _parse_result(generator.generate(prompt))
        if generated is None:
            skipped.append(error or "invalid_model_json")
            continue
        if error := _query_error(generated):
            skipped.append(error)
            continue
        provenance: dict[str, object] = {
            "prompt_version": prompt_version,
            "prompt": prompt,
            "raw_action": episode.raw_action,
            "query_reason": generated.reason,
            "candidate_count": len(supervised.candidate_answers),
        }
        if model := getattr(generator, "model", None):
            provenance["model"] = model
        queries.append(
            PreparedQuery(
                query_id=f"{case.trajectory_id}:{episode.anchor_event}",
                repo=case.repo,
                instance_id=case.instance_id,
                trajectory_id=case.trajectory_id,
                issue_statement=case.issue_statement,
                query=generated.query,
                answer=supervised.answer,
                base_commit=case.base_commit,
                trace_answer=trace_answer,
                candidate_answers=supervised.candidate_answers,
                usage_evidence=supervised.usage_evidence,
                anchor_terms=generated.anchor_terms,
                semantic_constraints=generated.semantic_constraints,
                source_event_indices=tuple(event.index for event in episode.context_events),
                result_event_indices=tuple(event.index for event in episode.result_events),
                strategy="trace-search-generated",
                source_events=case.events,
                provenance=provenance,
            )
        )
    return MiningBatch(
        tuple(queries),
        tuple(skipped),
        supervision.search_episode_count,
        len(supervision.episodes),
    )


def mine_query(
    case: TraceCase,
    generator: QueryGenerator,
    *,
    prompt_version: str = "trace-search-v3",
) -> MiningOutcome:
    """Return the first batch result for transitional single-row callers."""
    batch = mine_queries(case, generator, prompt_version=prompt_version)
    if batch.queries:
        return MiningOutcome(batch.queries[0])
    return MiningOutcome(None, batch.skip_reasons[0] if batch.skip_reasons else None)


def contains_shell_or_java_path(query: str) -> bool:
    return bool(_SHELL_OR_PATH.search(query))


def is_pure_direct_lookup(query: str) -> bool:
    return bool(_PURE_DIRECT_LOOKUP.fullmatch(query))


def _query_error(generated: GeneratedQuery) -> str | None:
    if not generated.semantic_constraints:
        return "missing_semantic_constraint"
    if contains_shell_or_java_path(generated.query):
        return "query_leaks_result"
    if is_pure_direct_lookup(generated.query):
        return "direct_lookup_query"
    return None


def _render_prompt_event(event: TraceEvent) -> str:
    label = f"[event {event.index} role={event.role}"
    if event.tool_name:
        label += f" tool={event.tool_name}"
    parts = [label + "]"]
    if event.text.strip():
        parts.append(f"text: {_clip(event.text)}")
    if event.tool_input:
        parts.append(f"input: {_clip(event.tool_input)}")
    return " ".join(parts) if len(parts) > 1 else ""


def _clip(text: str, limit: int = 4000) -> str:
    value = text.strip()
    return value if len(value) <= limit else value[:limit] + "... [truncated]"


def _parse_result(raw: str | None) -> tuple[GeneratedQuery | None, str | None]:
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
    anchors = _string_tuple(payload.get("anchor_terms"))
    constraints = _string_tuple(payload.get("semantic_constraints"))
    if (
        status != "valid"
        or not isinstance(query, str)
        or not isinstance(reason, str)
        or anchors is None
        or constraints is None
    ):
        return None, "invalid_model_json"
    query = query.strip()
    reason = reason.strip()
    if not query or not reason:
        return None, "invalid_model_json"
    return GeneratedQuery(query, reason, anchors, constraints), None


def _string_tuple(value: object) -> tuple[str, ...] | None:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        return None
    return tuple(item.strip() for item in value if item.strip())


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
        except Exception as exc:  # noqa: BLE001 -- one bad episode must not stop the batch
            print(exc)
            return None


def _default_session() -> object:
    import requests

    return requests.Session()
