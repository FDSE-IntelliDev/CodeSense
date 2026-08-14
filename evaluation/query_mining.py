"""Turn bounded portions of coding-agent traces into search queries."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any, Protocol

from evaluation.models import PreparedQuery, TraceCase, TraceEvent

__all__ = [
    "OpenAIQueryGenerator",
    "QueryGenerator",
    "build_prompt",
    "find_episodes",
    "is_search_event",
    "mine_queries",
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
_TOKEN = re.compile(r"[a-zA-Z][a-zA-Z0-9_-]{2,}")
_PATH_TOKENS = {
    "bin",
    "code",
    "find",
    "glob",
    "grep",
    "java",
    "main",
    "path",
    "rg",
    "ripgrep",
    "search",
    "src",
    "symbol",
    "test",
    "tests",
}


class QueryGenerator(Protocol):
    def generate(self, prompt: str) -> str | None:
        """Return model text for one query prompt."""


def is_search_event(event: TraceEvent) -> bool:
    if event.tool_output and not event.tool_input and not event.text:
        return False
    tool = (event.tool_name or "").strip().lower()
    if tool in _SEARCH_TOOLS:
        return True
    action = _action(event).lower()
    return bool(re.match(r"(?:rg|grep|find)\b", action))


def find_episodes(case: TraceCase) -> tuple[tuple[TraceEvent, ...], ...]:
    episodes: list[list[TraceEvent]] = []
    for event in case.events:
        if not is_search_event(event):
            continue
        if episodes and _same_episode(episodes[-1][-1], event):
            episodes[-1].append(event)
        else:
            episodes.append([event])
    return tuple(tuple(episode) for episode in episodes)


def build_prompt(case: TraceCase, episode: Sequence[TraceEvent], *, prompt_version: str) -> str:
    if not episode:
        raise ValueError("cannot build a prompt for an empty episode")
    anchor = episode[0].index
    prior = []
    for event in case.events:
        if event.index >= anchor:
            break
        rendered = _render_context(event)
        if rendered:
            prior.append(rendered)
    current = _action(episode[0])
    return f"""You create one code-search query for a Java repository.

Prompt version: {prompt_version}
Issue statement:
{case.issue_statement}

Trace context before the search action (tool outputs are intentionally omitted):
{chr(10).join(prior) or "(none)"}

Current raw search action:
{current}

Output one English natural-language query only. Describe the behavior, responsibility,
or code relationship to find. Do not output a shell command, a file path, a copied class
or method name, patch details, test results, or information from future trace events.
""".strip()


def mine_queries(
    case: TraceCase,
    generator: QueryGenerator,
    *,
    prompt_version: str = "trace-query-v1",
) -> tuple[PreparedQuery, ...]:
    found: list[PreparedQuery] = []
    for episode_index, episode in enumerate(find_episodes(case)):
        prompt = build_prompt(case, episode, prompt_version=prompt_version)
        extracted = _extract_query(episode[0])
        strategy = "extracted"
        query = extracted
        if query is None:
            strategy = "generated"
            query = _valid_query(generator.generate(prompt))
            if query is None:
                repair = (
                    prompt + "\nYour previous response was invalid. "
                    "Return one plain English sentence only."
                )
                query = _valid_query(generator.generate(repair))
        if query is None:
            continue
        provenance: dict[str, object] = {"prompt_version": prompt_version}
        provenance["prompt"] = prompt
        model = getattr(generator, "model", None)
        if model:
            provenance["model"] = model
        found.append(
            PreparedQuery(
                query_id=f"{case.trajectory_id}:{episode_index}",
                repo=case.repo,
                instance_id=case.instance_id,
                trajectory_id=case.trajectory_id,
                episode_index=episode_index,
                query=query,
                strategy=strategy,
                anchor_event=episode[0].index,
                raw_action=_action(episode[0]),
                source_events=case.events,
                provenance=provenance,
            )
        )
    return tuple(found)


def _same_episode(previous: TraceEvent, current: TraceEvent) -> bool:
    if current.index - previous.index > 2:
        return False
    previous_tool = (previous.tool_name or "").lower()
    current_tool = (current.tool_name or "").lower()
    if previous_tool != current_tool:
        return False
    previous_tokens = _intent_tokens(_action(previous))
    current_tokens = _intent_tokens(_action(current))
    if not previous_tokens or not current_tokens:
        return False
    return (
        len(previous_tokens & current_tokens) / min(len(previous_tokens), len(current_tokens))
        >= 0.5
    )


def _intent_tokens(action: str) -> set[str]:
    return {token.lower() for token in _TOKEN.findall(action) if token.lower() not in _PATH_TOKENS}


def _action(event: TraceEvent) -> str:
    return (event.tool_input or event.text or "").strip()


def _render_context(event: TraceEvent) -> str:
    if event.tool_output:
        return ""
    text = event.text.strip()
    if event.tool_name and event.tool_input:
        return f"[{event.role} tool={event.tool_name}] {event.tool_input}"
    return f"[{event.role}] {text}" if text else ""


def _extract_query(event: TraceEvent) -> str | None:
    if event.tool_name or not event.text.strip():
        return None
    return _valid_query(event.text)


def _valid_query(raw: str | None) -> str | None:
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    if text.startswith("{"):
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return None
        text = payload.get("query", "") if isinstance(payload, dict) else ""
    text = text.strip()
    if not text or "\n" in text or "/testbed" in text:
        return None
    lowered = text.lower()
    if re.match(r"(?:rg|grep|git\s+grep)\b", lowered):
        return None
    if "`" in text or re.search(r"(?:^|\s)/[^\s]+", text):
        return None
    return text


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
        except Exception:  # noqa: BLE001 -- one bad trace must not stop the batch
            return None


def _default_session() -> object:
    import requests

    return requests.Session()
