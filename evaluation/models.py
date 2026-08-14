"""Small JSON-serializable objects shared by trace adapters and query mining."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

__all__ = ["PreparedQuery", "TraceCase", "TraceEvent"]


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
        }


@dataclass(frozen=True, slots=True)
class PreparedQuery:
    query_id: str
    repo: str
    instance_id: str
    trajectory_id: str
    episode_index: int
    query: str
    strategy: str
    anchor_event: int
    raw_action: str
    source_events: tuple[TraceEvent, ...]
    provenance: Mapping[str, object]

    def to_dict(self) -> dict[str, Any]:
        return {
            "query_id": self.query_id,
            "repo": self.repo,
            "instance_id": self.instance_id,
            "trajectory_id": self.trajectory_id,
            "episode_index": self.episode_index,
            "query": self.query,
            "strategy": self.strategy,
            "anchor_event": self.anchor_event,
            "raw_action": self.raw_action,
            "source_events": [event.to_dict() for event in self.source_events],
            "provenance": dict(self.provenance),
        }
