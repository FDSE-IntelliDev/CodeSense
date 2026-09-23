"""Deterministic search-episode and Java-location extraction from agent traces."""

from __future__ import annotations

import ast
import json
import re
import shlex
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath

from evaluation.models import ToolCall, TraceEvent

__all__ = [
    "CandidateLocation",
    "EpisodeDiscovery",
    "SearchEpisode",
    "find_search_episodes",
    "is_search_event",
    "normalize_action",
    "parse_episode_candidates",
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
_JAVA_LOCATION = re.compile(
    r"(?P<path>(?:[A-Za-z]:)?(?:/|\.?\.?/)?[^\s:,\"']+\.java)"
    r"(?::(?P<line>\d+))?(?::\s?(?P<text>.*))?"
)
_IDENTIFIER = re.compile(r"[A-Za-z_$][\w$]*")
_CONTROL_WORDS = {"catch", "do", "for", "if", "new", "return", "switch", "throw", "while"}
_IGNORED_DIRS = {"build", "example", "examples", "generated", "target", "test", "tests"}


@dataclass(frozen=True, slots=True)
class CandidateLocation:
    file: str
    functions: tuple[str, ...]
    line: int | None
    raw_result: str


@dataclass(frozen=True, slots=True)
class SearchEpisode:
    anchor_event: int
    action_event: TraceEvent
    tool_call: ToolCall
    context_events: tuple[TraceEvent, ...]
    result_events: tuple[TraceEvent, ...]
    raw_action: str
    candidates: tuple[CandidateLocation, ...] = ()


@dataclass(frozen=True, slots=True)
class EpisodeDiscovery:
    episodes: tuple[SearchEpisode, ...]
    skip_reasons: tuple[str, ...]


def is_search_event(event: TraceEvent) -> bool:
    """Return whether an assistant event contains at least one search call."""
    if event.role.lower() in {"system", "user", "tool"}:
        return False
    return any(_is_search_call(call) for call in _event_calls(event))


def find_search_episodes(events: Sequence[TraceEvent]) -> EpisodeDiscovery:
    """Bind each recognized search call to its result without executing trace input."""
    episodes: list[SearchEpisode] = []
    skipped: list[str] = []
    for position, event in enumerate(events):
        calls = tuple(call for call in _event_calls(event) if _is_search_call(call))
        if not calls:
            continue
        results = _following_tool_results(events, position)
        identified = bool(calls) and all(call.id for call in calls)
        result_ids = bool(results) and all(result.tool_call_id for result in results)
        if len(calls) > 1 and not (identified and result_ids):
            skipped.append("ambiguous_tool_result")
            continue
        for call in calls:
            bound = (
                tuple(result for result in results if result.tool_call_id == call.id)
                if call.id and result_ids
                else results
            )
            context = _prompt_context(events, position)
            episodes.append(
                SearchEpisode(
                    anchor_event=event.index,
                    action_event=event,
                    tool_call=call,
                    context_events=context,
                    result_events=bound,
                    raw_action=(call.arguments or event.tool_input or event.text).strip(),
                )
            )
    return EpisodeDiscovery(tuple(episodes), tuple(skipped))


def normalize_action(action: str) -> str:
    """Collapse presentation-only whitespace in a trace action."""
    return " ".join(action.split())


def parse_episode_candidates(episode: SearchEpisode, repo: str) -> tuple[CandidateLocation, ...]:
    """Parse production Java locations from this episode's result events."""
    del repo  # Workspace normalization is independent of the GitHub slug.
    by_file: dict[str, CandidateLocation] = {}
    allow_functions = episode.tool_call.name.lower() != "find"
    for event in episode.result_events:
        for raw_line in (event.tool_output or event.text).splitlines():
            match = _JAVA_LOCATION.search(raw_line.strip())
            if not match:
                continue
            file = _normalize_java_path(match.group("path"))
            if not file or not _is_production_java(file):
                continue
            line = int(match.group("line")) if match.group("line") else None
            function = _declared_function(match.group("text") or "") if allow_functions else None
            previous = by_file.get(file)
            functions = list(previous.functions if previous else ())
            if function and function not in functions:
                functions.append(function)
            by_file[file] = CandidateLocation(
                file=file,
                functions=tuple(functions),
                line=previous.line if previous and previous.line is not None else line,
                raw_result=previous.raw_result if previous else raw_line.strip(),
            )
    return tuple(by_file.values())


def _event_calls(event: TraceEvent) -> tuple[ToolCall, ...]:
    tool_calls = getattr(event, "tool_calls", ())
    if tool_calls:
        return tuple(tool_calls)
    tool_name = getattr(event, "tool_name", None)
    if tool_name:
        return (
            ToolCall(
                getattr(event, "tool_call_id", None),
                tool_name,
                getattr(event, "tool_input", None) or getattr(event, "text", ""),
            ),
        )
    return ()


def _is_search_call(call: ToolCall) -> bool:
    if call.name.strip().lower() in _SEARCH_TOOLS:
        return True
    return _contains_search_command((call.arguments or "").strip())


def _contains_search_command(action: str) -> bool:
    pending = [action]
    seen: set[str] = set()
    while pending:
        candidate = pending.pop().strip()
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        if len(candidate) >= 2 and candidate[0] == candidate[-1] == "'":
            pending.append(candidate[1:-1])
            continue
        try:
            command = shlex.split(candidate)[0].lower()
        except (ValueError, IndexError):
            command = ""
        if command in {"find", "grep", "rg"}:
            return True
        parsed = _parse_action(candidate)
        if isinstance(parsed, str):
            pending.append(parsed)
        elif isinstance(parsed, Mapping):
            pending.extend(
                value for key in _COMMAND_KEYS if isinstance((value := parsed.get(key)), str)
            )
    return False


def _parse_action(value: str) -> object | None:
    for parser in (json.loads, ast.literal_eval):
        try:
            return parser(value)
        except (ValueError, SyntaxError, TypeError):
            continue
    return None


def _following_tool_results(events: Sequence[TraceEvent], position: int) -> tuple[TraceEvent, ...]:
    found: list[TraceEvent] = []
    for event in events[position + 1 :]:
        role = event.role.lower()
        if role in {"assistant", "user", "system"}:
            break
        if role == "tool" or event.tool_output is not None:
            found.append(event)
    return tuple(found)


def _prompt_context(events: Sequence[TraceEvent], position: int) -> tuple[TraceEvent, ...]:
    action = events[position]
    prior = next(
        (
            event
            for event in reversed(events[:position])
            if event.role.lower() == "assistant" and event.text.strip()
        ),
        None,
    )
    return (prior, action) if prior is not None else (action,)


def _normalize_java_path(path: str) -> str:
    value = path.strip().replace("\\", "/")
    if "/workspace/" in value:
        value = value.partition("/workspace/")[2]
        value = value.split("/", 1)[1] if "/" in value else ""
    value = value.removeprefix("./")
    if value.startswith(("a/", "b/")):
        value = value[2:]
    return value.lstrip("/")


def _is_production_java(path: str) -> bool:
    parsed = PurePosixPath(path)
    if parsed.suffix.lower() != ".java":
        return False
    if any(part.lower() in _IGNORED_DIRS for part in parsed.parts[:-1]):
        return False
    return re.search(r"(?:Test|Tests|TestCase|IT)\.java$", parsed.name) is None


def _declared_function(line: str) -> str | None:
    text = line.strip()
    if not text or text.startswith(("//", "/*", "*", "@")) or "(" not in text:
        return None
    before, after = text.split("(", 1)
    if "." in before or "=" in before or "->" in text:
        return None
    words = _IDENTIFIER.findall(before)
    if not words or words[-1] in _CONTROL_WORDS:
        return None
    if len(words) == 1 and "{" not in after:
        return None
    return words[-1]
