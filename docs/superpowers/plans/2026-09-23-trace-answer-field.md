# Trace Answer Field Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a deterministic, per-trace `trace_answer` field to every mined query without changing any current evaluation score.

**Architecture:** Extend `PreparedQuery` with another `CodeLocation` tuple, extract that tuple once from the trace's final `finish` message while constraining files to `TraceCase.answer`, and reuse it for every query mined from the same trace. `TraceCase.answer` is already produced from existing production Java files in the reference patch, so it is the allowlist that excludes added, deleted, test, example, generated, and build-output files.

**Tech Stack:** Python 3.11, frozen slot dataclasses, deterministic regex/path matching, pytest, Ruff.

**Spec:** `docs/superpowers/specs/2026-09-23-trace-answer-field-design.md`

## Global Constraints

- Add `trace_answer` as a separate field; do not merge it into `answer` or `candidate_answers`.
- Do not change `scripts/evaluation.py` or implement reward, penalty, aggregation, precision, or recall behavior.
- Extract deterministically; do not add an LLM call or expose final-answer information to the query-generation prompt.
- Every query mined from one trace receives the same immutable `trace_answer` tuple.
- Only files already present in `TraceCase.answer` are eligible; patch-added files never enter `trace_answer`.
- Keep uncertain functions empty instead of guessing.
- Preserve unrelated working-tree changes.
- Run commands inside the `codesearch` conda environment.

## File Structure

- Modify `evaluation/models.py`: own the `PreparedQuery.trace_answer` schema and serialization.
- Modify `evaluation/trace_search.py`: own deterministic extraction from the final `finish` message.
- Modify `evaluation/query_mining.py`: extract once per trace and propagate to all mined rows.
- Modify `tests/unit/evaluation/test_models.py`: pin serialization and default behavior.
- Modify `tests/unit/evaluation/test_trace_search.py`: pin matching, association, ambiguity, and missing-final behavior.
- Modify `tests/unit/evaluation/test_query_mining.py`: pin one-result-to-many-query propagation.
- Modify `tests/unit/evaluation/test_semantic_trace_pipeline.py`: prove patch-added files are excluded end to end.

## Review Focus

- A trace without a valid `finish` call yields an empty tuple instead of failing mining; Task 2 tests this.
- A basename shared by two eligible files is ambiguous and skipped; Task 2 tests this.
- A final answer may mention both an existing file and a patch-added file; only the existing file survives; Task 3 tests this.
- A function name shared by multiple matched files is not attached unless class-qualified; Task 2 tests this.
- Malformed or non-string `finish.message` data behaves like a missing final answer; Task 2 tests this.

---

### Task 1: Add the serialized `trace_answer` schema

**Files:**
- Modify: `evaluation/models.py:106-143`
- Test: `tests/unit/evaluation/test_models.py:70-131`

**Interfaces:**
- Consumes: existing `CodeLocation` and `PreparedQuery.to_dict()`.
- Produces: `PreparedQuery.trace_answer: tuple[CodeLocation, ...]` and JSON key `trace_answer`.

- [ ] **Step 1: Write the failing serialization tests**

Add this keyword to the existing `PreparedQuery(...)` constructor in
`test_prepared_query_serializes_source_events_and_provenance`:

```python
trace_answer=(CodeLocation("src/main/java/Navigation.java", ("afterCursor",)),),
```

After `payload = query.to_dict()`, add:

```python
assert payload["trace_answer"] == [
    {"file": "src/main/java/Navigation.java", "functions": ["afterCursor"]}
]
```

Add the default-value case:

```python
def test_prepared_query_defaults_trace_answer_to_empty() -> None:
    query = PreparedQuery(
        query_id="trace-1:2",
        repo="owner/repo",
        instance_id="issue-1",
        trajectory_id="trace-1",
        issue_statement="State can remain stale.",
        query="Find transition logic that can retain stale state.",
        answer=(),
    )

    assert query.trace_answer == ()
    assert query.to_dict()["trace_answer"] == []
```

- [ ] **Step 2: Run the model tests and verify failure**

```bash
conda run -n codesearch pytest tests/unit/evaluation/test_models.py -q
```

Expected: failure because `PreparedQuery` does not accept or serialize `trace_answer`.

- [ ] **Step 3: Add the minimal model field and serializer entry**

```python
answer: tuple[CodeLocation, ...]
trace_answer: tuple[CodeLocation, ...] = ()
candidate_answers: tuple[CodeLocation, ...] = ()
```

```python
"answer": [location.to_dict() for location in self.answer],
"trace_answer": [location.to_dict() for location in self.trace_answer],
"candidate_answers": [location.to_dict() for location in self.candidate_answers],
```

- [ ] **Step 4: Run the model tests and verify success**

```bash
conda run -n codesearch pytest tests/unit/evaluation/test_models.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit the schema change**

```bash
git add evaluation/models.py tests/unit/evaluation/test_models.py
git commit -m "feat: add trace answer query field"
```

### Task 2: Extract a conservative trace answer

**Files:**
- Modify: `evaluation/trace_search.py:15-26,298-355`
- Test: `tests/unit/evaluation/test_trace_search.py`

**Interfaces:**
- Consumes: `TraceCase.events`, `TraceCase.answer`, `_final_answer()`, `_edited_functions()`, and normalized Java paths.
- Produces: `extract_trace_answer(case: TraceCase) -> tuple[CodeLocation, ...]`.

- [ ] **Step 1: Write failing success-path tests**

Add a helper that builds a `finish` event and extend the existing test-only `_trace_case` helper with an optional `answer` keyword. Then add:

```python
def test_trace_answer_extracts_existing_file_and_explicit_function() -> None:
    case = _trace_case(
        (_finish_event(
            "Updated Navigation.afterCursor() in src/main/java/Navigation.java."
        ),),
        answer=(CodeLocation("src/main/java/Navigation.java", ("afterCursor",)),),
    )

    assert extract_trace_answer(case) == (
        CodeLocation("src/main/java/Navigation.java", ("afterCursor",)),
    )


def test_trace_answer_accepts_only_a_unique_basename() -> None:
    case = _trace_case(
        (_finish_event("The behavior is implemented by Navigation.java."),),
        answer=(CodeLocation("src/main/java/Navigation.java", ()),),
    )

    assert extract_trace_answer(case) == (
        CodeLocation("src/main/java/Navigation.java", ()),
    )
```

- [ ] **Step 2: Write failing conservative-behavior tests**

```python
def test_trace_answer_rejects_ambiguous_basename() -> None:
    case = _trace_case(
        (_finish_event("The behavior is implemented by Navigation.java."),),
        answer=(
            CodeLocation("module-a/src/Navigation.java", ()),
            CodeLocation("module-b/src/Navigation.java", ()),
        ),
    )

    assert extract_trace_answer(case) == ()


def test_trace_answer_ignores_missing_or_malformed_finish_message() -> None:
    missing = _trace_case(())
    malformed = _trace_case(
        (
            TraceEvent(
                1,
                "assistant",
                "",
                tool_calls=(ToolCall("finish-1", "finish", '{"message": 42}'),),
            ),
        )
    )

    assert extract_trace_answer(missing) == ()
    assert extract_trace_answer(malformed) == ()
```

Pin shared-function ambiguity:

```python
def test_trace_answer_requires_qualification_for_shared_function_name() -> None:
    answers = (
        CodeLocation("src/main/java/First.java", ("run",)),
        CodeLocation("src/main/java/Second.java", ("run",)),
    )
    unqualified = _trace_case(
        (_finish_event("First.java and Second.java use run()."),), answer=answers
    )
    qualified = _trace_case(
        (_finish_event("First.java uses First.run(); Second.java is involved."),),
        answer=answers,
    )

    assert extract_trace_answer(unqualified) == (
        CodeLocation("src/main/java/First.java", ()),
        CodeLocation("src/main/java/Second.java", ()),
    )
    assert extract_trace_answer(qualified)[0].functions == ("run",)
    assert extract_trace_answer(qualified)[1].functions == ()
```

- [ ] **Step 3: Run extraction tests and verify failure**

```bash
conda run -n codesearch pytest tests/unit/evaluation/test_trace_search.py -q
```

Expected: failure because `extract_trace_answer` does not exist.

- [ ] **Step 4: Implement file matching and function association**

Add `defaultdict` to the collections import, export `extract_trace_answer`, and implement:

```python
def extract_trace_answer(case: TraceCase) -> tuple[CodeLocation, ...]:
    """Extract final locations while allowing only existing reference-gold files."""
    final = _final_answer(case.events)
    if final is None or not case.answer:
        return ()
    final_event, text = final
    matched = _mentioned_trace_locations(text, case.answer)
    if not matched:
        return ()

    candidates = {
        location.file: tuple(
            dict.fromkeys(
                (
                    *location.functions,
                    *_edited_functions(case.events, location.file, -1, final_event.index),
                )
            )
        )
        for location in matched
    }
    owners: dict[str, set[str]] = defaultdict(set)
    for file, functions in candidates.items():
        for function in functions:
            owners[function].add(file)

    return tuple(
        CodeLocation(
            location.file,
            tuple(
                function
                for function in candidates[location.file]
                if _mentions_trace_function(
                    text, location.file, function, owners[function]
                )
            ),
        )
        for location in matched
    )
```

Use exact normalized paths first, and permit a basename only when it is unique among eligible locations:

```python
def _mentioned_trace_locations(
    text: str, locations: Sequence[CodeLocation]
) -> tuple[CodeLocation, ...]:
    basenames: dict[str, list[CodeLocation]] = defaultdict(list)
    for location in locations:
        basenames[PurePosixPath(location.file).name].append(location)

    found: list[CodeLocation] = []
    for location in locations:
        basename = PurePosixPath(location.file).name
        basename_match = re.search(
            rf"(?<![\w$]){re.escape(basename)}(?![\w$])", text
        )
        if _mentions_exact_file(text, location.file) or (
            len(basenames[basename]) == 1 and basename_match
        ):
            found.append(location)
    return tuple(found)
```

Associate a function when it belongs to only one matched file, or when a shared name is class-qualified:

```python
def _mentions_trace_function(
    text: str, file: str, function: str, owners: set[str]
) -> bool:
    if not re.search(rf"(?<![\w$]){re.escape(function)}(?![\w$])", text):
        return False
    if len(owners) == 1:
        return True
    class_name = PurePosixPath(file).stem
    return bool(
        re.search(
            rf"(?<![\w$]){re.escape(class_name)}\s*\.\s*"
            rf"{re.escape(function)}(?![\w$])",
            text,
        )
    )
```

The extractor iterates only over eligible files and known functions; it does not rescan the trace for each query.

- [ ] **Step 5: Run extraction tests and verify success**

```bash
conda run -n codesearch pytest tests/unit/evaluation/test_trace_search.py -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit the extractor**

```bash
git add evaluation/trace_search.py tests/unit/evaluation/test_trace_search.py
git commit -m "feat: extract trace final answers"
```

### Task 3: Propagate one trace answer to every query

**Files:**
- Modify: `evaluation/query_mining.py:11-16,135-183`
- Test: `tests/unit/evaluation/test_query_mining.py:133-163`
- Test: `tests/unit/evaluation/test_semantic_trace_pipeline.py`

**Interfaces:**
- Consumes: `extract_trace_answer(case)` from Task 2 and `PreparedQuery.trace_answer` from Task 1.
- Produces: all successful queries from one `mine_queries()` call carry the same tuple.

- [ ] **Step 1: Write the failing multi-query propagation test**

Create one two-search case, add an existing answer and a final event, then assert both rows share the value:

```python
original = _case(two_searches=True)
case = replace(
    original,
    answer=(CodeLocation("src/main/java/Pool.java", ("select",)),),
    events=(
        *original.events,
        TraceEvent(
            10,
            "assistant",
            "",
            tool_calls=(
                ToolCall(
                    "finish-1",
                    "finish",
                    '{"message":"Updated Pool.select() in src/main/java/Pool.java."}',
                ),
            ),
        ),
    ),
)
batch = mine_queries(case, _Generator(response, response))

expected = (CodeLocation("src/main/java/Pool.java", ("select",)),)
assert [query.trace_answer for query in batch.queries] == [expected, expected]
```

Import `replace`, `CodeLocation`, and `ToolCall` in the test file.

- [ ] **Step 2: Write the failing end-to-end added-file exclusion test**

In `test_semantic_trace_pipeline.py`, construct a resolved record whose reference patch modifies `Existing.java` and adds `Added.java` with `new file mode`. Its final `finish.message` names both. Include one eligible search episode so mining emits a row, then assert:

```python
assert payload["trace_answer"] == [
    {"file": "src/main/java/Existing.java", "functions": ["update"]}
]
assert all(
    item["file"] != "src/main/java/Added.java"
    for item in payload["trace_answer"]
)
assert "Added.java" not in batch.queries[0].provenance["prompt"]
```

The existing-file hunk declares `update`; the new-file hunk declares `create`. This proves the adapter's existing-file allowlist excludes added files without teaching the extractor the raw patch schema.

- [ ] **Step 3: Run mining tests and verify failure**

```bash
conda run -n codesearch pytest \
  tests/unit/evaluation/test_query_mining.py \
  tests/unit/evaluation/test_semantic_trace_pipeline.py -q
```

Expected: failures because `mine_queries()` does not populate `trace_answer`.

- [ ] **Step 4: Extract once and pass the value to every query**

Add `extract_trace_answer` to the existing import block:

```python
from evaluation.trace_search import (
    SupervisedEpisode,
    extract_trace_answer,
    is_search_event,
    supervise_search_episodes,
)
```

Immediately after the existing supervision call, add:

```python
supervision = supervise_search_episodes(case)
trace_answer = extract_trace_answer(case)
```

In the existing `PreparedQuery(...)` constructor, insert this keyword directly after
`answer=supervised.answer`:

```python
trace_answer=trace_answer,
```

Do not add `trace_answer` to the prompt, provenance, score calculation, or skip-reason logic.

- [ ] **Step 5: Run focused evaluation tests**

```bash
conda run -n codesearch pytest tests/unit/evaluation -q
```

Expected: all evaluation tests pass.

- [ ] **Step 6: Confirm the evaluator remains untouched**

```bash
git diff -- scripts/evaluation.py
```

Expected: no output.

- [ ] **Step 7: Commit propagation and integration coverage**

```bash
git add evaluation/query_mining.py \
  tests/unit/evaluation/test_query_mining.py \
  tests/unit/evaluation/test_semantic_trace_pipeline.py
git commit -m "feat: attach trace answers to mined queries"
```

### Task 4: Run repository-wide verification

**Files:**
- Verify only; no planned code changes.

**Interfaces:**
- Consumes: completed Tasks 1-3.
- Produces: lint, formatting, and test evidence for the finished feature.

- [ ] **Step 1: Run Ruff lint**

```bash
conda run -n codesearch ruff check .
```

Expected: exit code 0.

- [ ] **Step 2: Run Ruff format verification**

```bash
conda run -n codesearch ruff format --check .
```

Expected: exit code 0.

- [ ] **Step 3: Run the full test suite**

```bash
conda run -n codesearch pytest
```

Expected: exit code 0. If unrelated pre-existing working-tree changes fail a test, record the exact failing test separately and still report the focused evaluation-suite result.

- [ ] **Step 4: Review final scope**

```bash
git status --short
git log --oneline -4
```

Expected: feature commits contain only the seven implementation/test files listed above; unrelated dirty files remain uncommitted.
