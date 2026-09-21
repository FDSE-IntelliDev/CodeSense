"""Evaluate mined queries against CodeSense with manually edited parameters."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from codesense import Project
from codesense.llm import LlmConfig

# Edit these values before running the evaluation. Parameters intentionally do
# not come from a CLI, matching the other manual debugging scripts.
BENCHMARK = (
    "/Users/huangzhuochen/PycharmProjects/CodeSense/outputs/open_swe_traces/"
    "codesense-semantic-query.jsonl"
)
PROJECTS_DIR = "/Users/huangzhuochen/PycharmProjects/CodeSense/evaluation/projects"
INDEXES_DIR = f"{PROJECTS_DIR}/.indexes"
OUTPUT = (
    "/Users/huangzhuochen/PycharmProjects/CodeSense/outputs/open_swe_traces/"
    "codesense-evaluation.json"
)

# This mapping wins over PROJECTS_DIR. Use it for repositories already present
# elsewhere on the machine; missing entries are cloned automatically.
PROJECT_PATHS: dict[str, str] = {}

ROUTES = ("lexical", "planned", "codegen")
# ROUTES = ("lexical", "planned")
# ROUTES = ["codegen"]
SEARCH_LIMIT = 20
QUERY_LIMIT = 0
INCLUDE_TEST_FILES = False
GROUNDING_STRATEGY = "lexical"
VIEWER_ENABLED = True
VIEWER_PORT = 8765

BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
MODEL = "qwen3.7-plus"
TIMEOUT = 300.0

_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class _Viewer(Protocol):
    """Narrow observer interface used by the evaluation loop."""

    url: str

    def publish(self, record: Mapping[str, object]) -> None: ...

    def finish(self, summary: Mapping[str, object]) -> None: ...

    def close(self) -> None: ...


def main() -> int:
    """Load the benchmark, prepare repositories, search, score, and save."""
    records = _read_jsonl(Path(BENCHMARK).expanduser())
    llm = _llm()
    viewer = _start_viewer(
        {
            "benchmark": str(Path(BENCHMARK).expanduser()),
            "routes": list(ROUTES),
            "search_limit": SEARCH_LIMIT,
            "query_limit": QUERY_LIMIT,
            "include_test_files": INCLUDE_TEST_FILES,
        }
    )
    viewer_active = viewer is not None
    if viewer is not None:
        print(f"live evaluation: {viewer.url}")
    projects: dict[str, Project] = {}
    project_errors: dict[str, str] = {}
    cases: list[dict[str, object]] = []
    evaluated = 0

    try:
        for case_number, record in enumerate(records, 1):
            if QUERY_LIMIT and evaluated >= QUERY_LIMIT:
                break
            repo = str(record.get("repo") or "").strip()
            if repo not in projects and repo not in project_errors:
                try:
                    root = _project_path(repo, PROJECT_PATHS, Path(PROJECTS_DIR).expanduser())
                    projects[repo] = _open_project(
                        repo,
                        root,
                        Path(INDEXES_DIR).expanduser(),
                        llm,
                    )
                except Exception as exc:  # noqa: BLE001 -- one repository must not stop the run
                    project_errors[repo] = f"{type(exc).__name__}: {exc}"

            if repo in projects:
                evaluation = _evaluate_query(
                    projects[repo],
                    record,
                    routes=ROUTES,
                    limit=SEARCH_LIMIT,
                    include_test_files=INCLUDE_TEST_FILES,
                )
            else:
                evaluation = _failed_query(
                    record, ROUTES, project_errors.get(repo, "project unavailable")
                )
            case_result = {
                "query_id": record.get("query_id"),
                "repo": repo,
                "instance_id": record.get("instance_id"),
                "trajectory_id": record.get("trajectory_id"),
                "evaluation": evaluation,
            }
            cases.append(case_result)
            if viewer_active and viewer is not None:
                viewer_active = _publish_viewer(
                    viewer,
                    {"key": str(case_number), **case_result},
                )
            evaluated += not evaluation.get("skipped", False)

        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "benchmark": str(Path(BENCHMARK).expanduser().resolve()),
            "config": {
                "routes": list(ROUTES),
                "search_limit": SEARCH_LIMIT,
                "query_limit": QUERY_LIMIT,
                "include_test_files": INCLUDE_TEST_FILES,
                "grounding_strategy": GROUNDING_STRATEGY,
                "model": MODEL,
            },
            "summary": _summarize(cases, ROUTES),
            "cases": cases,
        }
        output = Path(OUTPUT).expanduser()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        if viewer_active and viewer is not None:
            _finish_viewer(viewer, _mapping(report["summary"]))
        print(f"evaluated {evaluated} queries; report: {output.resolve()}")
        if viewer_active and viewer is not None:
            _wait_for_viewer(viewer.url)
        return 0
    finally:
        if viewer is not None:
            _close_viewer(viewer)


def _start_viewer(meta: Mapping[str, object]) -> _Viewer | None:
    """Start the optional observer; a bind failure must not stop evaluation."""
    if not VIEWER_ENABLED:
        return None
    try:
        return _load_viewer_type().start(port=VIEWER_PORT, meta=meta)
    except Exception as exc:  # noqa: BLE001 -- an observer must never stop evaluation
        print(f"live evaluation unavailable: {type(exc).__name__}: {exc}")
        return None


def _load_viewer_type() -> Any:
    """Load the repository-local evaluation package when this script is run directly."""
    root = str(Path(__file__).resolve().parents[1])
    # Direct execution puts ``scripts/`` first, where this file would shadow
    # the sibling ``evaluation`` package. Promote the repository root even
    # when an IDE has already added it later in the import path.
    with suppress(ValueError):
        sys.path.remove(root)
    sys.path.insert(0, root)
    from evaluation.live_results import LiveEvaluationViewer

    return LiveEvaluationViewer


def _publish_viewer(viewer: _Viewer, record: Mapping[str, object]) -> bool:
    """Publish one completed query and disable streaming after observer failure."""
    try:
        viewer.publish(record)
        return True
    except Exception as exc:  # noqa: BLE001 -- an observer must never stop evaluation
        print(f"live evaluation disabled: {type(exc).__name__}: {exc}")
        return False


def _finish_viewer(viewer: _Viewer, summary: Mapping[str, object]) -> None:
    """Best-effort final summary publication."""
    try:
        viewer.finish(summary)
    except Exception as exc:  # noqa: BLE001 -- preserve the completed report
        print(f"live evaluation summary failed: {type(exc).__name__}: {exc}")


def _close_viewer(viewer: _Viewer) -> None:
    """Release optional viewer resources without changing evaluation outcome."""
    try:
        viewer.close()
    except Exception as exc:  # noqa: BLE001 -- preserve the evaluation outcome
        print(f"live evaluation shutdown failed: {type(exc).__name__}: {exc}")


def _wait_for_viewer(url: str) -> None:
    """Keep the in-memory result page available until the user stops it."""
    print(f"evaluation finished; viewer remains available at {url}")
    print("press Ctrl+C to exit")
    with suppress(KeyboardInterrupt):
        threading.Event().wait()


def _project_path(
    repo: str,
    manual_paths: Mapping[str, str],
    projects_dir: Path,
    *,
    run: Callable[..., object] = subprocess.run,
) -> Path:
    """Resolve a manual project, cached clone, or shallow-clone latest HEAD."""
    manual = manual_paths.get(repo)
    if manual:
        path = Path(manual).expanduser().resolve()
        if not path.is_dir():
            raise NotADirectoryError(path)
        return path
    if not _REPOSITORY.fullmatch(repo):
        raise ValueError(f"invalid GitHub repository name: {repo!r}")

    projects_dir.mkdir(parents=True, exist_ok=True)
    target = projects_dir / repo.replace("/", "__")
    if target.is_dir():
        return target.resolve()
    run(
        [
            "git",
            "clone",
            "--depth",
            "1",
            f"https://github.com/{repo}.git",
            str(target),
        ],
        check=True,
    )
    if not target.is_dir():
        raise RuntimeError(f"git clone did not create {target}")
    return target.resolve()


def _open_project(repo: str, root: Path, indexes_dir: Path, llm: object) -> Project:
    """Reuse one repository index, building the lexical index on first use."""
    index_dir = indexes_dir / repo.replace("/", "__")
    if (index_dir / "meta.json").is_file():
        return Project.open(index_dir, llm=llm)
    return Project.build(
        root,
        index_dir=index_dir,
        strategy=GROUNDING_STRATEGY,
        llm=llm,
        name=repo,
    )


def _evaluate_query(
    project: object,
    record: Mapping[str, object],
    *,
    routes: Sequence[str],
    limit: int,
    include_test_files: bool = False,
) -> dict[str, object]:
    """Run one benchmark query through each route and retain inspectable hits."""
    query = str(record.get("query") or "").strip()
    answers = list(_mappings(record.get("answer")))
    usable_answers = [
        answer
        for answer in answers
        if include_test_files or not _is_test_file(str(answer.get("file") or ""))
    ]
    base: dict[str, object] = {
        "query": query,
        "source_event_indices": list(_integers(record.get("source_event_indices"))),
        "answer": answers,
    }
    if not usable_answers:
        return {**base, "skipped": True, "skip_reason": "no production-code gold", "routes": {}}

    route_results: dict[str, object] = {}
    for route in routes:
        try:
            result = project.search(query, route=route, limit=limit)
            route_result = {
                "actual_route": result.route,
                "elapsed": result.elapsed,
                "notes": list(result.notes),
                "script": str(getattr(result, "script", "") or ""),
                "hits": [_hit_dict(hit) for hit in result.hits],
            }
            if result.route == route:
                route_result["metrics"] = _score(
                    result.hits, answers, include_test_files=include_test_files
                )
            else:
                route_result["error"] = f"requested {route} but search used {result.route}"
            route_results[route] = route_result
        except Exception as exc:  # noqa: BLE001 -- preserve other route results
            route_results[route] = {"error": f"{type(exc).__name__}: {exc}", "hits": []}
    return {**base, "skipped": False, "routes": route_results}


def _failed_query(
    record: Mapping[str, object], routes: Sequence[str], error: str
) -> dict[str, object]:
    return {
        "query": str(record.get("query") or "").strip(),
        "source_event_indices": list(_integers(record.get("source_event_indices"))),
        "answer": list(_mappings(record.get("answer"))),
        "skipped": False,
        "routes": {route: {"error": error, "hits": []} for route in routes},
    }


def _score(
    hits: Sequence[object],
    answers: Sequence[Mapping[str, object]],
    *,
    include_test_files: bool,
) -> dict[str, object]:
    """Compute file-level metrics and optional exact function-level metrics."""
    gold_locations = [
        answer
        for answer in answers
        if include_test_files or not _is_test_file(str(answer.get("file") or ""))
    ]
    gold_files = _unique(_path(answer.get("file")) for answer in gold_locations)
    hit_files = _unique(_path(getattr(hit, "file", "")) for hit in hits)
    matched_files = [file for file in gold_files if file in set(hit_files)]

    gold_functions = _unique_pairs(
        (_path(answer.get("file")), _function_name(function))
        for answer in gold_locations
        for function in _strings(answer.get("functions"))
    )
    hit_functions = _unique_pairs(
        (_path(getattr(hit, "file", "")), _function_name(getattr(hit, "name", ""))) for hit in hits
    )
    matched_functions = [pair for pair in gold_functions if pair in set(hit_functions)]

    return {
        "gold_files": gold_files,
        "gold_functions": [_pair_dict(pair) for pair in gold_functions],
        "matched_files": matched_files,
        "matched_functions": [_pair_dict(pair) for pair in matched_functions],
        "file_precision": _ratio(len(matched_files), len(hit_files)),
        "file_recall": _ratio(len(matched_files), len(gold_files)),
        "function_precision": (
            _ratio(len(matched_functions), len(hit_functions)) if gold_functions else None
        ),
        "function_recall": (
            _ratio(len(matched_functions), len(gold_functions)) if gold_functions else None
        ),
    }


def _hit_dict(hit: object) -> dict[str, object]:
    return {
        "rank": getattr(hit, "rank", 0),
        "name": getattr(hit, "name", ""),
        "kind": getattr(hit, "kind", ""),
        "file": getattr(hit, "file", ""),
        "line": getattr(hit, "line", 0),
        "score": getattr(hit, "score", 0.0),
        "why": getattr(hit, "why", ""),
    }


def _summarize(cases: Sequence[Mapping[str, object]], routes: Sequence[str]) -> dict[str, object]:
    summary: dict[str, object] = {}
    for route in routes:
        results = [
            route_result
            for case in cases
            for evaluation in [_mapping(case.get("evaluation"))]
            if not evaluation.get("skipped")
            for route_result in [_mapping(_mapping(evaluation.get("routes")).get(route))]
        ]
        valid = [result for result in results if "metrics" in result]
        metrics = [_mapping(result.get("metrics")) for result in valid]
        summary[route] = {
            "queries": len(results),
            "completed": len(valid),
            "errors": sum("error" in result for result in results),
            "file_precision": _mean(metric.get("file_precision") for metric in metrics),
            "file_recall": _mean(metric.get("file_recall") for metric in metrics),
            "function_precision": _mean(metric.get("function_precision") for metric in metrics),
            "function_recall": _mean(metric.get("function_recall") for metric in metrics),
        }
    return summary


def _llm() -> LlmConfig | None:
    if all(route == "lexical" for route in ROUTES):
        return None
    return LlmConfig.load(base_url=BASE_URL, model=MODEL, timeout=TIMEOUT)


def _read_jsonl(path: Path) -> list[Mapping[str, object]]:
    records: list[Mapping[str, object]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, Mapping):
                raise ValueError(f"line {line_number} must contain a JSON object")
            records.append(value)
    return records


def _is_test_file(file: str) -> bool:
    path = _path(file).lower()
    parts = path.split("/")
    return (
        "/src/test/" in f"/{path}/"
        or any(part in {"test", "tests"} for part in parts[:-1])
        or (bool(parts) and parts[-1].endswith("test.java"))
    )


def _path(value: object) -> str:
    return str(value or "").strip().replace("\\", "/").removeprefix("./")


def _function_name(value: object) -> str:
    return str(value or "").strip().split("(", 1)[0].rsplit(".", 1)[-1]


def _mappings(value: object) -> tuple[Mapping[str, object], ...]:
    return tuple(item for item in _sequence(value) if isinstance(item, Mapping))


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _integers(value: object) -> tuple[int, ...]:
    return tuple(
        item for item in _sequence(value) if isinstance(item, int) and not isinstance(item, bool)
    )


def _strings(value: object) -> tuple[str, ...]:
    return tuple(item for item in _sequence(value) if isinstance(item, str) and item.strip())


def _sequence(value: object) -> Sequence[object]:
    return value if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) else ()


def _unique(values: Any) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _unique_pairs(values: Any) -> list[tuple[str, str]]:
    return list(dict.fromkeys(pair for pair in values if all(pair)))


def _pair_dict(pair: tuple[str, str]) -> dict[str, str]:
    return {"file": pair[0], "function": pair[1]}


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _mean(values: Any) -> float | None:
    numbers = [float(value) for value in values if isinstance(value, (int, float))]
    return sum(numbers) / len(numbers) if numbers else None


if __name__ == "__main__":
    raise SystemExit(main())
