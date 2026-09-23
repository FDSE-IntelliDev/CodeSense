"""Deterministic search-episode and Java-location extraction from agent traces."""

from __future__ import annotations

import ast
import json
import re
import shlex
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import PurePosixPath

from evaluation.models import CodeLocation, ToolCall, TraceCase, TraceEvent, UsageEvidence

__all__ = [
    "CandidateLocation",
    "EpisodeDiscovery",
    "SearchEpisode",
    "SupervisedEpisode",
    "SupervisionBatch",
    "extract_trace_answer",
    "find_search_episodes",
    "is_search_event",
    "normalize_action",
    "parse_episode_candidates",
    "supervise_search_episodes",
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
_EDIT_BODY_KEYS = ("new_str", "patch", "content", "file_text")
_JAVA_LOCATION = re.compile(
    r"(?P<path>(?:[A-Za-z]:)?(?:/|\.?\.?/)?[^\s:,\"']+\.java)"
    r"(?::(?P<line>\d+))?(?::\s?(?P<text>.*))?"
)
_IDENTIFIER = re.compile(r"[A-Za-z_$][\w$]*")
_CONTROL_WORDS = {"catch", "do", "for", "if", "new", "return", "switch", "throw", "while"}
_IGNORED_DIRS = {"build", "example", "examples", "generated", "target", "test", "tests"}
_OPEN_ACTIONS = {"cat", "head", "less", "open", "read", "sed", "tail", "view"}
_EDIT_ACTIONS = {
    "apply_patch",
    "create",
    "create_file",
    "delete",
    "edit",
    "patch",
    "str_replace",
    "write",
    "write_file",
}
_FINAL_POSITIVE = re.compile(
    r"\b(?:add(?:ed)?|chang(?:e|ed)|creat(?:e|ed)|implement(?:ed)?)\b", re.I
)
_FINAL_NEGATIVE = re.compile(
    r"\b(?:already exists?|existing|not changed|remain(?:s|ed)? unchanged|unchanged)\b",
    re.I,
)


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


@dataclass(frozen=True, slots=True)
class SupervisedEpisode:
    episode: SearchEpisode
    answer: tuple[CodeLocation, ...]
    candidate_answers: tuple[CodeLocation, ...]
    usage_evidence: tuple[UsageEvidence, ...]


@dataclass(frozen=True, slots=True)
class SupervisionBatch:
    episodes: tuple[SupervisedEpisode, ...]
    skip_reasons: tuple[str, ...]
    search_episode_count: int


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
        if event.role.lower() in {"system", "user", "tool"}:
            continue
        all_calls = _event_calls(event)
        calls = tuple(call for call in all_calls if _is_search_call(call))
        if not calls:
            continue
        results = _following_tool_results(events, position)
        identified = bool(calls) and all(call.id for call in calls)
        result_ids = bool(results) and all(result.tool_call_id for result in results)
        if len(all_calls) > 1 and not (identified and result_ids):
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


def supervise_search_episodes(case: TraceCase) -> SupervisionBatch:
    """Build gold and weak candidates from deterministic later-use evidence."""
    discovery = find_search_episodes(case.events)
    episodes = tuple(
        replace(episode, candidates=parse_episode_candidates(episode, case.repo))
        for episode in discovery.episodes
    )
    evidence: list[list[UsageEvidence]] = [[] for _ in episodes]

    for event in case.events:
        kind = _usage_kind(event)
        if kind is None and (event.role.lower() != "assistant" or bool(_event_calls(event))):
            continue
        prior = [
            (position, episode)
            for position, episode in enumerate(episodes)
            if episode.anchor_event < event.index and episode.candidates
        ]
        if not prior:
            continue
        # A later search result supersedes an older producer for the same file.
        latest: dict[str, tuple[int, CandidateLocation]] = {}
        for position, episode in prior:
            for candidate in episode.candidates:
                latest[candidate.file] = (position, candidate)
        action = _event_action(event)
        matched = _match_candidate(action, [item[1] for item in latest.values()])
        if kind is None:
            matched = tuple(
                candidate for candidate in matched if _is_qualified_reference(action, candidate)
            )
            kind = "referenced"
        for candidate in matched:
            position, owned = latest[candidate.file]
            functions = _evidenced_functions(event, owned, kind)
            item = UsageEvidence(owned.file, functions, event.index, kind)
            if item not in evidence[position]:
                evidence[position].append(item)

    _add_final_answer_function_evidence(case.events, episodes, evidence)

    skipped = list(discovery.skip_reasons)
    supervised: list[SupervisedEpisode] = []
    for episode, items in zip(episodes, evidence, strict=True):
        if not episode.candidates:
            skipped.append("no_candidates")
            continue
        if not items:
            skipped.append("no_usage_evidence")
            continue
        gold_files = {item.file for item in items}
        answer = tuple(
            CodeLocation(
                candidate.file,
                tuple(
                    dict.fromkeys(
                        function
                        for item in items
                        if item.file == candidate.file
                        for function in item.functions
                    )
                ),
            )
            for candidate in episode.candidates
            if candidate.file in gold_files
        )
        candidates = tuple(
            CodeLocation(candidate.file, candidate.functions)
            for candidate in episode.candidates
            if candidate.file not in gold_files
        )
        supervised.append(SupervisedEpisode(episode, answer, candidates, tuple(items)))

    deduplicated: dict[tuple[object, ...], SupervisedEpisode] = {}
    for episode in supervised:
        signature = _episode_signature(episode)
        if signature in deduplicated:
            skipped.append("duplicate_episode")
        deduplicated[signature] = episode
    kept = tuple(sorted(deduplicated.values(), key=lambda item: item.episode.anchor_event))
    return SupervisionBatch(kept, tuple(skipped), len(discovery.episodes))


def _add_final_answer_function_evidence(
    events: Sequence[TraceEvent],
    episodes: Sequence[SearchEpisode],
    evidence: list[list[UsageEvidence]],
) -> None:
    """Use a final summary only to fill functions for an already-used result file."""
    final = _final_answer(events)
    if final is None or not _FINAL_POSITIVE.search(final[1]):
        return
    final_event, text = final
    for position, episode in enumerate(episodes):
        for candidate in episode.candidates:
            # A final summary cannot promote an otherwise unused search result.
            file_evidence = [item for item in evidence[position] if item.file == candidate.file]
            if not file_evidence or any(item.functions for item in file_evidence):
                continue
            if not _mentions_exact_file(text, candidate.file):
                continue
            edited = _edited_functions(
                events, candidate.file, episode.anchor_event, final_event.index
            )
            functions = tuple(
                function for function in edited if _is_positive_function_mention(text, function)
            )
            if functions:
                evidence[position].append(
                    UsageEvidence(candidate.file, functions, final_event.index, "final_answer")
                )


def _final_answer(events: Sequence[TraceEvent]) -> tuple[TraceEvent, str] | None:
    """Return the last explicit finish message."""
    for event in reversed(events):
        if event.role.lower() != "assistant":
            continue
        for call in reversed(_event_calls(event)):
            if call.name.strip().lower() != "finish":
                continue
            data = _action_mapping(call.arguments or "")
            message = data.get("message") if data else None
            if isinstance(message, str) and message.strip():
                return event, message.strip()
    return None


def extract_trace_answer(case: TraceCase) -> tuple[CodeLocation, ...]:
    """Extract final locations while allowing only existing reference-gold files."""
    final = _final_answer(case.events)
    if final is None or not case.answer:
        return ()
    final_event, text = final
    matched = _mentioned_trace_locations(text, case.answer)
    if not matched:
        return ()

    candidates = {
        location.file: tuple(
            dict.fromkeys(
                (
                    *location.functions,
                    *_edited_functions(case.events, location.file, -1, final_event.index),
                )
            )
        )
        for location in matched
    }
    owners: dict[str, set[str]] = defaultdict(set)
    for file, functions in candidates.items():
        for function in functions:
            owners[function].add(file)

    return tuple(
        CodeLocation(
            location.file,
            tuple(
                function
                for function in candidates[location.file]
                if _mentions_trace_function(text, location.file, function, owners[function])
            ),
        )
        for location in matched
    )


def _mentioned_trace_locations(
    text: str, locations: Sequence[CodeLocation]
) -> tuple[CodeLocation, ...]:
    """Return final-answer files, allowing a basename only when it is unique."""
    path_text = text.replace("`", "")
    basenames: dict[str, list[CodeLocation]] = defaultdict(list)
    for location in locations:
        basenames[PurePosixPath(location.file).name].append(location)

    found: list[CodeLocation] = []
    for location in locations:
        basename = PurePosixPath(location.file).name
        basename_match = re.search(rf"(?<![\w$]){re.escape(basename)}(?![\w$])", text)
        if _mentions_exact_file(path_text, location.file) or (
            len(basenames[basename]) == 1 and basename_match
        ):
            found.append(location)
    return tuple(found)


def _mentions_trace_function(text: str, file: str, function: str, owners: set[str]) -> bool:
    """Accept an explicit function mention, disambiguating shared names by class."""
    if not re.search(rf"(?<![\w$]){re.escape(function)}(?![\w$])", text):
        return False
    if len(owners) == 1:
        return True
    class_name = PurePosixPath(file).stem
    return bool(
        re.search(
            rf"(?<![\w$]){re.escape(class_name)}\s*\.\s*"
            rf"{re.escape(function)}(?![\w$])",
            text,
        )
    )


def _edited_functions(
    events: Sequence[TraceEvent], file: str, after: int, before: int
) -> tuple[str, ...]:
    """Extract Java declarations from edits to one exact candidate path."""
    found: list[str] = []
    for event in events:
        if not after < event.index < before or _usage_kind(event) != "edited":
            continue
        if not _mentions_exact_file(_event_action(event), file):
            continue
        for text in _edit_bodies(event):
            for line in text.splitlines():
                function = _declared_function(line)
                if function and function not in found:
                    found.append(function)
    return tuple(found)


def _edit_bodies(event: TraceEvent) -> tuple[str, ...]:
    """Decode the source-bearing fields of an edit action."""
    found: list[str] = []
    for raw in [*(call.arguments or "" for call in _event_calls(event)), event.tool_input or ""]:
        data = _action_mapping(raw)
        if data:
            found.extend(data[key] for key in _EDIT_BODY_KEYS if isinstance(data.get(key), str))
    return tuple(dict.fromkeys(found))


def _mentions_exact_file(text: str, file: str) -> bool:
    """Match a complete normalized Java path, never a basename alone."""
    return any(
        _normalize_java_path(match.group("path")) == file
        for match in _JAVA_LOCATION.finditer(text.replace("\\", "/"))
    )


def _is_positive_function_mention(text: str, function: str) -> bool:
    """Reject final-summary mentions immediately described as existing or unchanged."""
    matches = list(re.finditer(rf"(?<![\w$]){re.escape(function)}(?![\w$])", text))
    return any(
        not _FINAL_NEGATIVE.search(text[max(0, match.start() - 24) : match.end() + 64])
        for match in matches
    )


def _usage_kind(event: TraceEvent) -> str | None:
    """Classify a later assistant action as opened, searched, or edited."""
    if event.role.lower() != "assistant":
        return None
    operation = _action_operation(event)
    if operation in _EDIT_ACTIONS:
        return "edited"
    if operation in _OPEN_ACTIONS:
        return "opened"
    calls = _event_calls(event)
    if any(_is_search_call(call) for call in calls):
        return "searched"
    return None


def _match_candidate(
    action: str, candidates: Sequence[CandidateLocation]
) -> tuple[CandidateLocation, ...]:
    """Prefer complete paths, then unique suffixes, then unique basenames."""
    if not action:
        return ()
    normalized = action.replace("\\", "/")
    exact = [candidate for candidate in candidates if candidate.file in normalized]
    if exact:
        return tuple(dict.fromkeys(exact))

    mentioned = [
        _normalize_java_path(match.group("path")) for match in _JAVA_LOCATION.finditer(normalized)
    ]
    suffixes: list[CandidateLocation] = []
    for path in mentioned:
        if "/" not in path:
            continue
        matches = [
            candidate
            for candidate in candidates
            if candidate.file == path or candidate.file.endswith(f"/{path}")
        ]
        if len(matches) == 1:
            suffixes.extend(matches)
    if suffixes:
        return tuple(dict.fromkeys(suffixes))

    found: list[CandidateLocation] = []
    for basename in {PurePosixPath(path).name for path in mentioned}:
        matches = [
            candidate for candidate in candidates if PurePosixPath(candidate.file).name == basename
        ]
        if len(matches) == 1:
            found.extend(matches)
    return tuple(dict.fromkeys(found))


def _event_action(event: TraceEvent) -> str:
    parts = [call.arguments or "" for call in _event_calls(event)]
    if event.tool_input:
        parts.append(event.tool_input)
    if event.text:
        parts.append(event.text)
    return "\n".join(dict.fromkeys(part for part in parts if part))


def _action_operation(event: TraceEvent) -> str:
    for raw in [call.arguments or "" for call in _event_calls(event)]:
        data = _action_mapping(raw)
        if data:
            command = next(
                (data[key] for key in _COMMAND_KEYS if isinstance(data.get(key), str)),
                None,
            )
            if command:
                try:
                    return shlex.split(command)[0].lower()
                except (ValueError, IndexError):
                    return command.lower()
    name = (event.tool_name or "").strip().lower()
    return name


def _action_mapping(value: str) -> Mapping[str, object] | None:
    pending: object | None = value
    for _ in range(3):
        if isinstance(pending, Mapping):
            return pending
        if not isinstance(pending, str):
            return None
        if len(pending) >= 2 and pending[0] == pending[-1] == "'":
            pending = pending[1:-1]
            continue
        parsed = _parse_action(pending)
        if parsed == pending:
            return None
        pending = parsed
    return pending if isinstance(pending, Mapping) else None


def _is_qualified_reference(action: str, candidate: CandidateLocation) -> bool:
    if candidate.file in action.replace("\\", "/"):
        return True
    basename = PurePosixPath(candidate.file).name
    has_basename = re.search(rf"(?<![\w$]){re.escape(basename)}(?![\w$])", action)
    return bool(has_basename and _mentioned_functions(action, candidate))


def _evidenced_functions(
    event: TraceEvent, candidate: CandidateLocation, kind: str
) -> tuple[str, ...]:
    action = _event_action(event)
    found = list(_mentioned_functions(action, candidate))
    read_range = _read_range(event) if kind == "opened" else None
    if (
        read_range
        and candidate.line is not None
        and read_range[0] <= candidate.line <= read_range[1]
    ):
        for function in candidate.functions:
            if function not in found:
                found.append(function)
    return tuple(found)


def _mentioned_functions(action: str, candidate: CandidateLocation) -> tuple[str, ...]:
    return tuple(
        function
        for function in candidate.functions
        if re.search(rf"(?<![\w$]){re.escape(function)}(?![\w$])", action)
    )


def _read_range(event: TraceEvent) -> tuple[int, int] | None:
    for raw in [call.arguments or "" for call in _event_calls(event)]:
        data = _action_mapping(raw)
        if not data:
            continue
        value = data.get("view_range")
        if (
            isinstance(value, Sequence)
            and not isinstance(value, str)
            and len(value) == 2
            and all(isinstance(item, int) for item in value)
        ):
            return int(value[0]), int(value[1])
    return None


def _episode_signature(value: SupervisedEpisode) -> tuple[object, ...]:
    return (
        normalize_action(value.episode.raw_action),
        tuple(sorted(value.answer, key=lambda item: (item.file, item.functions))),
        tuple(sorted(value.candidate_answers, key=lambda item: (item.file, item.functions))),
    )


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
    if not words or "new" in words or words[-1] in _CONTROL_WORDS:
        return None
    if len(words) == 1 and "{" not in after:
        return None
    return words[-1]
