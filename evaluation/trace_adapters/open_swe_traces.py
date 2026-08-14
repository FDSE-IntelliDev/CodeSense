"""Normalize records from NVIDIA's Open-SWE-Traces dataset."""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from pathlib import Path

from evaluation.models import TraceCase, TraceEvent

__all__ = ["iter_jsonl", "normalize_record"]


def normalize_record(record: Mapping[str, object]) -> TraceCase:
    language = _required_text(record, "language")
    if language.lower() != "java":
        raise ValueError(f"expected a Java trace, got {language!r}")

    repo = _required_text(record, "repo")
    instance_id = _required_text(record, "instance_id")
    trajectory_id = _required_text(record, "trajectory_id")
    trajectory = record.get("trajectory")
    if not isinstance(trajectory, list) or not trajectory:
        raise ValueError("trajectory must be a non-empty list")

    events: list[TraceEvent] = []
    issue_statement = ""
    for raw_message in trajectory:
        if not isinstance(raw_message, Mapping):
            raise ValueError("every trajectory event must be an object")
        event = _event(len(events), raw_message)
        events.append(event)
        if not issue_statement and event.role == "user" and event.text.strip():
            issue_statement = event.text.strip()

    return TraceCase(
        repo=repo,
        language=language,
        instance_id=instance_id,
        trajectory_id=trajectory_id,
        issue_statement=issue_statement,
        base_commit=_base_commit(record),
        events=tuple(events),
        raw=dict(record),
    )


def iter_jsonl(path: Path) -> Iterator[TraceCase]:
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON on line {line_number}") from exc
            if not isinstance(record, Mapping):
                raise ValueError(f"line {line_number} must contain a JSON object")
            try:
                yield normalize_record(record)
            except ValueError as exc:
                raise ValueError(f"invalid trace on line {line_number}: {exc}") from exc


def _event(index: int, message: Mapping[str, object]) -> TraceEvent:
    role = str(message.get("role") or "unknown")
    content = message.get("content", "")
    text = _content_text(content)
    tool_name, tool_input = _tool_call(message, content)
    tool_output = None
    if role == "tool" or str(message.get("type") or "").lower() == "tool_result":
        tool_output = _as_text(content)
        tool_name = str(message.get("name") or message.get("tool_name") or "") or None
        text = ""
    return TraceEvent(
        index=index,
        role=role,
        text=text,
        tool_name=tool_name,
        tool_input=tool_input,
        tool_output=tool_output,
    )


def _tool_call(message: Mapping[str, object], content: object) -> tuple[str | None, str | None]:
    calls: list[object] = []
    raw_calls = message.get("tool_calls")
    if isinstance(raw_calls, list):
        calls.extend(raw_calls)
    if isinstance(content, list):
        calls.extend(block for block in content if isinstance(block, Mapping))

    for call in calls:
        if not isinstance(call, Mapping):
            continue
        function = call.get("function")
        if isinstance(function, Mapping):
            name = _as_optional_text(function.get("name"))
            arguments = function.get("arguments")
        else:
            name = _as_optional_text(call.get("name"))
            arguments = call.get("arguments") or call.get("input")
        if name:
            return name, _json_text(arguments)
    return None, None


def _content_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if not isinstance(block, Mapping):
                continue
            if str(block.get("type") or "").lower() in {"tool_call", "function", "tool_use"}:
                continue
            value = block.get("text") or block.get("content")
            if isinstance(value, str):
                parts.append(value)
        return "\n".join(parts)
    return _as_text(content)


def _base_commit(record: Mapping[str, object]) -> str | None:
    value = record.get("base_commit")
    if not value and isinstance(record.get("metadata"), Mapping):
        value = record["metadata"].get("base_commit")  # type: ignore[index]
    return _as_optional_text(value)


def _required_text(record: Mapping[str, object], name: str) -> str:
    value = _as_optional_text(record.get(name))
    if not value:
        raise ValueError(f"missing {name}")
    return value


def _as_optional_text(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _as_text(value: object) -> str:
    if isinstance(value, str):
        return value
    return _json_text(value)


def _json_text(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        return str(value)
