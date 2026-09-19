"""Behavior tests for the trace-query mining entry point."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "mine_trace_queries.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("codesense_mine_trace_queries_script", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_prompt_rows_imports_package_when_scripts_directory_precedes_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = SCRIPT.parents[1]
    scripts = root / "scripts"
    remaining = [path for path in sys.path if path not in {str(root), str(scripts)}]
    monkeypatch.setattr(sys, "path", [str(scripts), str(root), *remaining])
    for name in tuple(sys.modules):
        if name == "evaluation" or name.startswith("evaluation."):
            monkeypatch.delitem(sys.modules, name)

    module = _load_script()
    event = SimpleNamespace(
        index=1,
        role="assistant",
        text="",
        tool_name="rg",
        tool_input="rg cursor src/main/java",
        tool_output=None,
    )
    case = SimpleNamespace(
        repo="owner/repo",
        instance_id="issue-1",
        trajectory_id="trace-1",
        issue_statement="Navigation state can remain stale.",
        events=(event,),
        answer=(object(),),
        gold_error=None,
    )

    rows = module._prompt_rows(case, "semantic-query-v1")

    assert len(rows) == 1
    assert rows[0]["source_event_indices"] == [1]
