"""Normalize records from NVIDIA's Open-SWE-Traces dataset."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path, PurePosixPath

from evaluation.models import CodeLocation, TraceCase, TraceEvent

__all__ = ["extract_patch_locations", "iter_jsonl", "normalize_record"]

_DIFF_HEADER = re.compile(r"^diff --git a/(.+) b/(.+)$")
_HUNK_HEADER = re.compile(r"^@@ .*? @@\s*(.*)$")
_IDENTIFIER = re.compile(r"[A-Za-z_$][\w$]*")
_CONTROL_WORDS = {"catch", "do", "for", "if", "new", "return", "switch", "throw", "while"}
_IGNORED_DIRS = {"build", "generated", "target", "test", "tests"}


def normalize_record(record: Mapping[str, object]) -> TraceCase:
    if not _is_resolved(record):
        raise ValueError("expected resolved == 1")
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

    patch = _reference_patch(record)
    gold_error = None
    if patch is None:
        answer = ()
        gold_error = "missing_reference_patch"
    else:
        try:
            answer = extract_patch_locations(patch)
        except ValueError:
            answer = ()
            gold_error = "invalid_reference_patch"
        if not answer and gold_error is None:
            gold_error = "no_production_java_gold"

    return TraceCase(
        repo=repo,
        language=language,
        instance_id=instance_id,
        trajectory_id=trajectory_id,
        issue_statement=issue_statement,
        base_commit=_base_commit(record),
        events=tuple(events),
        raw=dict(record),
        answer=answer,
        gold_error=gold_error,
    )


def extract_patch_locations(patch: str) -> tuple[CodeLocation, ...]:
    """Extract existing production Java files and declared functions from a unified diff."""
    if not patch.strip():
        return ()
    sections = re.split(r"(?=^diff --git )", patch, flags=re.MULTILINE)
    by_file: dict[str, list[str]] = {}
    saw_diff = False
    for section in sections:
        lines = section.splitlines()
        if not lines:
            continue
        header = _DIFF_HEADER.match(lines[0])
        if not header:
            continue
        saw_diff = True
        _old_path, new_path = header.groups()
        if _is_added_or_deleted(lines) or not _is_production_java(new_path):
            continue
        functions = by_file.setdefault(new_path, [])
        for function in _changed_functions(lines):
            if function not in functions:
                functions.append(function)
    if not saw_diff:
        raise ValueError("reference patch is not a unified diff")
    return tuple(CodeLocation(file, tuple(functions)) for file, functions in by_file.items())


def _is_added_or_deleted(lines: Sequence[str]) -> bool:
    return any(
        line in {"--- /dev/null", "+++ /dev/null"}
        or line.startswith(("new file mode ", "deleted file mode "))
        for line in lines
    )


def _is_production_java(path: str) -> bool:
    parsed = PurePosixPath(path)
    if parsed.suffix.lower() != ".java":
        return False
    if any(part.lower() in _IGNORED_DIRS for part in parsed.parts):
        return False
    if parsed.parts and parsed.parts[0].lower() in {"example", "examples"}:
        return False
    return re.search(r"(?:Test|Tests|TestCase|IT)\.java$", parsed.name) is None


def _changed_functions(lines: Sequence[str]) -> tuple[str, ...]:
    candidates: list[str] = []
    for line in lines:
        hunk = _HUNK_HEADER.match(line)
        if hunk:
            candidates.append(hunk.group(1))
        elif line[:1] in {" ", "+", "-"} and not line.startswith(("+++", "---")):
            candidates.append(line[1:])
    found: list[str] = []
    for candidate in candidates:
        name = _declared_function(candidate)
        if name and name not in found:
            found.append(name)
    return tuple(found)


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
    # A bare ``foo();`` is a call. Constructors are accepted when a body opens.
    if len(words) == 1 and "{" not in after:
        return None
    if words[0] in _CONTROL_WORDS:
        return None
    return words[-1]


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
            if not _is_resolved(record):
                continue
            try:
                yield normalize_record(record)
            except ValueError as exc:
                raise ValueError(f"invalid trace on line {line_number}: {exc}") from exc


def _is_resolved(record: Mapping[str, object]) -> bool:
    resolved = record.get("resolved")
    return type(resolved) is int and resolved == 1


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


def _reference_patch(record: Mapping[str, object]) -> str | None:
    metadata = record.get("metadata")
    if not isinstance(metadata, Mapping):
        return None
    reference = metadata.get("reference_patch")
    if not isinstance(reference, Mapping):
        return None
    return _as_optional_text(reference.get("patch"))


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
