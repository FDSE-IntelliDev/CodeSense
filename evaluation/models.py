"""Small JSON-serializable objects shared by trace adapters and query mining."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

__all__ = ["CodeLocation", "PreparedQuery", "TraceCase", "TraceEvent"]


@dataclass(frozen=True, slots=True)
class TraceEvent:
    index: int
    role: str
    text: str
    tool_name: str | None = None
    tool_input: str | None = None
    tool_output: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "index": self.index,
            "role": self.role,
            "text": self.text,
            "tool_name": self.tool_name,
            "tool_input": self.tool_input,
            "tool_output": self.tool_output,
        }


@dataclass(frozen=True, slots=True)
class TraceCase:
    repo: str
    language: str
    instance_id: str
    trajectory_id: str
    issue_statement: str
    base_commit: str | None
    events: tuple[TraceEvent, ...]
    raw: Mapping[str, object]
    answer: tuple[CodeLocation, ...] = ()
    gold_error: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "repo": self.repo,
            "language": self.language,
            "instance_id": self.instance_id,
            "trajectory_id": self.trajectory_id,
            "issue_statement": self.issue_statement,
            "base_commit": self.base_commit,
            "events": [event.to_dict() for event in self.events],
            "raw": dict(self.raw),
            "answer": [location.to_dict() for location in self.answer],
            "gold_error": self.gold_error,
        }


@dataclass(frozen=True, slots=True)
class CodeLocation:
    file: str
    functions: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {"file": self.file, "functions": list(self.functions)}


@dataclass(frozen=True, slots=True)
class PreparedQuery:
    query_id: str
    repo: str
    instance_id: str
    trajectory_id: str
    issue_statement: str
    query: str
    answer: tuple[CodeLocation, ...]
    source_event_indices: tuple[int, ...]
    strategy: str
    source_events: tuple[TraceEvent, ...]
    provenance: Mapping[str, object]

    def to_dict(self) -> dict[str, Any]:
        return {
            "query_id": self.query_id,
            "repo": self.repo,
            "instance_id": self.instance_id,
            "trajectory_id": self.trajectory_id,
            "issue_statement": self.issue_statement,
            "query": self.query,
            "answer": [location.to_dict() for location in self.answer],
            "source_event_indices": list(self.source_event_indices),
            "strategy": self.strategy,
            "source_events": [event.to_dict() for event in self.source_events],
            "provenance": dict(self.provenance),
        }
