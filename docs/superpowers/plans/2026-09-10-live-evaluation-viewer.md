# Live Evaluation Viewer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stream each completed benchmark query into one local browser page showing gold answers, route hits, metrics, matched answers, and missed answers.

**Architecture:** Add a standard-library `LiveResultStore` plus `ThreadingHTTPServer`/SSE facade in `evaluation/live_results.py`. `scripts/evaluation.py` starts the facade, prints its loopback URL, publishes exactly once after each query's routes finish, and preserves the existing final JSON report.

**Tech Stack:** Python 3.11 standard library (`http.server`, `queue`, `threading`, `json`), browser `EventSource`, embedded HTML/CSS/JavaScript, pytest.

**Spec:** `docs/superpowers/specs/2026-09-10-live-evaluation-viewer-design.md`

## Global Constraints

- Bind only `127.0.0.1`; do not open a browser or expose a remote host option.
- Add no third-party dependency and do not change `_score()` or the final evaluation JSON schema.
- One publish occurs after one query's complete route result, including skipped and failed results.
- Viewer startup, browser absence, disconnects, and publish failures must not stop evaluation.
- Dynamic content must be inserted with DOM `textContent`; do not interpolate benchmark data into executable HTML.
- `scripts/evaluation.py`, `evaluation/query_viewer.py`, `evaluation/README.md`, and their existing tests are user-owned uncommitted files. Preserve all existing content and do not stage or commit them without separate authorization.
- Run all Python commands in the `codesearch` conda environment.

---

### Task 1: In-memory result stream and safe page renderer

**Files:**
- Create: `evaluation/live_results.py`
- Create: `tests/unit/evaluation/test_live_results.py`

**Interfaces:**
- Produces: `LiveResultStore(meta: Mapping[str, object] | None = None)`.
- Produces: `LiveResultStore.publish(record: Mapping[str, object]) -> None`.
- Produces: `LiveResultStore.finish(summary: Mapping[str, object]) -> None`.
- Produces: `LiveResultStore.close() -> None`, which sends one internal close event to every current subscriber.
- Produces: `LiveResultStore.snapshot() -> dict[str, object]`.
- Produces: `LiveResultStore.subscribe() -> tuple[queue.Queue[tuple[str, dict[str, object]]], dict[str, object]]` and `unsubscribe(queue) -> None`; registration and snapshot capture happen under one lock.
- Produces: `viewer_page() -> str`, a data-independent HTML document whose JavaScript consumes `snapshot`, `query`, and `summary` SSE events.
- Consumes in later tasks: immutable JSON-compatible copies of evaluation records.

- [ ] **Step 1: Write failing store tests**

Add tests that define the required ordering, copy isolation, atomic subscription snapshot, query event, summary event, and unsubscribe behavior:

```python
def test_store_snapshots_records_and_streams_later_queries() -> None:
    store = LiveResultStore({"routes": ["lexical", "planned"]})
    store.publish({"key": "1:1", "query": "first", "routes": {}})

    subscriber, snapshot = store.subscribe()
    assert snapshot["records"] == [{"key": "1:1", "query": "first", "routes": {}}]

    store.publish({"key": "1:2", "query": "second", "routes": {}})
    assert subscriber.get_nowait() == (
        "query",
        {"key": "1:2", "query": "second", "routes": {}},
    )
```

Also mutate the original input and returned snapshot after publication and assert the store remains unchanged. Assert `finish()` sends one `summary` event, `close()` sends one close event, and a removed subscriber receives nothing further.

- [ ] **Step 2: Run Task 1 tests and verify RED**

Run:

```bash
conda run -n codesearch pytest -q tests/unit/evaluation/test_live_results.py
```

Expected: collection fails because `evaluation.live_results` does not exist.

- [ ] **Step 3: Implement the minimal thread-safe store**

Use `threading.Lock`, `queue.Queue`, and `copy.deepcopy`. Keep private state limited to `meta`, ordered `records`, optional `summary`, and subscriber queues. In `subscribe()`, add the queue and create the snapshot inside the same `with self._lock:` block. In `publish()` and `finish()`, copy subscriber references while holding the lock and enqueue after releasing it.

- [ ] **Step 4: Write failing page-contract tests**

Assert the returned page contains:

```python
page = viewer_page()
assert "new EventSource('/events')" in page
assert "matched" in page and "missed" in page and "extra" in page
assert "file_precision" in page and "function_recall" in page
assert ".textContent" in page
assert ".innerHTML" not in page
```

Add a JavaScript-source assertion that route gold status reads `metrics.matched_files` and `metrics.matched_functions`, while extra hit status is derived only for presentation and never changes metrics.

- [ ] **Step 5: Run the page test and verify RED**

Run the focused file again. Expected: store tests pass and page-contract tests fail because `viewer_page()` is absent or incomplete.

- [ ] **Step 6: Implement the single-page viewer**

Return a static HTML document with these DOM builders:

```javascript
function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = String(text);
  return node;
}
```

Implement `renderRecord(record)` to append one card, `renderRoute(search, routeName, routeResult)` to append metrics/gold/hits, and `applySnapshot(snapshot)` to reset progress metadata and render every accumulated record. For each route, flatten gold answers into file rows and `(file, function)` rows. Use `routeResult.metrics.matched_files` and `.matched_functions` for green/red gold status. Mark a hit green when its file or exact normalized `(file, name)` appears in those matched collections; otherwise mark it gray. Deduplicate cards by `record.key` in a `Set`.

- [ ] **Step 7: Verify Task 1 GREEN**

Run the focused test, `ruff check` on the two files, and `ruff format --check` on the two files. Expected: all pass.

- [ ] **Step 8: Preserve the checkpoint without committing user files**

Run `git diff --check` and record the Task 1 RED/GREEN commands. Do not stage or commit: the following integration tasks need the new module together with user-owned untracked evaluation files.

---

### Task 2: Loopback HTTP and SSE facade

**Files:**
- Modify: `evaluation/live_results.py`
- Modify: `tests/unit/evaluation/test_live_results.py`

**Interfaces:**
- Consumes: `LiveResultStore` and `viewer_page()` from Task 1.
- Produces: `LiveEvaluationViewer.start(*, port: int = 8765, meta: Mapping[str, object] | None = None) -> LiveEvaluationViewer`.
- Produces: `.url: str`, `.publish(record)`, `.finish(summary)`, and `.close()`.
- HTTP contract: `GET /`, `GET /api/snapshot`, and `GET /events`; every other path returns 404.

- [ ] **Step 1: Write failing HTTP tests**

Start with port `0`, then use `urllib.request.urlopen(viewer.url)` and `/api/snapshot` to assert:

```python
viewer = LiveEvaluationViewer.start(port=0, meta={"routes": ["lexical"]})
try:
    assert viewer.url.startswith("http://127.0.0.1:")
    assert response.headers.get_content_type() == "text/html"
    assert snapshot["meta"] == {"routes": ["lexical"]}
finally:
    viewer.close()
```

Add a raw `http.client.HTTPConnection` SSE test: connect to `/events`, assert `text/event-stream`, read the initial `event: snapshot`, publish one record, then read `event: query`. Use bounded socket timeouts so a regression fails rather than hanging.

- [ ] **Step 2: Run Task 2 tests and verify RED**

Expected: failures because `LiveEvaluationViewer` and the HTTP routes do not exist.

- [ ] **Step 3: Implement the server facade**

Create a handler factory closed over the store and page. Required response behavior:

```python
if self.path == "/":
    send_html(viewer_page())
elif self.path == "/api/snapshot":
    send_json(store.snapshot())
elif self.path == "/events":
    stream_events(store)
else:
    self.send_error(HTTPStatus.NOT_FOUND)
```

For SSE, call `subscriber, snapshot = store.subscribe()`, write the snapshot event first, then block on the subscriber queue with a finite timeout and send `: keepalive\n\n` comments between events. Catch `BrokenPipeError`, `ConnectionResetError`, and `OSError`, and always unsubscribe in `finally`. Set `ThreadingHTTPServer.daemon_threads = True`; `.close()` wakes subscribers, calls `shutdown()`, `server_close()`, and joins the accept thread with a bounded timeout.

- [ ] **Step 4: Verify HTTP/SSE GREEN and lifecycle safety**

Run Task 2 tests repeatedly twice to catch leaked ports/threads. Assert `close()` is idempotent and publish without subscribers returns immediately.

- [ ] **Step 5: Run focused quality checks**

Run:

```bash
conda run -n codesearch ruff check evaluation/live_results.py tests/unit/evaluation/test_live_results.py
conda run -n codesearch ruff format --check evaluation/live_results.py tests/unit/evaluation/test_live_results.py
git diff --check
```

- [ ] **Step 6: Preserve the checkpoint without committing user files**

Record RED/GREEN results and leave changes unstaged for Task 3.

---

### Task 3: Publish evaluation queries and document usage

**Files:**
- Modify: `scripts/evaluation.py`
- Modify: `tests/unit/test_evaluation_script.py`
- Modify: `evaluation/README.md`
- Modify: `CHANGELOG.md`
- Test: `tests/unit/evaluation/test_live_results.py`

**Interfaces:**
- Consumes: `LiveEvaluationViewer.start()`, `.publish()`, `.finish()`, and `.url`.
- Produces: `_start_viewer(meta: Mapping[str, object]) -> LiveEvaluationViewer | None` using a side-effect-free module import.
- Produces: `_live_record(record, search_result, *, case_number: int, search_number: int) -> dict[str, object]` with stable key `"{case_number}:{search_number}"`.
- Preserves: `main() -> int`, `_evaluate_search()`, `_score()`, report `summary`, and report `cases`.

- [ ] **Step 1: Write failing integration tests around the evaluation loop**

Extend the existing script tests with a fake viewer and a minimal benchmark containing two query entries. Set `module.BENCHMARK`, `module.OUTPUT`, `module.PROJECT_PATHS`, and `module.ROUTES` to temporary/local values; replace `module._llm`, `module._open_project`, and `module._start_viewer` with deterministic fakes. Let the real `main()` read the temporary JSONL, run its loops, score hits, and write the temporary report, then assert:

```python
assert [item["key"] for item in fake_viewer.records] == ["1:1", "1:2"]
assert fake_viewer.records[0]["repo"] == "example/repo"
assert fake_viewer.records[0]["search"]["routes"]["lexical"]["metrics"]["file_recall"] == 1.0
assert fake_viewer.finished_summary == written_report["summary"]
```

Add separate tests that `VIEWER_ENABLED = False` never starts a server, and an `OSError` from `LiveEvaluationViewer.start()` prints a warning but still returns the normal report/exit code.

- [ ] **Step 2: Run integration tests and verify RED**

Run:

```bash
conda run -n codesearch pytest -q tests/unit/test_evaluation_script.py
```

Expected: new tests fail because the viewer configuration and publishing hook do not exist.

- [ ] **Step 3: Add viewer configuration and startup**

Add adjacent to the existing hardcoded evaluation parameters:

```python
VIEWER_ENABLED = True
VIEWER_PORT = 8765
```

Import `LiveEvaluationViewer` at module scope; importing it must not bind a port or start a thread. Implement `_start_viewer()` to catch `OSError`, print `viewer unavailable: {type(exc).__name__}: {exc}`, and return `None`. On success print `viewer: {viewer.url}`. Do not call `webbrowser.open()`.

- [ ] **Step 4: Publish exactly once per completed query**

Enumerate records and searches from 1. Immediately after `searches.append(result)`, publish this shape when a viewer exists:

```python
{
    "key": f"{case_number}:{search_number}",
    "query_id": record.get("query_id"),
    "repo": repo,
    "instance_id": record.get("instance_id"),
    "trajectory_id": record.get("trajectory_id"),
    "search": result,
}
```

Wrap each `.publish()` in a small helper that catches viewer-side exceptions and disables only further live publishing. Do not catch or alter search/scoring exceptions beyond the existing behavior. After constructing the final report, call `.finish(report["summary"])` before writing the unchanged JSON report.

- [ ] **Step 5: Verify integration GREEN and report compatibility**

Run existing and new evaluation tests together:

```bash
conda run -n codesearch pytest -q tests/unit/test_evaluation_script.py tests/unit/evaluation/test_query_viewer.py tests/unit/evaluation/test_live_results.py
```

Assert the prior `_score()` expected dictionary and fallback-not-scored tests remain byte-for-byte unchanged.

- [ ] **Step 6: Document the observer**

Append a short `Live viewer` section to `evaluation/README.md` stating:

- edit `VIEWER_ENABLED` and `VIEWER_PORT` in `scripts/evaluation.py`;
- run the script and manually open the printed loopback URL;
- results appear after each query's routes complete;
- red is missed gold, green is matched gold/hit, gray is an extra hit;
- the page exists only while the evaluation process is running.

Add a `2026-09-10 — 实时评测结果 Viewer` changelog entry above the 2026-09-09 entry. Preserve the existing uncommitted 2026-08-25 entry exactly.

- [ ] **Step 7: Run final verification**

Run:

```bash
conda run -n codesearch ruff check .
conda run -n codesearch ruff format --check evaluation/live_results.py scripts/evaluation.py tests/unit/evaluation/test_live_results.py tests/unit/test_evaluation_script.py
conda run -n codesearch pytest
git diff --check
```

If the repository-wide format check remains blocked only by the pre-existing `processed==10` line in `scripts/mine_trace_queries.py`, report it separately and do not modify that unrelated file.

- [ ] **Step 8: Manual local smoke test without opening a browser**

Start `LiveEvaluationViewer` on port `0` from a short Python command, fetch `/api/snapshot` with `urllib`, publish one fixture record, verify the snapshot contains it, then close the viewer. Do not start a real benchmark, clone repositories, call an LLM, or open a browser.

- [ ] **Step 9: Hand off the uncommitted implementation safely**

Report every changed file and test result. Keep implementation changes unstaged because they overlap user-owned uncommitted evaluation work; ask before creating a commit that would include those files.
