"""Evaluate mined queries against CodeSense with manually edited parameters."""

from __future__ import annotations

import json
import sys
import threading
from collections.abc import Mapping, Sequence
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from codesense import Project
from codesense.llm import LlmConfig

# Edit these values before running the evaluation. Parameters intentionally do
# not come from a CLI, matching the other manual debugging scripts.
_ROOT = Path(__file__).resolve().parents[1]
BENCHMARK = str(_ROOT / "outputs/open_swe_traces/codesense-semantic-query.jsonl")
PROJECTS_DIR = str(_ROOT / "evaluation/projects")
INDEXES_DIR = f"{PROJECTS_DIR}/.indexes"
OUTPUT = str(_ROOT / "outputs/open_swe_traces/codesense-evaluation.json")

# This mapping wins over PROJECTS_DIR. Use it for repositories already present
# elsewhere on the machine; missing entries are cloned automatically.
PROJECT_PATHS: dict[str, str] = {}

ROUTES = ("lexical", "planned", "codegen")
# ROUTES = ("lexical", "planned")
# ROUTES = ["codegen"]
SEARCH_LIMIT = 20
QUERY_LIMIT = 0
# Optional benchmark slicing. ``RUN_ONLY`` takes precedence when both modes
# are enabled; identifiers are matched against each record's ``query_id``.
RUN_ONLY = False
QUERY_LIST: list[str] = []
START_FROM = False
START_ID = ""
INCLUDE_TEST_FILES = False
GROUNDING_STRATEGY = "lexical"
VIEWER_ENABLED = True
VIEWER_PORT = 8765

BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
MODEL = "qwen3.7-plus"
TIMEOUT = 300.0

# Keep the repository-local evaluation package importable when this file is
# launched as ``python scripts/evaluation.py``. The scripts directory holds
# evaluation.py, which would otherwise shadow the sibling ``evaluation``
# package; promote the repository root even when an IDE added it later.
with suppress(ValueError):
    sys.path.remove(str(_ROOT))
sys.path.insert(0, str(_ROOT))

from evaluation.repo_cache import resolve_repo  # noqa: E402

#: Gold fields checked against the checkout, in the order they are reported.
_GOLD_FIELDS = ("answer", "trace_answer", "candidate_answers")
#: Characters that make a gold path a glob rather than a real file location.
_GLOB_CHARS = frozenset("*?[]")


class _Viewer(Protocol):
    """Narrow observer interface used by the evaluation loop."""

    url: str

    def publish(self, record: Mapping[str, object]) -> None: ...

    def finish(self, summary: Mapping[str, object]) -> None: ...

    def close(self) -> None: ...


def main() -> int:
    """Load the benchmark, prepare repositories, search, score, and save."""
    records = _read_jsonl(Path(BENCHMARK).expanduser())
    selection = {
        "run_only": RUN_ONLY,
        "query_list": list(QUERY_LIST),
        "start_from": START_FROM,
        "start_id": START_ID,
    }
    records = _select_records(
        records,
        run_only=RUN_ONLY,
        query_list=QUERY_LIST,
        start_from=START_FROM,
        start_id=START_ID,
    )
    llm = _llm()
    viewer = _start_viewer(
        {
            "benchmark": str(Path(BENCHMARK).expanduser()),
            "routes": list(ROUTES),
            "search_limit": SEARCH_LIMIT,
            "query_limit": QUERY_LIMIT,
            "include_test_files": INCLUDE_TEST_FILES,
            "selection": selection,
        }
    )
    viewer_active = viewer is not None
    if viewer is not None:
        print(f"live evaluation: {viewer.url}")
    projects: dict[tuple[str, str | None], Project] = {}
    project_errors: dict[tuple[str, str | None], str] = {}
    project_roots: dict[tuple[str, str | None], Path] = {}
    cases: list[dict[str, object]] = []
    evaluated = 0

    try:
        for case_number, record in enumerate(records, 1):
            if QUERY_LIMIT and evaluated >= QUERY_LIMIT:
                break
            repo = str(record.get("repo") or "").strip()
            base_commit = _text_or_none(record.get("base_commit"))
            # Keyed by commit as well as repo: two revisions of one repository
            # need separate checkouts and separate indexes.
            key = (repo, base_commit)
            if key not in projects and key not in project_errors:
                try:
                    root = resolve_repo(
                        repo,
                        base_commit,
                        Path(PROJECTS_DIR).expanduser(),
                        manual_paths=PROJECT_PATHS,
                    )
                    project_roots[key] = root
                    projects[key] = _open_project(
                        repo,
                        root,
                        Path(INDEXES_DIR).expanduser(),
                        llm,
                        base_commit=base_commit,
                    )
                except Exception as exc:  # noqa: BLE001 -- one repository must not stop the run
                    project_errors[key] = f"{type(exc).__name__}: {exc}"

            if key in projects:
                evaluation = _evaluate_query(
                    projects[key],
                    record,
                    routes=ROUTES,
                    limit=SEARCH_LIMIT,
                    include_test_files=INCLUDE_TEST_FILES,
                    root=project_roots.get(key),
                )
            else:
                evaluation = _failed_query(
                    record, ROUTES, project_errors.get(key, "project unavailable")
                )
            case_result = {
                "query_id": record.get("query_id"),
                "repo": repo,
                "base_commit": base_commit,
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

            # if case_number % 5 == 0:
            #     break

        trace_coverage = _trace_coverage(
            cases,
            ROUTES,
            include_test_files=INCLUDE_TEST_FILES,
        )
        summary = _summarize(cases, ROUTES, trace_coverage=trace_coverage)
        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "benchmark": str(Path(BENCHMARK).expanduser().resolve()),
            "config": {
                "routes": list(ROUTES),
                "search_limit": SEARCH_LIMIT,
                "query_limit": QUERY_LIMIT,
                "selection": selection,
                "include_test_files": INCLUDE_TEST_FILES,
                "grounding_strategy": GROUNDING_STRATEGY,
                "model": MODEL,
            },
            "summary": summary,
            "trace_coverage": trace_coverage,
            "cases": cases,
        }
        output = Path(OUTPUT).expanduser()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        if viewer_active and viewer is not None:
            _finish_viewer(
                viewer,
                {"routes": summary, "trace_coverage": trace_coverage},
            )
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


def _open_project(
    repo: str,
    root: Path,
    indexes_dir: Path,
    llm: object,
    *,
    base_commit: str | None = None,
) -> Project:
    """Reuse one repository index, isolated by commit, building it on first use.

    The index directory name carries the commit so two revisions of the same
    repository never share an artifact. When the directory already exists its
    ``meta.json`` commit is checked against the expected one; a mismatch means
    the artifact predates commit isolation, so it is rebuilt rather than trusted.
    """
    name = repo.replace("/", "__")
    if base_commit:
        name = f"{name}__{base_commit}"
    index_dir = indexes_dir / name
    if (index_dir / "meta.json").is_file():
        project = Project.open(index_dir, llm=llm)
        if not base_commit or project.index.meta.commit == base_commit:
            return project
    return Project.build(
        root,
        index_dir=index_dir,
        strategy=GROUNDING_STRATEGY,
        llm=llm,
        name=repo,
        commit=base_commit or "",
    )


def _evaluate_query(
    project: object,
    record: Mapping[str, object],
    *,
    routes: Sequence[str],
    limit: int,
    include_test_files: bool = False,
    root: Path | None = None,
) -> dict[str, object]:
    """Run one benchmark query through each route and retain inspectable hits.

    ``root`` is the checked-out repository. When given, every gold path is
    verified against it first: a glob or a path absent from this revision cannot
    be scored honestly, so a case whose *primary* gold is invalid is skipped
    rather than allowed to deflate recall. Problems on bonus gold are recorded
    for inspection but do not skip the case.
    """
    query = str(record.get("query") or "").strip()
    answers = list(_mappings(record.get("answer")))
    trace_answers = list(_mappings(record.get("trace_answer")))
    candidates = list(_mappings(record.get("candidate_answers")))
    usable_answers = _scoreable_locations(
        answers,
        include_test_files=include_test_files,
    )
    base: dict[str, object] = {
        "query": query,
        "source_event_indices": list(_integers(record.get("source_event_indices"))),
        "answer": answers,
        "trace_answer": trace_answers,
        "candidate_answers": candidates,
    }
    gold_problems = _gold_problems(record, root) if root is not None else []
    if gold_problems:
        base["gold_problems"] = gold_problems
    if not usable_answers:
        return {**base, "skipped": True, "skip_reason": "no production-code gold", "routes": {}}
    if _primary_gold_invalid(usable_answers, gold_problems):
        return {**base, "skipped": True, "skip_reason": "invalid gold path", "routes": {}}

    route_results: dict[str, object] = {}
    for route in routes:
        try:
            result = project.search(query, route=route, limit=limit)
            metrics, labels = _score(
                result.hits,
                answers,
                candidates,
                trace_answers=trace_answers,
                include_test_files=include_test_files,
            )
            route_result = {
                "actual_route": result.route,
                # Route fidelity is a separate dimension from retrieval quality:
                # a degraded search still returns hits worth scoring, so metrics
                # are always kept and the fallback is flagged rather than thrown
                # away as an error. Only a raised exception loses the metrics.
                "route_fidelity": result.route == route,
                "elapsed": result.elapsed,
                "notes": list(result.notes),
                "script": str(getattr(result, "script", "") or ""),
                "metrics": metrics,
                "hits": [
                    {**_hit_dict(hit), "label": label}
                    for hit, label in zip(result.hits, labels, strict=True)
                ],
            }
            route_results[route] = route_result
        except Exception as exc:  # noqa: BLE001 -- preserve other route results
            route_results[route] = {
                "error": f"{type(exc).__name__}: {exc}",
                "route_fidelity": False,
                "hits": [],
            }
    return {**base, "skipped": False, "routes": route_results}


def _failed_query(
    record: Mapping[str, object], routes: Sequence[str], error: str
) -> dict[str, object]:
    return {
        "query": str(record.get("query") or "").strip(),
        "source_event_indices": list(_integers(record.get("source_event_indices"))),
        "answer": list(_mappings(record.get("answer"))),
        "trace_answer": list(_mappings(record.get("trace_answer"))),
        "candidate_answers": list(_mappings(record.get("candidate_answers"))),
        "skipped": False,
        "routes": {
            route: {"error": error, "route_fidelity": False, "hits": []} for route in routes
        },
    }


def _gold_problems(record: Mapping[str, object], root: Path) -> list[dict[str, str]]:
    """List every gold path that is a glob or absent from the checked-out tree.

    Deduplicated by path so a file listed as both primary and bonus gold is
    reported once, under the first field that names it.
    """
    problems: list[dict[str, str]] = []
    seen: set[str] = set()
    for field_name in _GOLD_FIELDS:
        for location in _mappings(record.get(field_name)):
            file = _path(location.get("file"))
            if not file or file in seen:
                continue
            seen.add(file)
            reason = _gold_path_problem(file, root)
            if reason is not None:
                problems.append({"file": file, "reason": reason, "field": field_name})
    return problems


def _gold_path_problem(file: str, root: Path) -> str | None:
    """Classify one gold path: ``glob``, ``missing``, or None when it is valid."""
    if any(character in file for character in _GLOB_CHARS):
        return "glob"
    if not (root / file).exists():
        return "missing"
    return None


def _primary_gold_invalid(
    usable_answers: Sequence[Mapping[str, object]],
    problems: Sequence[Mapping[str, str]],
) -> bool:
    """True when any scoreable primary gold path cannot be trusted."""
    invalid = {problem["file"] for problem in problems}
    return any(_path(answer.get("file")) in invalid for answer in usable_answers)


def _score(
    hits: Sequence[object],
    answers: Sequence[Mapping[str, object]],
    candidate_answers: Sequence[Mapping[str, object]],
    *,
    trace_answers: Sequence[Mapping[str, object]] = (),
    include_test_files: bool,
) -> tuple[dict[str, object], list[str]]:
    """Score query gold, the augmented gold, and trace-only bonus coverage."""
    primary = _location_metrics(hits, answers, include_test_files=include_test_files)
    trace = _location_metrics(hits, trace_answers, include_test_files=include_test_files)
    augmented = _location_metrics(
        hits,
        (*answers, *trace_answers),
        include_test_files=include_test_files,
    )
    gold_file_set = set(_strings(primary.get("gold_files")))
    trace_file_set = set(_strings(trace.get("gold_files"))) - gold_file_set
    candidate_files = (
        set(
            _unique(
                _path(answer.get("file"))
                for answer in _scoreable_locations(
                    candidate_answers,
                    include_test_files=include_test_files,
                )
            )
        )
        - gold_file_set
        - trace_file_set
    )
    hit_paths = [_hit_file(hit) for hit in hits]
    labels = [
        (
            "gold_hit"
            if path in gold_file_set
            else "trace_answer_hit"
            if path in trace_file_set
            else "candidate_hit"
            if path in candidate_files
            else "unlabeled_hit"
        )
        for path in hit_paths
    ]
    metrics = dict(primary)
    metrics["query_plus_trace_answer"] = augmented
    metrics["trace_answer"] = {
        "files": trace["gold_files"],
        "functions": trace["gold_functions"],
        "matched_files": trace["matched_files"],
        "matched_functions": trace["matched_functions"],
        "file_recall": trace["file_recall"],
        "function_recall": trace["function_recall"],
    }
    return metrics, labels


def _location_metrics(
    hits: Sequence[object],
    locations: Sequence[Mapping[str, object]],
    *,
    include_test_files: bool,
) -> dict[str, object]:
    """Compute one consistent metric set for a collection of answer locations."""
    gold_locations = _scoreable_locations(
        locations,
        include_test_files=include_test_files,
    )
    gold_files = _unique(_path(location.get("file")) for location in gold_locations)
    gold_file_set = set(gold_files)
    hit_paths = [_hit_file(hit) for hit in hits]
    hit_files = _unique(hit_paths)
    hit_file_set = set(hit_files)
    matched_files = [file for file in gold_files if file in hit_file_set]
    first_gold_rank = next(
        (index for index, path in enumerate(hit_paths, 1) if path in gold_file_set),
        None,
    )

    gold_functions = _unique_pairs(
        (_path(location.get("file")), _function_name(function))
        for location in gold_locations
        for function in _strings(location.get("functions"))
    )
    hit_functions = _unique_pairs((_hit_file(hit), _function_name(_hit_name(hit))) for hit in hits)
    hit_function_set = set(hit_functions)
    matched_functions = [pair for pair in gold_functions if pair in hit_function_set]
    return {
        "gold_files": gold_files,
        "gold_functions": [_pair_dict(pair) for pair in gold_functions],
        "matched_files": matched_files,
        "matched_functions": [_pair_dict(pair) for pair in matched_functions],
        "observed_file_precision": _ratio(len(matched_files), len(hit_files)),
        "file_recall": _ratio(len(matched_files), len(gold_files)),
        "first_gold_rank": first_gold_rank,
        "mrr": 1 / first_gold_rank if first_gold_rank is not None else 0.0,
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


def _trace_coverage(
    cases: Sequence[Mapping[str, object]],
    routes: Sequence[str],
    *,
    include_test_files: bool,
) -> list[dict[str, object]]:
    """Measure how the union of each trace's query hits covers its final answer."""
    grouped: dict[tuple[str, str, str], list[Mapping[str, object]]] = {}
    for case in cases:
        trajectory = str(case.get("trajectory_id") or case.get("query_id") or "")
        key = (
            str(case.get("repo") or ""),
            str(case.get("instance_id") or ""),
            trajectory,
        )
        grouped.setdefault(key, []).append(case)

    coverage: list[dict[str, object]] = []
    for (repo, instance_id, trajectory_id), trace_cases in grouped.items():
        trace_answers = _merge_locations(
            *(
                _scoreable_locations(
                    _mappings(_mapping(case.get("evaluation")).get("trace_answer")),
                    include_test_files=include_test_files,
                )
                for case in trace_cases
            )
        )
        # Some mined traces have no patch-derived final answer. They cannot
        # contribute a meaningful coverage denominator, so keep them only in
        # per-query evaluation and omit them from this secondary aggregate.
        if not trace_answers:
            continue
        route_coverage: dict[str, object] = {}
        for route in routes:
            # A degraded query still returned hits, so end-to-end coverage counts
            # it; route fidelity is reported separately rather than used to drop
            # the fallback's contribution.
            completed_results = [
                result
                for case in trace_cases
                for evaluation in [_mapping(case.get("evaluation"))]
                if not evaluation.get("skipped")
                for result in [_mapping(_mapping(evaluation.get("routes")).get(route))]
                if "metrics" in result
            ]
            hits = [hit for result in completed_results for hit in _mappings(result.get("hits"))]
            metrics = _location_metrics(
                hits,
                trace_answers,
                include_test_files=include_test_files,
            )
            route_coverage[route] = {
                "completed_queries": len(completed_results),
                "faithful_queries": sum(
                    bool(result.get("route_fidelity")) for result in completed_results
                ),
                "matched_files": metrics["matched_files"],
                "matched_functions": metrics["matched_functions"],
                "file_recall": metrics["file_recall"],
                "function_recall": metrics["function_recall"],
            }
        coverage.append(
            {
                "repo": repo,
                "instance_id": instance_id,
                "trajectory_id": trajectory_id,
                "query_count": len(trace_cases),
                "trace_answer": trace_answers,
                "routes": route_coverage,
            }
        )
    return coverage


def _summarize(
    cases: Sequence[Mapping[str, object]],
    routes: Sequence[str],
    *,
    trace_coverage: Sequence[Mapping[str, object]] = (),
) -> dict[str, object]:
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
        faithful = [result for result in valid if result.get("route_fidelity")]
        metrics = [_mapping(result.get("metrics")) for result in valid]
        augmented = [_mapping(metric.get("query_plus_trace_answer")) for metric in metrics]
        trace_metrics = [
            _mapping(_mapping(trace.get("routes")).get(route)) for trace in trace_coverage
        ]
        summary[route] = {
            "queries": len(results),
            "completed": len(valid),
            "errors": sum("error" in result for result in results),
            # Route fidelity is reported apart from retrieval quality: `completed`
            # counts every search that returned hits (faithful or degraded), while
            # `degraded` isolates how many fell back to another route.
            "degraded": len(valid) - len(faithful),
            "route_fidelity": _ratio(len(faithful), len(valid)),
            "observed_file_precision": _mean(
                metric.get("observed_file_precision") for metric in metrics
            ),
            "file_recall": _mean(metric.get("file_recall") for metric in metrics),
            "mrr": _mean(metric.get("mrr") for metric in metrics),
            "function_precision": _mean(metric.get("function_precision") for metric in metrics),
            "function_recall": _mean(metric.get("function_recall") for metric in metrics),
            "query_plus_trace_answer": {
                "observed_file_precision": _mean(
                    metric.get("observed_file_precision") for metric in augmented
                ),
                "file_recall": _mean(metric.get("file_recall") for metric in augmented),
                "mrr": _mean(metric.get("mrr") for metric in augmented),
                "function_precision": _mean(
                    metric.get("function_precision") for metric in augmented
                ),
                "function_recall": _mean(metric.get("function_recall") for metric in augmented),
            },
            "trace_answer_coverage": {
                "traces": len(trace_metrics),
                "file_recall": _mean(metric.get("file_recall") for metric in trace_metrics),
                "function_recall": _mean(metric.get("function_recall") for metric in trace_metrics),
            },
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


def _select_records(
    records: Sequence[Mapping[str, object]],
    *,
    run_only: bool,
    query_list: Sequence[str],
    start_from: bool,
    start_id: str,
) -> list[Mapping[str, object]]:
    """Select benchmark records by query ID before any project work starts.

    ``run_only`` has precedence over ``start_from`` so accidentally enabling
    both modes cannot broaden a deliberately narrow query list. Selection
    always preserves benchmark order, while invalid IDs fail fast instead of
    silently producing a misleading empty or partial report.
    """
    if run_only:
        requested = tuple(
            dict.fromkeys(query_id.strip() for query_id in query_list if query_id.strip())
        )
        if not requested:
            raise ValueError("RUN_ONLY requires at least one query ID in QUERY_LIST")
        available = {str(record.get("query_id") or "") for record in records}
        missing = [query_id for query_id in requested if query_id not in available]
        if missing:
            raise ValueError(f"query IDs not found in benchmark: {missing!r}")
        requested_set = set(requested)
        return [record for record in records if str(record.get("query_id") or "") in requested_set]

    if start_from:
        first_id = start_id.strip()
        if not first_id:
            raise ValueError("START_FROM requires START_ID")
        for index, record in enumerate(records):
            if str(record.get("query_id") or "") == first_id:
                return list(records[index:])
        raise ValueError(f"query ID not found in benchmark: {first_id!r}")

    return list(records)


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


def _text_or_none(value: object) -> str | None:
    """Normalize an optional string field, treating blank as absent."""
    return value.strip() if isinstance(value, str) and value.strip() else None


def _function_name(value: object) -> str:
    return str(value or "").strip().split("(", 1)[0].rsplit(".", 1)[-1]


def _mappings(value: object) -> tuple[Mapping[str, object], ...]:
    return tuple(item for item in _sequence(value) if isinstance(item, Mapping))


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _hit_file(hit: object) -> str:
    """Read a normalized hit path from either runtime objects or saved mappings."""
    value = hit.get("file") if isinstance(hit, Mapping) else getattr(hit, "file", "")
    return _path(value)


def _hit_name(hit: object) -> str:
    """Read a hit name from either runtime objects or saved mappings."""
    value = hit.get("name") if isinstance(hit, Mapping) else getattr(hit, "name", "")
    return str(value or "")


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


def _merge_locations(
    *groups: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    """Union answer locations by file while preserving first-seen order."""
    merged: dict[str, list[str]] = {}
    for group in groups:
        for location in group:
            file = _path(location.get("file"))
            if not file:
                continue
            functions = merged.setdefault(file, [])
            for function in _strings(location.get("functions")):
                if function not in functions:
                    functions.append(function)
    return [{"file": file, "functions": functions} for file, functions in merged.items()]


def _scoreable_locations(
    locations: Sequence[Mapping[str, object]],
    *,
    include_test_files: bool,
) -> list[dict[str, object]]:
    """Return revision-valid gold while preserving legacy benchmark support.

    New benchmarks annotate every location. A missing file is excluded
    completely; an existing file remains valid file-level gold while only
    functions explicitly marked present contribute function-level gold.
    Records without the annotations retain the previous scoring behaviour.
    """
    scoreable: list[dict[str, object]] = []
    for location in locations:
        file = str(location.get("file") or "")
        if location.get("file_exist") is False:
            continue
        if not include_test_files and _is_test_file(file):
            continue
        functions = list(_strings(location.get("functions")))
        existence = location.get("function_exist")
        if isinstance(existence, Mapping):
            functions = [function for function in functions if existence.get(function) is True]
        scoreable.append({**dict(location), "functions": functions})
    return scoreable


def _pair_dict(pair: tuple[str, str]) -> dict[str, str]:
    return {"file": pair[0], "function": pair[1]}


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _mean(values: Any) -> float | None:
    numbers = [float(value) for value in values if isinstance(value, (int, float))]
    return sum(numbers) / len(numbers) if numbers else None


if __name__ == "__main__":
    raise SystemExit(main())
