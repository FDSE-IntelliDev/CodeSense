"""Behavior tests for the trace-query mining entry point."""

from __future__ import annotations

import importlib.util
import json
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


def test_script_imports_package_when_scripts_directory_precedes_root(
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

    assert module.mine_queries.__module__ == "evaluation.query_mining"


def test_run_cases_writes_every_query_returned_for_one_trace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_script()
    output_path = tmp_path / "queries.jsonl"
    rows = ({"query_id": "trace-1:2"}, {"query_id": "trace-1:8"})
    batch = SimpleNamespace(
        queries=tuple(SimpleNamespace(to_dict=lambda row=row: row) for row in rows),
        skip_reasons=(),
        search_episode_count=2,
        eligible_episode_count=2,
    )
    monkeypatch.setattr(module, "mine_queries", lambda *args, **kwargs: batch)

    summary = module._run_cases(
        (object(),),
        object(),
        output_path,
        limit=0,
        prompt_version="trace-search-v2",
        dry_run=False,
    )

    assert len(output_path.read_text().splitlines()) == 2
    assert summary["written_queries"] == 2
    assert summary["search_episodes"] == 2
    assert summary["eligible_episodes"] == 2


def test_run_cases_does_not_stop_after_ten_processed_traces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_script()
    output_path = tmp_path / "queries.jsonl"
    empty_batch = SimpleNamespace(
        queries=(),
        skip_reasons=("no_usage_evidence",),
        search_episode_count=1,
        eligible_episode_count=0,
    )
    monkeypatch.setattr(module, "mine_queries", lambda *args, **kwargs: empty_batch)

    summary = module._run_cases(
        tuple(object() for _index in range(11)),
        object(),
        output_path,
        limit=0,
        prompt_version="trace-search-v2",
        dry_run=False,
    )

    assert summary["processed_traces"] == 11
    assert summary["skipped"] == {"no_usage_evidence": 11}


def test_prompt_rows_returns_one_row_per_eligible_episode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script()
    context = (SimpleNamespace(index=1), SimpleNamespace(index=2))
    results = (SimpleNamespace(index=3),)
    item = SimpleNamespace(
        episode=SimpleNamespace(
            anchor_event=2,
            context_events=context,
            result_events=results,
        )
    )
    supervision = SimpleNamespace(
        episodes=(item,),
        search_episode_count=2,
        skip_reasons=("no_usage_evidence",),
    )
    case = SimpleNamespace(repo="owner/repo", trajectory_id="trace-1")
    monkeypatch.setattr(module, "supervise_search_episodes", lambda case: supervision)
    monkeypatch.setattr(module, "build_prompt", lambda *args, **kwargs: "PROMPT")

    rows, search_count, eligible_count, reasons = module._prompt_rows(case, "trace-search-v2")

    assert rows == [
        {
            "type": "prompt",
            "repo": "owner/repo",
            "trajectory_id": "trace-1",
            "anchor_event": 2,
            "source_event_indices": [1, 2],
            "result_event_indices": [3],
            "prompt": "PROMPT",
        }
    ]
    assert (search_count, eligible_count, reasons) == (
        2,
        1,
        ("no_usage_evidence",),
    )


def test_run_cases_annotates_answer_existence_in_the_pinned_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_script()

    # Fixture repo: the target file declares only value(), not unquote().
    repo_root = tmp_path / "repo"
    pkg = repo_root / "src/main/java/com/amihaiemil/eoyaml"
    pkg.mkdir(parents=True)
    (pkg / "ReadPlainScalarValue.java").write_text(
        "package com.amihaiemil.eoyaml;\n"
        "public final class ReadPlainScalarValue {\n"
        '    public String value() { return ""; }\n'
        "}\n",
        encoding="utf-8",
    )
    # Never clone: hand back the fixture for any (repo, commit).
    monkeypatch.setattr(module, "resolve_repo", lambda repo, commit, *a, **k: repo_root)

    java_file = "src/main/java/com/amihaiemil/eoyaml/ReadPlainScalarValue.java"
    row = {
        "repo": "decorators-squad/eo-yaml",
        "base_commit": "95a4860",
        "query": "q",
        "answer": [],
        "trace_answer": [{"file": java_file, "functions": ["value", "unquote"]}],
        "candidate_answers": [],
    }
    batch = SimpleNamespace(
        queries=(SimpleNamespace(to_dict=lambda: dict(row)),),
        skip_reasons=(),
        search_episode_count=1,
        eligible_episode_count=1,
    )
    monkeypatch.setattr(module, "mine_queries", lambda *a, **k: batch)

    output_path = tmp_path / "benchmark.jsonl"
    summary = module._run_cases(
        (SimpleNamespace(repo="decorators-squad/eo-yaml", base_commit="95a4860"),),
        object(),
        output_path,
        limit=0,
        prompt_version="trace-search-v3",
        dry_run=False,
    )

    written = json.loads(output_path.read_text().splitlines()[0])
    location = written["trace_answer"][0]
    assert location["functions"] == ["value", "unquote"]
    assert location["file"] == java_file
    assert location["file_exist"] is True
    assert location["function_exist"] == {"value": True, "unquote": False}
    assert summary["written_queries"] == 1
