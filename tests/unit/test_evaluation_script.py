"""Behavior tests for the benchmark evaluation entry point."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "evaluation.py"


def _load_script() -> ModuleType:
    assert SCRIPT.is_file(), "scripts/evaluation.py must provide the benchmark entry point"
    spec = importlib.util.spec_from_file_location("codesense_evaluation_script", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_open_project_names_the_index_by_repo_and_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_script()
    built: list[tuple[str, object]] = []

    class FakeProject:
        def __init__(self, commit: str) -> None:
            self.index = SimpleNamespace(meta=SimpleNamespace(commit=commit))

        @classmethod
        def open(cls, index_dir: object, *, llm: object = None) -> FakeProject:
            return cls("abc123")

        @classmethod
        def build(cls, root: object, *, index_dir: object, **kwargs: object) -> FakeProject:
            built.append((Path(index_dir).name, kwargs.get("commit")))
            return cls(str(kwargs.get("commit", "")))

    monkeypatch.setattr(module, "Project", FakeProject)
    indexes = tmp_path / "indexes"

    module._open_project("owner/repo", tmp_path, indexes, None, base_commit="abc123")

    assert built == [("owner__repo__abc123", "abc123")]


def test_open_project_reuses_an_index_whose_meta_commit_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_script()
    index_dir = tmp_path / "indexes" / "owner__repo__abc123"
    index_dir.mkdir(parents=True)
    (index_dir / "meta.json").write_text("{}", encoding="utf-8")
    calls = {"open": 0, "build": 0}

    class FakeProject:
        def __init__(self, commit: str) -> None:
            self.index = SimpleNamespace(meta=SimpleNamespace(commit=commit))

        @classmethod
        def open(cls, index_dir: object, *, llm: object = None) -> FakeProject:
            calls["open"] += 1
            return cls("abc123")

        @classmethod
        def build(cls, root: object, *, index_dir: object, **kwargs: object) -> FakeProject:
            calls["build"] += 1
            return cls(str(kwargs.get("commit", "")))

    monkeypatch.setattr(module, "Project", FakeProject)

    module._open_project("owner/repo", tmp_path, tmp_path / "indexes", None, base_commit="abc123")

    assert calls == {"open": 1, "build": 0}


def test_open_project_rebuilds_when_the_meta_commit_mismatches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_script()
    index_dir = tmp_path / "indexes" / "owner__repo__abc123"
    index_dir.mkdir(parents=True)
    (index_dir / "meta.json").write_text("{}", encoding="utf-8")
    calls = {"open": 0, "build": 0}

    class FakeProject:
        def __init__(self, commit: str) -> None:
            self.index = SimpleNamespace(meta=SimpleNamespace(commit=commit))

        @classmethod
        def open(cls, index_dir: object, *, llm: object = None) -> FakeProject:
            calls["open"] += 1
            return cls("stale-commit")

        @classmethod
        def build(cls, root: object, *, index_dir: object, **kwargs: object) -> FakeProject:
            calls["build"] += 1
            return cls(str(kwargs.get("commit", "")))

    monkeypatch.setattr(module, "Project", FakeProject)

    module._open_project("owner/repo", tmp_path, tmp_path / "indexes", None, base_commit="abc123")

    # The directory name promises a commit the artifact does not carry, so it is
    # rebuilt instead of silently scoring against the wrong version.
    assert calls == {"open": 1, "build": 1}


def test_score_reports_file_and_function_metrics_without_test_gold() -> None:
    module = _load_script()
    answers = [
        {"file": "src/main/java/example/Client.java", "functions": ["send(String)"]},
        {"file": "src/main/java/example/Retry.java", "functions": []},
        {"file": "src/test/java/example/ClientTest.java", "functions": ["testSend"]},
    ]
    hits = [
        SimpleNamespace(file="src/main/java/example/Client.java", name="send"),
        SimpleNamespace(file="src/main/java/example/Retry.java", name="Retry"),
        SimpleNamespace(file="src/main/java/example/Other.java", name="send"),
        SimpleNamespace(file="src/main/java/example/Client.java", name="close"),
    ]

    score, labels = module._score(hits, answers, candidate_answers=[], include_test_files=False)

    assert {
        key: value
        for key, value in score.items()
        if key not in {"query_plus_trace_answer", "trace_answer"}
    } == {
        "gold_files": [
            "src/main/java/example/Client.java",
            "src/main/java/example/Retry.java",
        ],
        "gold_functions": [{"file": "src/main/java/example/Client.java", "function": "send"}],
        "matched_files": [
            "src/main/java/example/Client.java",
            "src/main/java/example/Retry.java",
        ],
        "matched_functions": [{"file": "src/main/java/example/Client.java", "function": "send"}],
        "observed_file_precision": pytest.approx(2 / 3),
        "file_recall": 1.0,
        "first_gold_rank": 1,
        "mrr": 1.0,
        "function_precision": 0.25,
        "function_recall": 1.0,
    }
    assert score["query_plus_trace_answer"]["file_recall"] == 1.0
    assert score["trace_answer"] == {
        "files": [],
        "functions": [],
        "matched_files": [],
        "matched_functions": [],
        "file_recall": 0.0,
        "function_recall": None,
    }
    assert labels == ["gold_hit", "gold_hit", "unlabeled_hit", "gold_hit"]


def test_score_excludes_files_and_functions_marked_absent_from_the_revision() -> None:
    module = _load_script()
    answers = [
        {
            "file": "src/Missing.java",
            "functions": ["ghost"],
            "file_exist": False,
            "function_exist": {"ghost": False},
        },
        {
            "file": "src/Present.java",
            "functions": ["existing", "addedLater"],
            "file_exist": True,
            "function_exist": {"existing": True, "addedLater": False},
        },
    ]
    hits = [
        SimpleNamespace(file="src/Present.java", name="existing"),
        SimpleNamespace(file="src/Missing.java", name="ghost"),
    ]

    metrics, labels = module._score(
        hits,
        answers,
        candidate_answers=[],
        include_test_files=False,
    )

    assert metrics["gold_files"] == ["src/Present.java"]
    assert metrics["gold_functions"] == [{"file": "src/Present.java", "function": "existing"}]
    assert metrics["matched_files"] == ["src/Present.java"]
    assert metrics["matched_functions"] == [{"file": "src/Present.java", "function": "existing"}]
    assert labels == ["gold_hit", "unlabeled_hit"]


def test_evaluate_query_skips_when_all_primary_files_are_marked_absent() -> None:
    module = _load_script()

    class Project:
        def search(self, *args: object, **kwargs: object) -> object:
            pytest.fail("a query without scoreable primary gold must not run")

    record = {
        "query": "Find missing logic",
        "answer": [
            {
                "file": "src/Missing.java",
                "functions": ["ghost"],
                "file_exist": False,
                "function_exist": {"ghost": False},
            }
        ],
    }

    result = module._evaluate_query(Project(), record, routes=("lexical",), limit=20)

    assert result["skipped"] is True
    assert result["skip_reason"] == "no production-code gold"


def test_score_reports_observed_precision_rank_and_candidate_labels() -> None:
    module = _load_script()
    hits = [
        SimpleNamespace(file="src/Candidate.java", name="Candidate"),
        SimpleNamespace(file="src/Gold.java", name="Gold"),
        SimpleNamespace(file="src/Other.java", name="Other"),
    ]

    metrics, labels = module._score(
        hits,
        answers=[{"file": "src/Gold.java", "functions": []}],
        candidate_answers=[{"file": "src/Candidate.java", "functions": []}],
        include_test_files=False,
    )

    assert metrics["observed_file_precision"] == pytest.approx(1 / 3)
    assert metrics["file_recall"] == 1.0
    assert metrics["first_gold_rank"] == 2
    assert metrics["mrr"] == 0.5
    assert labels == ["candidate_hit", "gold_hit", "unlabeled_hit"]


def test_score_keeps_query_metrics_and_adds_trace_answer_bonus_metrics() -> None:
    module = _load_script()
    hits = [
        SimpleNamespace(file="src/Query.java", name="queryMethod"),
        SimpleNamespace(file="src/Trace.java", name="traceMethod"),
        SimpleNamespace(file="src/Candidate.java", name="Candidate"),
        SimpleNamespace(file="src/Other.java", name="Other"),
    ]

    metrics, labels = module._score(
        hits,
        answers=[{"file": "src/Query.java", "functions": ["queryMethod"]}],
        candidate_answers=[{"file": "src/Candidate.java", "functions": []}],
        trace_answers=[
            {"file": "src/Query.java", "functions": ["traceOnlyMethod"]},
            {"file": "src/Trace.java", "functions": ["traceMethod"]},
        ],
        include_test_files=False,
    )

    assert metrics["observed_file_precision"] == pytest.approx(1 / 4)
    assert metrics["file_recall"] == 1.0
    assert metrics["query_plus_trace_answer"]["observed_file_precision"] == pytest.approx(2 / 4)
    assert metrics["query_plus_trace_answer"]["file_recall"] == 1.0
    assert metrics["trace_answer"] == {
        "files": ["src/Query.java", "src/Trace.java"],
        "functions": [
            {"file": "src/Query.java", "function": "traceOnlyMethod"},
            {"file": "src/Trace.java", "function": "traceMethod"},
        ],
        "matched_files": ["src/Query.java", "src/Trace.java"],
        "matched_functions": [{"file": "src/Trace.java", "function": "traceMethod"}],
        "file_recall": 1.0,
        "function_recall": 0.5,
    }
    assert labels == ["gold_hit", "trace_answer_hit", "candidate_hit", "unlabeled_hit"]


def test_evaluate_query_runs_every_route_and_keeps_ranked_hits() -> None:
    module = _load_script()
    calls: list[tuple[str, str, int]] = []

    class Project:
        def search(self, query: str, *, route: str, limit: int) -> object:
            calls.append((query, route, limit))
            return SimpleNamespace(
                route=route,
                script=f"# generated for {route}\nanswer = frag",
                elapsed=0.25,
                notes=[f"used {route}"],
                hits=[
                    SimpleNamespace(
                        rank=1,
                        name="send",
                        kind="method",
                        file="src/main/java/example/Client.java",
                        line=12,
                        score=0.9,
                        why="send@name",
                    )
                ],
            )

    record = {
        "query": "Find the client send method.",
        "source_event_indices": [4],
        "answer": [{"file": "src/main/java/example/Client.java", "functions": ["send"]}],
        "trace_answer": [{"file": "src/main/java/example/Client.java", "functions": ["send"]}],
        "candidate_answers": [{"file": "src/main/java/example/ClientConfig.java", "functions": []}],
    }

    result = module._evaluate_query(
        Project(), record, routes=("lexical", "planned", "codegen"), limit=20
    )

    assert calls == [
        ("Find the client send method.", "lexical", 20),
        ("Find the client send method.", "planned", 20),
        ("Find the client send method.", "codegen", 20),
    ]
    assert result["query"] == "Find the client send method."
    assert result["source_event_indices"] == [4]
    assert result["answer"] == [
        {"file": "src/main/java/example/Client.java", "functions": ["send"]}
    ]
    assert result["routes"]["codegen"]["hits"] == [
        {
            "rank": 1,
            "name": "send",
            "kind": "method",
            "file": "src/main/java/example/Client.java",
            "line": 12,
            "score": 0.9,
            "why": "send@name",
            "label": "gold_hit",
        }
    ]
    assert result["routes"]["codegen"]["script"] == ("# generated for codegen\nanswer = frag")
    assert result["routes"]["codegen"]["metrics"]["file_recall"] == 1.0
    assert result["candidate_answers"] == [
        {"file": "src/main/java/example/ClientConfig.java", "functions": []}
    ]
    assert result["trace_answer"] == [
        {"file": "src/main/java/example/Client.java", "functions": ["send"]}
    ]
    assert result["routes"]["codegen"]["metrics"]["trace_answer"]["matched_files"] == [
        "src/main/java/example/Client.java"
    ]


def test_trace_coverage_includes_fallbacks_in_end_to_end_coverage() -> None:
    module = _load_script()
    trace_answer = [
        {"file": "src/A.java", "functions": ["a"]},
        {"file": "src/B.java", "functions": ["b"]},
    ]
    cases = [
        {
            "query_id": "q1",
            "repo": "owner/repo",
            "instance_id": "issue-1",
            "trajectory_id": "trace-1",
            "evaluation": {
                "trace_answer": trace_answer,
                "skipped": False,
                "routes": {
                    "planned": {
                        "actual_route": "planned",
                        "route_fidelity": True,
                        "metrics": {},
                        "hits": [{"file": "src/A.java", "name": "a"}],
                    },
                    "codegen": {
                        # codegen degraded to lexical but still returned a hit;
                        # end-to-end coverage must not discard it.
                        "actual_route": "lexical",
                        "route_fidelity": False,
                        "metrics": {},
                        "hits": [{"file": "src/B.java", "name": "b"}],
                    },
                },
            },
        },
        {
            "query_id": "q2",
            "repo": "owner/repo",
            "instance_id": "issue-1",
            "trajectory_id": "trace-1",
            "evaluation": {
                "trace_answer": trace_answer,
                "skipped": False,
                "routes": {
                    "planned": {
                        "actual_route": "planned",
                        "route_fidelity": True,
                        "metrics": {},
                        "hits": [{"file": "src/B.java", "name": "b"}],
                    },
                    "codegen": {
                        "actual_route": "codegen",
                        "route_fidelity": True,
                        "metrics": {},
                        "hits": [{"file": "src/A.java", "name": "a"}],
                    },
                },
            },
        },
    ]

    coverage = module._trace_coverage(cases, ("planned", "codegen"), include_test_files=False)

    assert coverage == [
        {
            "repo": "owner/repo",
            "instance_id": "issue-1",
            "trajectory_id": "trace-1",
            "query_count": 2,
            "trace_answer": trace_answer,
            "routes": {
                "planned": {
                    "completed_queries": 2,
                    "faithful_queries": 2,
                    "matched_files": ["src/A.java", "src/B.java"],
                    "matched_functions": [
                        {"file": "src/A.java", "function": "a"},
                        {"file": "src/B.java", "function": "b"},
                    ],
                    "file_recall": 1.0,
                    "function_recall": 1.0,
                },
                "codegen": {
                    "completed_queries": 2,
                    "faithful_queries": 1,
                    "matched_files": ["src/A.java", "src/B.java"],
                    "matched_functions": [
                        {"file": "src/A.java", "function": "a"},
                        {"file": "src/B.java", "function": "b"},
                    ],
                    "file_recall": 1.0,
                    "function_recall": 1.0,
                },
            },
        }
    ]

    summary = module._summarize(cases, ("planned", "codegen"), trace_coverage=coverage)
    assert summary["planned"]["trace_answer_coverage"] == {
        "traces": 1,
        "file_recall": 1.0,
        "function_recall": 1.0,
    }
    assert summary["codegen"]["trace_answer_coverage"] == {
        "traces": 1,
        "file_recall": 1.0,
        "function_recall": 1.0,
    }


def test_summarize_separates_route_fidelity_from_retrieval_quality() -> None:
    module = _load_script()
    cases = [
        {
            "evaluation": {
                "skipped": False,
                "routes": {"codegen": {"route_fidelity": True, "metrics": {"file_recall": 1.0}}},
            }
        },
        {
            "evaluation": {
                "skipped": False,
                "routes": {
                    # Degraded but still produced hits and metrics.
                    "codegen": {"route_fidelity": False, "metrics": {"file_recall": 0.0}}
                },
            }
        },
        {
            "evaluation": {
                "skipped": False,
                "routes": {"codegen": {"error": "boom", "route_fidelity": False, "hits": []}},
            }
        },
    ]

    summary = module._summarize(cases, ("codegen",))

    assert summary["codegen"]["queries"] == 3
    # Two produced metrics (one faithful, one degraded); one raised.
    assert summary["codegen"]["completed"] == 2
    assert summary["codegen"]["errors"] == 1
    assert summary["codegen"]["degraded"] == 1
    assert summary["codegen"]["route_fidelity"] == 0.5
    # Retrieval quality is averaged over both faithful and degraded results.
    assert summary["codegen"]["file_recall"] == 0.5


def test_trace_coverage_omits_traces_without_a_final_answer() -> None:
    module = _load_script()
    cases = [
        {
            "query_id": "q1",
            "repo": "owner/repo",
            "trajectory_id": "trace-without-answer",
            "evaluation": {"trace_answer": [], "skipped": False, "routes": {}},
        }
    ]

    assert module._trace_coverage(cases, ("lexical",), include_test_files=False) == []


def test_evaluate_query_keeps_metrics_and_flags_route_fidelity_on_fallback() -> None:
    module = _load_script()

    class Project:
        def search(self, query: str, *, route: str, limit: int) -> object:
            # codegen degrades to lexical, and the lexical fallback still hits gold.
            return SimpleNamespace(
                route="lexical",
                elapsed=0.1,
                notes=["codegen failed; fell back to lexical"],
                script="",
                hits=[
                    SimpleNamespace(
                        rank=1,
                        name="send",
                        kind="method",
                        file="src/main/java/example/Client.java",
                        line=12,
                        score=0.9,
                        why="send@name",
                    )
                ],
            )

    record = {
        "query": "Find the client send method.",
        "source_event_indices": [4],
        "answer": [{"file": "src/main/java/example/Client.java", "functions": ["send"]}],
    }

    result = module._evaluate_query(Project(), record, routes=("codegen",), limit=20)
    codegen = result["routes"]["codegen"]

    # The fallback's real end-to-end quality is preserved, not discarded ...
    assert codegen["actual_route"] == "lexical"
    assert codegen["metrics"]["file_recall"] == 1.0
    # ... and route fidelity is tracked as a separate, explicit dimension.
    assert codegen["route_fidelity"] is False
    assert "error" not in codegen


def test_evaluate_query_marks_a_faithful_route_with_route_fidelity() -> None:
    module = _load_script()

    class Project:
        def search(self, query: str, *, route: str, limit: int) -> object:
            return SimpleNamespace(
                route=route,
                elapsed=0.1,
                notes=[],
                script="",
                hits=[
                    SimpleNamespace(
                        rank=1,
                        name="send",
                        kind="method",
                        file="src/main/java/example/Client.java",
                        line=12,
                        score=0.9,
                        why="send@name",
                    )
                ],
            )

    record = {
        "query": "Find the client send method.",
        "answer": [{"file": "src/main/java/example/Client.java", "functions": ["send"]}],
    }

    result = module._evaluate_query(Project(), record, routes=("codegen",), limit=20)
    codegen = result["routes"]["codegen"]

    assert codegen["route_fidelity"] is True
    assert codegen["metrics"]["file_recall"] == 1.0


def test_evaluate_query_records_error_without_metrics_when_search_raises() -> None:
    module = _load_script()

    class Project:
        def search(self, query: str, *, route: str, limit: int) -> object:
            raise RuntimeError("the model could not interpret the query")

    record = {
        "query": "Find the client send method.",
        "answer": [{"file": "src/main/java/example/Client.java", "functions": ["send"]}],
    }

    result = module._evaluate_query(Project(), record, routes=("planned",), limit=20)
    planned = result["routes"]["planned"]

    assert planned["error"] == "RuntimeError: the model could not interpret the query"
    assert planned["route_fidelity"] is False
    assert planned["hits"] == []
    assert "metrics" not in planned


def test_gold_problems_flags_globs_and_paths_missing_from_the_checkout(
    tmp_path: Path,
) -> None:
    module = _load_script()
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "Real.java").write_text("class Real {}", encoding="utf-8")
    record = {
        "answer": [
            {"file": "src/Real.java", "functions": []},
            {"file": "src/Gone.java", "functions": []},
        ],
        "trace_answer": [{"file": "*.java", "functions": []}],
    }

    problems = module._gold_problems(record, tmp_path)

    assert {"file": "src/Gone.java", "reason": "missing", "field": "answer"} in problems
    assert {"file": "*.java", "reason": "glob", "field": "trace_answer"} in problems
    # An existing, well-formed path is not a problem.
    assert all(problem["file"] != "src/Real.java" for problem in problems)


def test_evaluate_query_skips_when_primary_gold_is_missing_from_the_checkout(
    tmp_path: Path,
) -> None:
    module = _load_script()

    class Project:
        def search(self, query: str, *, route: str, limit: int) -> object:
            raise AssertionError("invalid gold must not reach the search")

    record = {
        "query": "Find the client send method.",
        "answer": [{"file": "src/Gone.java", "functions": ["send"]}],
    }

    result = module._evaluate_query(Project(), record, routes=("lexical",), limit=20, root=tmp_path)

    assert result["skipped"] is True
    assert result["skip_reason"] == "invalid gold path"
    assert result["gold_problems"] == [
        {"file": "src/Gone.java", "reason": "missing", "field": "answer"}
    ]
    assert result["routes"] == {}


def test_evaluate_query_scores_when_only_trace_gold_is_invalid(tmp_path: Path) -> None:
    module = _load_script()
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "Real.java").write_text("class Real {}", encoding="utf-8")

    class Project:
        def search(self, query: str, *, route: str, limit: int) -> object:
            return SimpleNamespace(
                route=route,
                elapsed=0.0,
                notes=[],
                script="",
                hits=[
                    SimpleNamespace(
                        rank=1,
                        name="Real",
                        kind="file",
                        file="src/Real.java",
                        line=1,
                        score=1.0,
                        why="real@name",
                    )
                ],
            )

    record = {
        "query": "Find the real logic.",
        "answer": [{"file": "src/Real.java", "functions": []}],
        "trace_answer": [{"file": "*.java", "functions": []}],
    }

    result = module._evaluate_query(Project(), record, routes=("lexical",), limit=20, root=tmp_path)

    # Primary gold is valid, so the case is still scored; the bad trace gold is
    # surfaced for inspection instead of silently deflating a bonus metric.
    assert result["skipped"] is False
    assert result["routes"]["lexical"]["metrics"]["file_recall"] == 1.0
    assert {"file": "*.java", "reason": "glob", "field": "trace_answer"} in result["gold_problems"]


def test_main_streams_each_completed_query_and_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load_script()
    benchmark = tmp_path / "queries.jsonl"
    output = tmp_path / "report.json"
    project_root = tmp_path / "repo"
    project_root.mkdir()
    # The gold must exist in the checkout or the case is (correctly) skipped.
    (project_root / "src").mkdir()
    (project_root / "src" / "Client.java").write_text("class Client {}", encoding="utf-8")
    benchmark.write_text(
        json.dumps(
            {
                "query_id": "query-1",
                "repo": "owner/repo",
                "instance_id": "instance-1",
                "trajectory_id": "trajectory-1",
                "query": "Find the production logic responsible for creating clients.",
                "source_event_indices": [3, 8],
                "answer": [{"file": "src/Client.java", "functions": []}],
                "trace_answer": [{"file": "src/Client.java", "functions": []}],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    class Project:
        def search(self, query: str, *, route: str, limit: int) -> object:
            file = "src/Client.java"
            return SimpleNamespace(
                route=route,
                elapsed=0.01,
                notes=[],
                hits=[
                    SimpleNamespace(
                        rank=1,
                        name=Path(file).stem,
                        kind="file",
                        file=file,
                        line=1,
                        score=1.0,
                        why="test",
                    )
                ],
            )

    class Viewer:
        url = "http://127.0.0.1:8765"

        def __init__(self) -> None:
            self.records: list[dict[str, object]] = []
            self.summary: dict[str, object] | None = None
            self.closed = False

        def publish(self, record: dict[str, object]) -> None:
            self.records.append(record)

        def finish(self, summary: dict[str, object]) -> None:
            self.summary = summary

        def close(self) -> None:
            self.closed = True

    viewer = Viewer()
    monkeypatch.setattr(module, "BENCHMARK", str(benchmark))
    monkeypatch.setattr(module, "OUTPUT", str(output))
    monkeypatch.setattr(module, "PROJECT_PATHS", {"owner/repo": str(project_root)})
    monkeypatch.setattr(module, "PROJECTS_DIR", str(tmp_path / "projects"))
    monkeypatch.setattr(module, "INDEXES_DIR", str(tmp_path / "indexes"))
    monkeypatch.setattr(module, "ROUTES", ("lexical",))
    monkeypatch.setattr(module, "_llm", lambda: None)
    monkeypatch.setattr(module, "_open_project", lambda *_args, **_kwargs: Project())
    monkeypatch.setattr(module, "_start_viewer", lambda _meta: viewer, raising=False)
    waited_for: list[str] = []
    monkeypatch.setattr(
        module,
        "_wait_for_viewer",
        lambda url: waited_for.append(url),
        raising=False,
    )

    assert module.main() == 0

    report = json.loads(output.read_text(encoding="utf-8"))
    assert [record["key"] for record in viewer.records] == ["1"]
    assert viewer.records[0]["evaluation"] == report["cases"][0]["evaluation"]
    assert viewer.records[0]["evaluation"]["routes"]["lexical"]["metrics"]["file_recall"] == 1.0
    assert report["trace_coverage"][0]["routes"]["lexical"]["file_recall"] == 1.0
    assert viewer.summary == {
        "routes": report["summary"],
        "trace_coverage": report["trace_coverage"],
    }
    assert waited_for == [viewer.url]
    assert viewer.closed is True
    assert viewer.url in capsys.readouterr().out


def test_wait_for_viewer_prints_url_and_stops_on_ctrl_c(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script()

    class InterruptedEvent:
        def wait(self) -> None:
            raise KeyboardInterrupt

    monkeypatch.setattr(module.threading, "Event", InterruptedEvent)

    module._wait_for_viewer("http://127.0.0.1:8765")

    assert capsys.readouterr().out.splitlines() == [
        "evaluation finished; viewer remains available at http://127.0.0.1:8765",
        "press Ctrl+C to exit",
    ]


def test_load_viewer_type_prefers_repository_package_when_run_from_scripts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script()
    root = SCRIPT.parents[1]
    scripts = root / "scripts"
    remaining_paths = [path for path in sys.path if path not in {str(root), str(scripts)}]
    monkeypatch.setattr(sys, "path", [str(scripts), str(root), *remaining_paths])
    monkeypatch.delitem(sys.modules, "evaluation.live_results", raising=False)
    monkeypatch.delitem(sys.modules, "evaluation", raising=False)

    viewer_type = module._load_viewer_type()

    assert viewer_type.__module__ == "evaluation.live_results"


def test_viewer_startup_and_publish_failures_do_not_stop_the_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_script()

    class BrokenViewer:
        @classmethod
        def start(cls, **_kwargs: object) -> object:
            raise RuntimeError("viewer startup failed")

    monkeypatch.setattr(module, "VIEWER_ENABLED", True, raising=False)
    monkeypatch.setattr(module, "_load_viewer_type", lambda: BrokenViewer)
    assert module._start_viewer({"routes": ["lexical"]}) is None

    class PublishFailure:
        url = "http://127.0.0.1:8765"

        def __init__(self) -> None:
            self.publish_calls = 0
            self.finished = False
            self.closed = False

        def publish(self, _record: dict[str, object]) -> None:
            self.publish_calls += 1
            raise RuntimeError("browser observer failed")

        def finish(self, _summary: dict[str, object]) -> None:
            self.finished = True

        def close(self) -> None:
            self.closed = True

    benchmark = tmp_path / "queries.jsonl"
    output = tmp_path / "report.json"
    project_root = tmp_path / "repo"
    project_root.mkdir()
    benchmark.write_text(
        "\n".join(
            json.dumps(
                {
                    "query_id": f"query-{number}",
                    "repo": "owner/repo",
                    "query": query,
                    "answer": [{"file": file, "functions": []}],
                }
            )
            for number, query, file in ((1, "one", "A.java"), (2, "two", "B.java"))
        )
        + "\n",
        encoding="utf-8",
    )

    class Project:
        def search(self, _query: str, *, route: str, limit: int) -> object:
            return SimpleNamespace(route=route, elapsed=0.0, notes=[], hits=[])

    viewer = PublishFailure()
    monkeypatch.setattr(module, "BENCHMARK", str(benchmark))
    monkeypatch.setattr(module, "OUTPUT", str(output))
    monkeypatch.setattr(module, "PROJECT_PATHS", {"owner/repo": str(project_root)})
    monkeypatch.setattr(module, "ROUTES", ("lexical",))
    monkeypatch.setattr(module, "_llm", lambda: None)
    monkeypatch.setattr(module, "_open_project", lambda *_args, **_kwargs: Project())
    monkeypatch.setattr(module, "_start_viewer", lambda _meta: viewer)

    assert module.main() == 0
    assert len(json.loads(output.read_text(encoding="utf-8"))["cases"]) == 2
    assert viewer.publish_calls == 1
    assert viewer.finished is False
    assert viewer.closed is True


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

    assert module.resolve_repo.__module__ == "evaluation.repo_cache"
