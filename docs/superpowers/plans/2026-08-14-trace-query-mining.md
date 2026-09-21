# Trace Query Mining Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将 Open-SWE-Traces 的单条 Java 记录转换成可直接评审和传给 `Project.search()` 的最终 query JSONL。

**Architecture:** `trace_adapters.open_swe_traces` 只做数据格式归一化；`query_mining` 识别代码搜索 episode，并使用 prefix-only 上下文抽取或生成 query。LLM 通过现有 `LlmConfig` 的 OpenAI-compatible `/chat/completions` 接口调用。CLI 只负责读取 JSONL、注入参数和写出 JSONL。

**Tech Stack:** Python 3.10、dataclasses、标准库 JSONL、现有 `codesense.llm.LlmConfig`、requests、pytest。

## Global Constraints

- 只消费 `language == "java"` 的 Open-SWE-Traces 记录。
- 不读取 reference patch、model patch、当前 tool output 或 anchor 之后的事件作为 query 输入。
- 没有代码搜索 episode 时不走 problem statement-only 兜底。
- LLM key 只从 `CODESENSE_API_KEY` 或未跟踪的 `config.yml` 读取。
- `scripts/` 只做 CLI 编排，不承载 episode 或 prompt 业务逻辑。
- 保留原始 trace provenance，query 输出可独立 JSON 序列化。

---

### Task 1: Trace and query data models

**Files:**
- Create: `evaluation/__init__.py`
- Create: `evaluation/models.py`
- Create: `tests/unit/evaluation/__init__.py`
- Create: `tests/unit/evaluation/test_models.py`

**Interfaces:**
- `TraceEvent(index: int, role: str, text: str, tool_name: str | None, tool_input: str | None, tool_output: str | None)`
- `TraceCase(repo: str, language: str, instance_id: str, trajectory_id: str, issue_statement: str, base_commit: str | None, events: tuple[TraceEvent, ...], raw: Mapping[str, object])`
- `CodeLocation(file: str, functions: tuple[str, ...])`
- `SearchQuery(event_indices: tuple[int, ...], query: str, answers: tuple[CodeLocation, ...])`
- `PreparedQuery(query_id: str, repo: str, instance_id: str, trajectory_id: str, searches: tuple[SearchQuery, ...], final_answer: tuple[CodeLocation, ...], strategy: str, source_events: tuple[TraceEvent, ...], provenance: Mapping[str, object])`
- Each model provides `to_dict()` with only JSON-compatible values.

- [ ] **Step 1: Write failing serialization tests**

  Assert that a `TraceEvent` and a `PreparedQuery` round-trip through `to_dict()` without losing event indexes, strategy, raw action, and source provenance.

- [ ] **Step 2: Run tests and confirm the expected import failure**

  Run: `pytest tests/unit/evaluation/test_models.py -q`

  Expected: FAIL because `evaluation.models` does not exist.

- [ ] **Step 3: Implement the dataclasses and serializers**

  Use frozen, slotted dataclasses. Serialize event tuples to lists and mappings to plain dictionaries; do not serialize arbitrary Python objects.

- [ ] **Step 4: Run the focused tests**

  Run: `pytest tests/unit/evaluation/test_models.py -q`

  Expected: PASS.

### Task 2: Normalize Open-SWE records

**Files:**
- Create: `evaluation/trace_adapters/__init__.py`
- Create: `evaluation/trace_adapters/open_swe_traces.py`
- Create: `tests/unit/evaluation/test_open_swe_traces.py`

**Interfaces:**
- `normalize_record(record: Mapping[str, object]) -> TraceCase`
- `iter_jsonl(path: Path) -> Iterator[TraceCase]`
- Only records with the strict integer value `resolved == 1` are eligible; batch iteration
  silently skips all other resolution states.

- [ ] **Step 1: Write failing adapter tests**

  Use a small record containing `repo`, `language`, `instance_id`, `trajectory_id`, a first user message, an assistant search action, a tool output, and metadata. Assert that the normalized case preserves Java identity, extracts the issue statement, assigns monotonically increasing event indexes, and stores tool output separately from tool input.

- [ ] **Step 2: Add rejection tests**

  Assert that a non-Java record raises `ValueError`, malformed/missing trajectory raises `ValueError`, and a missing `repo` or `trajectory_id` raises `ValueError`.

- [ ] **Step 3: Run tests and confirm failure**

  Run: `pytest tests/unit/evaluation/test_open_swe_traces.py -q`

  Expected: FAIL because the adapter module does not exist.

- [ ] **Step 4: Implement the minimal normalization**

  Accept message content as either a string or a list of `{type, text|content, ...}` blocks. Extract tool call name and arguments from common `function`/`tool_call` shapes, but retain the complete original record in `TraceCase.raw`. Do not parse patches or produce gold in this subtask.

- [ ] **Step 5: Run adapter tests**

  Run: `pytest tests/unit/evaluation/test_open_swe_traces.py -q`

  Expected: PASS.

### Task 3: Identify search events and build local-window prompts

**Files:**
- Create: `evaluation/query_mining.py`
- Create: `tests/unit/evaluation/test_query_mining.py`

**Interfaces:**
- `is_search_event(event: TraceEvent) -> bool`
- `find_episodes(case: TraceCase) -> tuple[tuple[TraceEvent, ...], ...]`
- `build_prompt(case: TraceCase, *, prompt_version: str) -> str`
- `mine_queries(case: TraceCase, generator: QueryGenerator, *, prompt_version: str = "trace-query-v1") -> tuple[PreparedQuery, ...]`
- `QueryGenerator` is a protocol with `generate(prompt: str) -> str | None`.

- [ ] **Step 1: Write failing search-event tests**

  Assert that repeated `rg`/`grep` actions are detected, each search event contributes its adjacent tool/assistant events, system/user events are excluded, and a trace with no search action yields an empty tuple.

- [ ] **Step 2: Write the trace-wide prompt test**

  Build one prompt from a trace with multiple search actions. Assert that only the immediately adjacent assistant/tool events and the search actions themselves appear, while distant events, system/user events, and the issue statement do not.

- [ ] **Step 3: Run tests and confirm failure**

  Run: `pytest tests/unit/evaluation/test_query_mining.py -q`

  Expected: FAIL because `evaluation.query_mining` does not exist.

- [ ] **Step 4: Implement deterministic search detection and one-call extraction**

  Recognize `grep`, `rg`, `ripgrep`, `find`, `search`, `search_code`, `code_search`, and symbol/file navigation actions. Pass one local context window per detected search to one LLM call, parse multiple searches with structured code-location answers, and emit one instance-level record containing all searches plus a structured turn-level final answer.

- [ ] **Step 5: Implement the prefix-only prompt**

  Include issue statement, events before the first episode anchor, and the current raw action. Explicitly instruct the model to output one English natural-language code-search query, not a shell command, path, class name copied from the action, patch detail, or future result.

- [ ] **Step 6: Implement extraction and generated query validation**

  Extract only a human-readable query argument that is not shell/path syntax. Otherwise call the injected generator. Accept plain text or `{"query": "..."}` / fenced JSON, reject empty, multiline, shell-command, absolute-path, and `/testbed` outputs. Retry once with a short repair suffix, then skip the episode.

- [ ] **Step 7: Run query mining tests**

  Run: `pytest tests/unit/evaluation/test_query_mining.py -q`

  Expected: PASS.

### Task 4: OpenAI-compatible generator and JSONL debug entry point

**Files:**
- Modify: `evaluation/query_mining.py`
- Create: `scripts/mine_trace_queries.py`
- Create: `tests/unit/evaluation/test_query_generator.py`
- Create: `tests/fixtures/evaluation/open_swe_sample.jsonl`
- Modify: `.gitignore` only if a generated local output path is not already ignored

**Interfaces:**
- `OpenAIQueryGenerator(config: LlmConfig, session: object | None = None)`
- `OpenAIQueryGenerator.generate(prompt: str) -> str | None`
- CLI: configure `INPUT`, `OUTPUT`, `LIMIT`, `DRY_RUN`, and model settings at the top of
  `scripts/mine_trace_queries.py`, then run `python scripts/mine_trace_queries.py`.

- [ ] **Step 1: Write failing HTTP adapter tests**

  Inject a fake session and assert that the generator posts to `/chat/completions`, sends the configured model and prompt, uses temperature `0`, and returns the assistant content. Assert request failure returns `None` without exposing the API key.

- [ ] **Step 2: Run the focused tests and confirm failure**

  Run: `pytest tests/unit/evaluation/test_query_generator.py -q`

  Expected: FAIL because `OpenAIQueryGenerator` does not exist.

- [ ] **Step 3: Implement the generator**

  Follow the existing `codesense.llm.codegen` request pattern with an injectable requests session and exception degradation. Keep it in `evaluation` so the core `codesense` package remains independent of evaluation.

- [ ] **Step 4: Implement the CLI**

  Read one JSON object per non-empty input line, normalize Java records, make at most one LLM
  call per trace, and write one JSON object per extracted search query. Use
  `LlmConfig.load(base_url=..., model=..., timeout=...)`; never accept an API key CLI flag.
  Dry-run emits one `{"type":"prompt", "trace":..., "search_event_indices":..., "prompt":...}`
  record per trace without making LLM calls.

- [ ] **Step 5: Run focused tests and a local dry run**

  Run: `pytest tests/unit/evaluation -q`.

  Run: `python scripts/mine_trace_queries.py --input tests/fixtures/evaluation/open_swe_sample.jsonl --output /tmp/codesense-queries.jsonl --dry-run --limit 1`.

  Expected: tests pass and the output contains normalized episode/query provenance without an API call.

### Task 5: Verification and commit

**Files:**
- Modify none beyond Tasks 1–4.

- [ ] **Step 1: Run formatting and lint for new files**

  Run: `ruff check evaluation scripts/mine_trace_queries.py tests/unit/evaluation` and `ruff format --check evaluation scripts/mine_trace_queries.py tests/unit/evaluation`.

- [ ] **Step 2: Run the complete test suite**

  Run: `pytest`.

- [ ] **Step 3: Inspect generated output**

  Confirm every output query has `query_id`, `strategy`, `anchor_event`, `raw_action`, `source_events`, and prompt/model provenance; confirm no output contains a tool output or later event in the prompt snapshot.

- [ ] **Step 4: Commit only query-mining files**

  ```bash
  git add evaluation tests/unit/evaluation scripts/mine_trace_queries.py docs/superpowers/plans/2026-08-14-trace-query-mining.md
  git commit -m "feat(evaluation): mine queries from Open-SWE traces"
  ```
