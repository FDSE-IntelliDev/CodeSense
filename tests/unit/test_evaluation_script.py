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


def test_project_path_prefers_manual_mapping(tmp_path: Path) -> None:
    module = _load_script()
    manual = tmp_path / "manual"
    cached = tmp_path / "projects" / "owner__repo"
    manual.mkdir()
    cached.mkdir(parents=True)

    found = module._project_path("owner/repo", {"owner/repo": str(manual)}, tmp_path / "projects")

    assert found == manual.resolve()


def test_project_path_clones_missing_repository(tmp_path: Path) -> None:
    module = _load_script()
    projects = tmp_path / "projects"
    calls: list[list[str]] = []

    def clone(command: list[str], *, check: bool) -> None:
        assert check is True
        calls.append(command)
        Path(command[-1]).mkdir(parents=True)

    found = module._project_path("owner/repo", {}, projects, run=clone)

    assert found == (projects / "owner__repo").resolve()
    assert calls == [
        [
            "git",
            "clone",
            "--depth",
            "1",
            "https://github.com/owner/repo.git",
            str(projects / "owner__repo"),
        ]
    ]


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

    score = module._score(hits, answers, include_test_files=False)

    assert score == {
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
        "file_precision": pytest.approx(2 / 3),
        "file_recall": 1.0,
        "function_precision": 0.25,
        "function_recall": 1.0,
    }


def test_evaluate_query_runs_every_route_and_keeps_ranked_hits() -> None:
    module = _load_script()
    calls: list[tuple[str, str, int]] = []

    class Project:
        def search(self, query: str, *, route: str, limit: int) -> object:
            calls.append((query, route, limit))
            return SimpleNamespace(
                route=route,
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
        }
    ]
    assert result["routes"]["codegen"]["metrics"]["file_recall"] == 1.0


def test_evaluate_query_does_not_score_a_fallback_as_the_requested_route() -> None:
    module = _load_script()

    class Project:
        def search(self, query: str, *, route: str, limit: int) -> object:
            return SimpleNamespace(
                route="lexical",
                elapsed=0.1,
                notes=["codegen failed; fell back to lexical"],
                hits=[],
            )

    record = {
        "query": "Find the client send method.",
        "source_event_indices": [4],
        "answer": [{"file": "src/main/java/example/Client.java", "functions": ["send"]}],
    }

    result = module._evaluate_query(Project(), record, routes=("codegen",), limit=20)
    codegen = result["routes"]["codegen"]

    assert codegen["actual_route"] == "lexical"
    assert codegen["error"] == "requested codegen but search used lexical"
    assert "metrics" not in codegen


def test_main_streams_each_completed_query_and_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load_script()
    benchmark = tmp_path / "queries.jsonl"
    output = tmp_path / "report.json"
    project_root = tmp_path / "repo"
    project_root.mkdir()
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
    monkeypatch.setattr(module, "_open_project", lambda *_args: Project())
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
    assert viewer.summary == report["summary"]
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
    monkeypatch.setattr(module, "_open_project", lambda *_args: Project())
    monkeypatch.setattr(module, "_start_viewer", lambda _meta: viewer)

    assert module.main() == 0
    assert len(json.loads(output.read_text(encoding="utf-8"))["cases"]) == 2
    assert viewer.publish_calls == 1
    assert viewer.finished is False
    assert viewer.closed is True
