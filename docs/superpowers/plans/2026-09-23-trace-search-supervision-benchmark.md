# Trace Search Supervision Benchmark Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace patch-derived benchmark gold with per-search-episode supervision built from deterministic search results and later trace usage, while generating one semantic query for every useful episode.

**Architecture:** Add one focused `evaluation/trace_search.py` module for deterministic episode/result/evidence analysis and keep `evaluation/query_mining.py` responsible for LLM prompting and validation. Extend the existing flat JSONL model with mutually exclusive gold and candidate locations, then adapt the hard-coded mining entry point, evaluator, and two existing viewers without changing CodeSense search behavior.

**Tech Stack:** Python 3.11 dataclasses, standard-library `json`/`re`/`shlex`, pytest, Ruff, existing OpenAI-compatible query generator, existing dependency-free HTML/JavaScript viewers.

**Spec:** `docs/superpowers/specs/2026-09-23-trace-search-supervision-benchmark-design.md`

## Global Constraints

- Only Open-SWE-Traces records with `resolved == 1` and `language == "java"` are eligible.
- Test, example, generated, and build Java files never enter `answer` or `candidate_answers`.
- LLM prompts may contain the full issue, the nearest preceding assistant reasoning, and the current search action, but never the current result, future trace, final answer, reference patch, gold, or candidates.
- Exact class, method, and technical terms are allowed as anchors; a query still needs at least one behavior, state, failure, performance, responsibility, or side-effect constraint.
- `answer` and `candidate_answers` are mutually exclusive at file level; once a file is gold, unproven functions from that file are omitted rather than repeated as candidates.
- Candidate answers are weak labels, not negatives; the first version does not award partial candidate credit.
- Only the first semantic gate is implemented; no exact-name, BM25, graph, or embedding baseline gate is added.
- Mining and evaluation scripts retain manually edited module constants rather than adding CLI configuration.
- CodeSense index construction and `Project.search()` execution are not changed.
- Preserve unrelated dirty-worktree files; do not stage `CHANGELOG.md`, `evaluation/README.md`, `evaluation/evaluation_viewer.py`, or unrelated tests.

## Review Focus

- An assistant event with multiple tool calls must preserve every call; when result IDs are absent and assignment is ambiguous, skip with `ambiguous_tool_result` instead of binding the first call.
- Absolute Open-SWE workspace paths and `path.java:line:text` output must normalize to stable repository-relative POSIX paths without accepting ambiguous basenames.
- Tool output that merely repeats a filename must not count as later use; only a classified later action or qualified assistant reference creates `UsageEvidence`.
- When repeated searches return the same file, later use belongs to the most recent producer so one action does not create duplicate gold.
- A query containing an exact method anchor plus a behavioral constraint must pass, while pure reference/call/implementation/name lookup and result file paths must fail.

---

### Task 1: Preserve Tool Calls and Serialize Supervision Models

**Files:**
- Modify: `evaluation/models.py:1-97`
- Modify: `evaluation/trace_adapters/open_swe_traces.py:24-68, 151-199`
- Test: `tests/unit/evaluation/test_models.py`
- Test: `tests/unit/evaluation/test_open_swe_traces.py`

**Interfaces:**
- Consumes: raw Open-SWE `tool_calls[*].id`, `function.name`, `function.arguments`, and tool-result `tool_call_id`.
- Produces: `ToolCall(id: str | None, name: str, arguments: str | None)`, extended `TraceEvent.tool_calls`, `TraceEvent.tool_call_id`, `UsageEvidence`, and extended `PreparedQuery` fields used by all later tasks.

- [ ] **Step 1: Add failing model serialization tests**

```python
def test_trace_event_serializes_multiple_tool_calls_and_result_identity() -> None:
    event = TraceEvent(
        index=2,
        role="assistant",
        text="inspect both paths",
        tool_calls=(
            ToolCall("call-a", "rg", '{"pattern":"pool"}'),
            ToolCall("call-b", "find", '{"path":"src/main/java"}'),
        ),
    )
    payload = event.to_dict()
    assert [call["id"] for call in payload["tool_calls"]] == ["call-a", "call-b"]


def test_prepared_query_serializes_gold_candidates_and_evidence() -> None:
    query = PreparedQuery(
        query_id="trace-1:2",
        repo="acme/project",
        instance_id="issue-1",
        trajectory_id="trace-1",
        issue_statement="Pool reuse order is inconsistent.",
        query="Find where pool ordering changes reusable connection selection.",
        answer=(CodeLocation("src/main/java/Pool.java", ("select",)),),
        candidate_answers=(CodeLocation("src/main/java/PoolConfig.java", ()),),
        usage_evidence=(
            UsageEvidence("src/main/java/Pool.java", ("select",), 8, "opened"),
        ),
        anchor_terms=("isPoolLifo",),
        semantic_constraints=("changes reusable connection selection",),
        source_event_indices=(1, 2),
        result_event_indices=(3,),
        strategy="trace-search-generated",
        source_events=(),
        provenance={"prompt_version": "trace-search-v2"},
    )
    payload = query.to_dict()
    assert payload["candidate_answers"] == [
        {"file": "src/main/java/PoolConfig.java", "functions": []}
    ]
    assert payload["usage_evidence"][0]["kind"] == "opened"
    assert payload["result_event_indices"] == [3]
```

- [ ] **Step 2: Run the model tests and verify the new names are missing**

Run: `conda run -n codesearch pytest tests/unit/evaluation/test_models.py -q`

Expected: FAIL because `ToolCall`, `UsageEvidence`, and the new `PreparedQuery` fields do not exist.

- [ ] **Step 3: Implement the minimal serializable models**

```python
@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str | None
    name: str
    arguments: str | None


@dataclass(frozen=True, slots=True)
class UsageEvidence:
    file: str
    functions: tuple[str, ...]
    event_index: int
    kind: str


@dataclass(frozen=True, slots=True)
class PreparedQuery:
    query_id: str
    repo: str
    instance_id: str
    trajectory_id: str
    issue_statement: str
    query: str
    answer: tuple[CodeLocation, ...]
    candidate_answers: tuple[CodeLocation, ...]
    usage_evidence: tuple[UsageEvidence, ...]
    anchor_terms: tuple[str, ...]
    semantic_constraints: tuple[str, ...]
    source_event_indices: tuple[int, ...]
    result_event_indices: tuple[int, ...]
    strategy: str
    source_events: tuple[TraceEvent, ...]
    provenance: Mapping[str, object]
```

Give the existing `TraceEvent.tool_name`, `tool_input`, and `tool_output` fields `None` defaults, then extend
`TraceEvent` with `tool_calls: tuple[ToolCall, ...] = ()` and `tool_call_id: str | None = None`.
Retain `tool_name` and `tool_input` for existing single-call
consumers and viewer compatibility; populate them from the only call or first call, while all new episode
logic reads `tool_calls`.

- [ ] **Step 4: Add failing adapter tests for multiple calls and result IDs**

```python
def test_normalize_record_preserves_every_tool_call_and_result_id() -> None:
    record = _record()
    record["trajectory"][1]["tool_calls"] = [
        {"id": "a", "function": {"name": "rg", "arguments": '{"pattern":"pool"}'}},
        {"id": "b", "function": {"name": "find", "arguments": '{"path":"src"}'}},
    ]
    record["trajectory"][2]["tool_call_id"] = "a"
    case = normalize_record(record)
    assert [call.id for call in case.events[1].tool_calls] == ["a", "b"]
    assert case.events[2].tool_call_id == "a"
```

- [ ] **Step 5: Run the adapter test and verify it fails**

Run: `conda run -n codesearch pytest tests/unit/evaluation/test_open_swe_traces.py::test_normalize_record_preserves_every_tool_call_and_result_id -q`

Expected: FAIL because `_tool_call()` currently returns only the first call and drops IDs.

- [ ] **Step 6: Replace first-call extraction with `_tool_calls()`**

```python
def _tool_calls(message: Mapping[str, object], content: object) -> tuple[ToolCall, ...]:
    calls = list(message.get("tool_calls") or [])
    if isinstance(content, list):
        calls.extend(block for block in content if isinstance(block, Mapping))
    found: list[ToolCall] = []
    for call in calls:
        if not isinstance(call, Mapping):
            continue
        function = call.get("function")
        data = function if isinstance(function, Mapping) else call
        name = _as_optional_text(data.get("name"))
        if name:
            found.append(
                ToolCall(
                    _as_optional_text(call.get("id")),
                    name,
                    _json_text(data.get("arguments") or data.get("input")),
                )
            )
    return tuple(found)
```

Set assistant `tool_calls`, compatibility `tool_name/tool_input`, and tool-result `tool_call_id` in `_event()`.
Do not make missing reference patches invalidate adapter normalization; keep legacy `TraceCase.answer` and
`gold_error` only as auxiliary metadata.

- [ ] **Step 7: Run focused tests**

Run: `conda run -n codesearch pytest tests/unit/evaluation/test_models.py tests/unit/evaluation/test_open_swe_traces.py -q`

Expected: PASS.

- [ ] **Step 8: Commit the model and adapter contract**

```bash
git add evaluation/models.py evaluation/trace_adapters/open_swe_traces.py tests/unit/evaluation/test_models.py tests/unit/evaluation/test_open_swe_traces.py
git commit -m "refactor: preserve trace tool-call supervision"
```

---

### Task 2: Discover Search Episodes and Parse Result Candidates

**Files:**
- Create: `evaluation/trace_search.py`
- Create: `tests/unit/evaluation/test_trace_search.py`
- Modify: `evaluation/query_mining.py:1-89` only to import/re-export `is_search_event` if compatibility requires it

**Interfaces:**
- Consumes: `TraceCase.events`, `TraceEvent.tool_calls`, `TraceEvent.tool_call_id`, and compatibility single-call fields.
- Produces: `SearchEpisode`, `CandidateLocation`, `find_search_episodes(events)`, `parse_episode_candidates(episode, repo)`, and `normalize_action(action)`.

- [ ] **Step 1: Write failing episode association tests**

```python
def test_find_search_episodes_uses_call_id_before_position() -> None:
    events = (
        TraceEvent(index=0, role="user", text="Fix pool order."),
        TraceEvent(index=1, role="assistant", text="I will inspect both pool paths."),
        TraceEvent(
            index=2,
            role="assistant",
            text="",
            tool_calls=(
                ToolCall("search-a", "rg", "rg fifo src"),
                ToolCall("search-b", "find", "find src -name '*.java'"),
            ),
        ),
        TraceEvent(
            index=3, role="tool", text="", tool_output="src/A.java", tool_call_id="search-a"
        ),
        TraceEvent(
            index=4, role="tool", text="", tool_output="src/B.java", tool_call_id="search-b"
        ),
    )
    found = find_search_episodes(events)
    assert [
        (episode.tool_call.id, [event.index for event in episode.result_events])
        for episode in found.episodes
    ] == [
        ("search-a", [3]),
        ("search-b", [4]),
    ]


def test_find_search_episodes_rejects_ambiguous_unidentified_multiple_calls() -> None:
    events = (
        TraceEvent(
            index=0,
            role="assistant",
            text="",
            tool_calls=(
                ToolCall(None, "rg", "rg fifo src"),
                ToolCall(None, "rg", "rg lifo src"),
            ),
        ),
        TraceEvent(index=1, role="tool", text="", tool_output="src/Pool.java"),
    )
    found = find_search_episodes(events)
    assert found.episodes == ()
    assert found.skip_reasons == ("ambiguous_tool_result",)
```

The result object is:

```python
@dataclass(frozen=True, slots=True)
class EpisodeDiscovery:
    episodes: tuple[SearchEpisode, ...]
    skip_reasons: tuple[str, ...]
```

- [ ] **Step 2: Run the episode tests and verify import failure**

Run: `conda run -n codesearch pytest tests/unit/evaluation/test_trace_search.py -q`

Expected: FAIL because `evaluation.trace_search` does not exist.

- [ ] **Step 3: Implement search recognition and deterministic result binding**

```python
@dataclass(frozen=True, slots=True)
class SearchEpisode:
    anchor_event: int
    action_event: TraceEvent
    tool_call: ToolCall
    context_events: tuple[TraceEvent, ...]
    result_events: tuple[TraceEvent, ...]
    raw_action: str
    candidates: tuple[CandidateLocation, ...] = ()


def find_search_episodes(events: Sequence[TraceEvent]) -> EpisodeDiscovery:
    """Bind each recognized search call to its result without executing trace input."""


def normalize_action(action: str) -> str:
    return " ".join(action.split())
```

Reuse the existing recursive JSON/Python-literal command discovery, adding `shlex.split()` only for parsing.
For missing IDs, bind only contiguous tool events before the next assistant/user event. Build prompt context from
the action event plus the nearest preceding non-empty assistant reasoning event; never add tool results.

- [ ] **Step 4: Write failing candidate parsing tests**

```python
def _episode_with_output(output: str) -> SearchEpisode:
    call = ToolCall("search-a", "rg", "rg pool src/main/java")
    action = TraceEvent(index=2, role="assistant", text="", tool_calls=(call,))
    result = TraceEvent(
        index=3,
        role="tool",
        text="",
        tool_output=output,
        tool_call_id="search-a",
    )
    return SearchEpisode(2, action, call, (action,), (result,), call.arguments or "")


def test_parse_candidates_normalizes_workspace_paths_and_declarations() -> None:
    raw = (
        "/workspace/open-feature__java-sdk__1.0/src/main/java/dev/Client.java:42: "
        "public Evaluation getDoubleValue(String key) {"
    )
    episode = _episode_with_output(raw)
    assert parse_episode_candidates(episode, "open-feature/java-sdk") == (
        CandidateLocation("src/main/java/dev/Client.java", ("getDoubleValue",), 42, raw),
    )


def test_parse_candidates_excludes_tests_and_does_not_infer_call_names() -> None:
    raw = (
        "src/test/java/dev/ClientTest.java\n"
        "src/main/java/dev/Client.java:80: provider.getDoubleValue(key);"
    )
    episode = _episode_with_output(raw)
    assert parse_episode_candidates(episode, "open-feature/java-sdk") == (
        CandidateLocation(
            "src/main/java/dev/Client.java",
            (),
            80,
            "src/main/java/dev/Client.java:80: provider.getDoubleValue(key);",
        ),
    )
```

- [ ] **Step 5: Implement candidate parsing and merging**

```python
@dataclass(frozen=True, slots=True)
class CandidateLocation:
    file: str
    functions: tuple[str, ...]
    line: int | None
    raw_result: str


def parse_episode_candidates(
    episode: SearchEpisode, repo: str
) -> tuple[CandidateLocation, ...]:
    """Parse production Java locations from this episode's result events."""
```

Recognize `path.java`, `path.java:line`, and `path.java:line:text`. Strip `/workspace/<checkout>/`, `./`,
`a/`, and `b/`; reject test/example/generated/build paths. Merge repeated files while preserving first-seen order
and unique explicit declaration functions. A call expression or search keyword does not create a function.

- [ ] **Step 6: Run focused episode and parser tests**

Run: `conda run -n codesearch pytest tests/unit/evaluation/test_trace_search.py -q`

Expected: PASS.

- [ ] **Step 7: Commit deterministic episode parsing**

```bash
git add evaluation/trace_search.py evaluation/query_mining.py tests/unit/evaluation/test_trace_search.py
git commit -m "feat: parse trace search episodes"
```

---

### Task 3: Derive Usage Evidence, Gold, Candidates, and Deduplication

**Files:**
- Modify: `evaluation/trace_search.py`
- Modify: `tests/unit/evaluation/test_trace_search.py`

**Interfaces:**
- Consumes: parsed `SearchEpisode` values and the full ordered trace.
- Produces: `SupervisedEpisode`, `supervise_search_episodes(case)`, stable skip reasons, mutually exclusive gold/candidate locations, and evidence.

- [ ] **Step 1: Write failing action-classification and evidence tests**

```python
def _trace_case(events: tuple[TraceEvent, ...]) -> TraceCase:
    return TraceCase(
        repo="acme/project",
        language="java",
        instance_id="issue-1",
        trajectory_id="trace-1",
        issue_statement="Pool reuse order is inconsistent.",
        base_commit=None,
        events=events,
        raw={},
    )


def test_open_edit_and_followup_search_create_usage_evidence() -> None:
    events = (
        TraceEvent(0, "assistant", "", "rg", "rg pool src/main/java", None),
        TraceEvent(1, "tool", "", "rg", None, "src/main/java/Pool.java"),
        TraceEvent(
            2,
            "assistant",
            "",
            "str_replace_editor",
            '{"command":"view","path":"src/main/java/Pool.java"}',
            None,
        ),
        TraceEvent(3, "tool", "", "str_replace_editor", None, "class Pool {}"),
        TraceEvent(4, "assistant", "", "bash", '{"command":"rg Pool.java notes.txt"}', None),
        TraceEvent(5, "tool", "", "bash", None, "no matches"),
        TraceEvent(
            6,
            "assistant",
            "",
            "str_replace_editor",
            '{"command":"str_replace","path":"src/main/java/Pool.java"}',
            None,
        ),
    )
    batch = supervise_search_episodes(_trace_case(events))
    episode = batch.episodes[0]
    assert [(item.file, item.kind) for item in episode.usage_evidence] == [
        ("src/main/java/Pool.java", "opened"),
        ("src/main/java/Pool.java", "searched"),
        ("src/main/java/Pool.java", "edited"),
    ]


def test_tool_output_alone_is_not_usage_evidence() -> None:
    events = (
        TraceEvent(0, "assistant", "", "rg", "rg pool src/main/java", None),
        TraceEvent(1, "tool", "", "rg", None, "src/main/java/Pool.java"),
        TraceEvent(2, "assistant", "The search completed."),
        TraceEvent(3, "tool", "", "unknown", None, "src/main/java/Pool.java"),
    )
    batch = supervise_search_episodes(_trace_case(events))
    assert batch.episodes == ()
    assert "no_usage_evidence" in batch.skip_reasons
```

- [ ] **Step 2: Write failing ambiguity and nearest-producer tests**

```python
def test_basename_only_matches_when_unique() -> None:
    events = (
        TraceEvent(0, "assistant", "", "rg", "rg Client src/main/java", None),
        TraceEvent(
            1,
            "tool",
            "",
            "rg",
            None,
            "src/main/java/a/Client.java\nsrc/main/java/b/Client.java",
        ),
        TraceEvent(2, "assistant", "", "view", '{"path":"Client.java"}', None),
    )
    batch = supervise_search_episodes(_trace_case(events))
    assert batch.episodes == ()


def test_usage_is_assigned_to_the_most_recent_producer() -> None:
    events = (
        TraceEvent(2, "assistant", "", "rg", "rg pool src/main/java", None),
        TraceEvent(3, "tool", "", "rg", None, "src/main/java/Pool.java"),
        TraceEvent(8, "assistant", "", "rg", "rg pool src/main/java", None),
        TraceEvent(9, "tool", "", "rg", None, "src/main/java/Pool.java"),
        TraceEvent(
            10,
            "assistant",
            "",
            "str_replace_editor",
            '{"command":"view","path":"src/main/java/Pool.java"}',
            None,
        ),
    )
    batch = supervise_search_episodes(_trace_case(events))
    assert [episode.anchor_event for episode in batch.episodes] == [8]


def test_complete_assistant_path_and_function_create_reference_evidence() -> None:
    events = (
        TraceEvent(
            0,
            "assistant",
            "",
            "rg",
            "rg select src/main/java",
            None,
        ),
        TraceEvent(
            1,
            "tool",
            "",
            "rg",
            None,
            "src/main/java/Pool.java:42: public void select() {",
        ),
        TraceEvent(
            2,
            "assistant",
            "src/main/java/Pool.java and select explain the ordering behavior.",
        ),
    )
    episode = supervise_search_episodes(_trace_case(events)).episodes[0]
    assert episode.answer == (CodeLocation("src/main/java/Pool.java", ("select",)),)
    assert episode.usage_evidence[0].kind == "referenced"


def test_read_range_promotes_only_the_declaration_inside_the_range() -> None:
    events = (
        TraceEvent(0, "assistant", "", "rg", "rg select src/main/java", None),
        TraceEvent(
            1,
            "tool",
            "",
            "rg",
            None,
            "src/main/java/Pool.java:42: public void select() {",
        ),
        TraceEvent(
            2,
            "assistant",
            "",
            "str_replace_editor",
            '{"command":"view","path":"src/main/java/Pool.java","view_range":[40,50]}',
            None,
        ),
    )
    episode = supervise_search_episodes(_trace_case(events)).episodes[0]
    assert episode.answer == (CodeLocation("src/main/java/Pool.java", ("select",)),)
```

- [ ] **Step 3: Run the evidence tests and verify failure**

Run: `conda run -n codesearch pytest tests/unit/evaluation/test_trace_search.py -q`

Expected: FAIL because supervision and evidence matching are not implemented.

- [ ] **Step 4: Implement structured later-action classification**

```python
def _usage_kind(event: TraceEvent) -> str | None:
    """Classify a later action as opened, searched, edited, or unsupported."""


def _match_candidate(
    action: str, candidates: Sequence[CandidateLocation]
) -> tuple[CandidateLocation, ...]:
    """Prefer exact paths, then unique suffixes, then unique basenames."""
```

Parse structured JSON arguments before shell text. Recognize read/view/cat/sed/head/tail as `opened`, search
tools as `searched`, and edit/write/apply-patch operations as `edited`. Assistant prose becomes `referenced`
only for a complete normalized path or a unique file-plus-function pair.

- [ ] **Step 5: Implement supervision and file-level partitioning**

```python
@dataclass(frozen=True, slots=True)
class SupervisedEpisode:
    episode: SearchEpisode
    answer: tuple[CodeLocation, ...]
    candidate_answers: tuple[CodeLocation, ...]
    usage_evidence: tuple[UsageEvidence, ...]


@dataclass(frozen=True, slots=True)
class SupervisionBatch:
    episodes: tuple[SupervisedEpisode, ...]
    skip_reasons: tuple[str, ...]
    search_episode_count: int


def supervise_search_episodes(case: TraceCase) -> SupervisionBatch:
    """Return only episodes with at least one later-used production Java file."""
```

Scan all episodes before assigning evidence so a later use can be credited to the nearest preceding producer.
When a file becomes gold, include only functions with function-level evidence or a declaration line inside the
later read range. Remove that entire file from `candidate_answers`.

- [ ] **Step 6: Implement exact episode deduplication**

```python
def _episode_signature(value: SupervisedEpisode) -> tuple[object, ...]:
    return (
        normalize_action(value.episode.raw_action),
        tuple(sorted(value.answer, key=lambda item: (item.file, item.functions))),
        tuple(sorted(value.candidate_answers, key=lambda item: (item.file, item.functions))),
    )
```

Keep the greatest `anchor_event` for equal signatures and append `duplicate_episode` for each discarded item.

- [ ] **Step 7: Run focused tests**

Run: `conda run -n codesearch pytest tests/unit/evaluation/test_trace_search.py -q`

Expected: PASS.

- [ ] **Step 8: Commit deterministic supervision**

```bash
git add evaluation/trace_search.py tests/unit/evaluation/test_trace_search.py
git commit -m "feat: derive trace search supervision"
```

---

### Task 4: Generate and Validate One Semantic Query per Supervised Episode

**Files:**
- Modify: `evaluation/query_mining.py:1-310`
- Modify: `tests/unit/evaluation/test_query_mining.py`
- Modify: `tests/unit/evaluation/test_semantic_trace_pipeline.py`

**Interfaces:**
- Consumes: `SupervisionBatch` from `supervise_search_episodes(case)` and `QueryGenerator.generate(prompt)`.
- Produces: `build_prompt(case, episode, prompt_version)`, `mine_queries(case, generator, prompt_version) -> MiningBatch`, and populated `PreparedQuery` rows.

- [ ] **Step 1: Replace patch-gold fixtures with episode-supervision fixtures**

```python
def _valid_response(query: str) -> str:
    return json.dumps({
        "status": "valid",
        "query": query,
        "reason": "The anchor is combined with its behavioral effect.",
        "anchor_terms": ["isPoolLifo"],
        "semantic_constraints": ["changes reusable connection selection"],
    })


def _case(*, two_searches: bool = False) -> TraceCase:
    events = [
        TraceEvent(0, "user", "Pool reuse order can change request behavior."),
        TraceEvent(1, "assistant", "I will inspect pool ordering."),
        TraceEvent(2, "assistant", "", "rg", "rg isPoolLifo src/main/java", None),
        TraceEvent(
            3,
            "tool",
            "",
            "rg",
            None,
            "src/main/java/Pool.java\nsrc/main/java/PoolConfig.java",
        ),
        TraceEvent(
            4,
            "assistant",
            "I will read Pool.java now.",
            "str_replace_editor",
            '{"command":"view","path":"src/main/java/Pool.java"}',
            None,
        ),
        TraceEvent(5, "tool", "", "str_replace_editor", None, "class Pool {}"),
    ]
    if two_searches:
        events.extend(
            [
                TraceEvent(6, "assistant", "I will inspect buffer pressure."),
                TraceEvent(7, "assistant", "", "rg", "rg watermark src/main/java", None),
                TraceEvent(8, "tool", "", "rg", None, "src/main/java/Buffer.java"),
                TraceEvent(
                    9,
                    "assistant",
                    "",
                    "str_replace_editor",
                    '{"command":"view","path":"src/main/java/Buffer.java"}',
                    None,
                ),
            ]
        )
    return TraceCase(
        "acme/project",
        "java",
        "issue-1",
        "trace-1",
        "Pool reuse order can change request behavior.",
        None,
        tuple(events),
        {},
    )
```

Build a trace where a search returns `Pool.java` and `PoolConfig.java`, then a later view opens only `Pool.java`.

- [ ] **Step 2: Add failing prompt-boundary tests**

```python
def test_prompt_contains_issue_reasoning_action_but_hides_result_and_future() -> None:
    case = _case()
    episode = supervise_search_episodes(case).episodes[0]
    prompt = build_prompt(case, episode, prompt_version="trace-search-v2")
    assert case.issue_statement in prompt
    assert "isPoolLifo" in prompt
    assert "I will inspect pool ordering" in prompt
    assert "src/main/java/Pool.java" not in prompt
    assert "I will read Pool.java now." not in prompt
    assert "SECRET_PATCH" not in prompt
```

- [ ] **Step 3: Add failing batch-mining and semantic-gate tests**

```python
def test_mine_queries_returns_one_row_per_supervised_episode() -> None:
    generator = _Generator(_valid_response("Find code whose policy changes runtime behavior."))
    batch = mine_queries(_case(two_searches=True), generator)
    assert [query.query_id for query in batch.queries] == ["trace-1:2", "trace-1:7"]


def test_exact_anchor_with_behavior_passes_but_direct_lookup_fails() -> None:
    accepted = mine_queries(
        _case(),
        _Generator(
            _valid_response("Find where isPoolLifo changes reusable connection selection.")
        ),
    )
    rejected = mine_queries(
        _case(),
        _Generator(
            json.dumps(
                {
                    "status": "valid",
                    "query": "Locate calls to isPoolLifo.",
                    "reason": "Direct lookup.",
                    "anchor_terms": ["isPoolLifo"],
                    "semantic_constraints": [],
                }
            )
        ),
    )
    assert len(accepted.queries) == 1
    assert rejected.queries == ()
    assert rejected.skip_reasons == ("missing_semantic_constraint",)
```

- [ ] **Step 4: Run the mining tests and verify old one-query behavior fails**

Run: `conda run -n codesearch pytest tests/unit/evaluation/test_query_mining.py tests/unit/evaluation/test_semantic_trace_pipeline.py -q`

Expected: FAIL because `mine_query()` still uses patch gold and returns one row per trace.

- [ ] **Step 5: Implement episode-specific prompt and strict JSON parsing**

```python
@dataclass(frozen=True, slots=True)
class GeneratedQuery:
    query: str
    reason: str
    anchor_terms: tuple[str, ...]
    semantic_constraints: tuple[str, ...]


def build_prompt(
    case: TraceCase, episode: SupervisedEpisode, *, prompt_version: str
) -> str:
    """Expose issue, pre-search reasoning, and action while hiding answer-side events."""
```

Update few-shot examples and require one JSON object with all four fields. `anchor_terms` may be empty;
`semantic_constraints` must not be empty.

- [ ] **Step 6: Replace identifier leakage rejection with the first semantic gate**

```python
_SHELL_OR_PATH = re.compile(r"(?:^|\s)(?:rg|grep|git\s+grep)\b|[\w./-]+\.java\b", re.I)
_PURE_DIRECT_LOOKUP = re.compile(
    r"\s*(?:find|list|locate|search for)\s+(?:all\s+)?(?:java\s+)?"
    r"(?:files?\s+(?:that\s+)?(?:contain|reference)|implementations?\s+of|"
    r"calls?\s+to|(?:class|method)\s+named)\b[^,.]*[.]?\s*",
    re.I,
)


def contains_shell_or_java_path(query: str) -> bool:
    return bool(_SHELL_OR_PATH.search(query))


def is_pure_direct_lookup(query: str) -> bool:
    return bool(_PURE_DIRECT_LOOKUP.fullmatch(query))


def _query_error(generated: GeneratedQuery) -> str | None:
    if not generated.semantic_constraints:
        return "missing_semantic_constraint"
    if contains_shell_or_java_path(generated.query):
        return "query_leaks_result"
    if is_pure_direct_lookup(generated.query):
        return "direct_lookup_query"
    return None
```

Delete exact gold identifier rejection. Implement `is_pure_direct_lookup()` with anchored bad-form patterns so
`Locate calls to isPoolLifo` fails but an anchor combined with a behavioral clause passes.

- [ ] **Step 7: Implement batch mining without retry**

```python
@dataclass(frozen=True, slots=True)
class MiningBatch:
    queries: tuple[PreparedQuery, ...]
    skip_reasons: tuple[str, ...]
    search_episode_count: int
    eligible_episode_count: int


def mine_queries(
    case: TraceCase,
    generator: QueryGenerator,
    *,
    prompt_version: str = "trace-search-v2",
) -> MiningBatch:
    """Generate one query per supervised episode; isolate per-episode failures."""
```

Call the generator only after deterministic supervision yields gold. Set `query_id` to
`f"{case.trajectory_id}:{anchor_event}"`, `strategy` to `trace-search-generated`, and preserve model,
prompt, raw action, reason, and candidate count in provenance.

- [ ] **Step 8: Run focused query mining and integration tests**

Run: `conda run -n codesearch pytest tests/unit/evaluation/test_query_mining.py tests/unit/evaluation/test_semantic_trace_pipeline.py -q`

Expected: PASS.

- [ ] **Step 9: Commit semantic episode mining**

```bash
git add evaluation/query_mining.py tests/unit/evaluation/test_query_mining.py tests/unit/evaluation/test_semantic_trace_pipeline.py
git commit -m "feat: mine semantic queries from useful searches"
```

---

### Task 5: Update the Hard-Coded Mining Entry Point

**Files:**
- Modify: `scripts/mine_trace_queries.py:1-105`
- Create: `tests/unit/test_mine_trace_queries_script.py`

**Interfaces:**
- Consumes: `mine_queries()` and deterministic supervision/prompt helpers.
- Produces: `_run_cases(cases, generator, output, limit, prompt_version, dry_run) -> dict[str, object]`, zero-to-many JSONL rows per trace, one prompt row per eligible episode in dry-run mode, and aggregate trace/episode/skip counters.

- [ ] **Step 1: Write failing script behavior tests**

```python
from types import SimpleNamespace


def test_main_writes_every_query_returned_for_one_trace(tmp_path, monkeypatch) -> None:
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


def test_run_cases_does_not_stop_after_ten_processed_traces(tmp_path, monkeypatch) -> None:
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
```

- [ ] **Step 2: Run the new script tests and verify failure**

Run: `conda run -n codesearch pytest tests/unit/test_mine_trace_queries_script.py -q`

Expected: FAIL because the script calls singular `mine_query()` and contains an unconditional ten-record break.

- [ ] **Step 3: Implement zero-to-many output and dry-run rows**

```python
def _prompt_rows(
    case: TraceCase, prompt_version: str
) -> tuple[list[dict[str, object]], int, int, tuple[str, ...]]:
    supervision = supervise_search_episodes(case)
    rows = [
        {
            "type": "prompt",
            "repo": case.repo,
            "trajectory_id": case.trajectory_id,
            "anchor_event": item.episode.anchor_event,
            "source_event_indices": [event.index for event in item.episode.context_events],
            "result_event_indices": [event.index for event in item.episode.result_events],
            "prompt": build_prompt(case, item, prompt_version=prompt_version),
        }
        for item in supervision.episodes
    ]
    return (
        rows,
        supervision.search_episode_count,
        len(supervision.episodes),
        supervision.skip_reasons,
    )


def _run_cases(
    cases: Iterable[TraceCase],
    generator: QueryGenerator | None,
    output_path: Path,
    *,
    limit: int,
    prompt_version: str,
    dry_run: bool,
) -> dict[str, object]:
    processed = written = search_episodes = eligible_episodes = 0
    skip_reasons: Counter[str] = Counter()
    with output_path.open("w", encoding="utf-8") as output:
        for case in cases:
            if limit and processed >= limit:
                break
            if dry_run:
                rows, search_count, eligible_count, reasons = _prompt_rows(case, prompt_version)
            else:
                if generator is None:
                    raise ValueError("generator is required unless DRY_RUN is true")
                batch = mine_queries(case, generator, prompt_version=prompt_version)
                rows = [query.to_dict() for query in batch.queries]
                search_count = batch.search_episode_count
                eligible_count = batch.eligible_episode_count
                reasons = batch.skip_reasons
            for row in rows:
                output.write(json.dumps(row, ensure_ascii=False) + "\n")
            skip_reasons.update(reasons)
            processed += 1
            written += len(rows)
            search_episodes += search_count
            eligible_episodes += eligible_count
    return {
        "processed_traces": processed,
        "search_episodes": search_episodes,
        "eligible_episodes": eligible_episodes,
        "written_queries": written,
        "skipped": dict(skip_reasons),
    }
```

Set `PROMPT_VERSION = "trace-search-v2"`. Remove the `processed % 10 == 0` debug break; only `LIMIT`
controls input count. Dry-run calls deterministic supervision and writes one prompt object per eligible episode
without creating a generator.

- [ ] **Step 4: Emit stable aggregate counters**

```python
summary = {
    "processed_traces": processed,
    "search_episodes": search_episodes,
    "eligible_episodes": eligible_episodes,
    "written_queries": written,
    "skipped": skip_reasons,
}
```

- [ ] **Step 5: Run the script and mining test subset**

Run: `conda run -n codesearch pytest tests/unit/test_mine_trace_queries_script.py tests/unit/evaluation/test_query_mining.py -q`

Expected: PASS.

- [ ] **Step 6: Commit the mining entry point**

```bash
git add scripts/mine_trace_queries.py tests/unit/test_mine_trace_queries_script.py
git commit -m "feat: emit per-episode benchmark queries"
```

---

### Task 6: Score Gold and Classify Candidate Hits

**Files:**
- Modify: `scripts/evaluation.py:270-380`
- Modify: `tests/unit/test_evaluation_script.py`

**Interfaces:**
- Consumes: existing CodeSense ranked hits, `answer`, and `candidate_answers` from each JSONL row.
- Produces: `observed_file_precision`, `file_recall`, `mrr`, `first_gold_rank`, optional function metrics, and a `label` on each serialized hit.

- [ ] **Step 1: Write failing metric and hit-label tests**

```python
def test_score_reports_observed_precision_rank_and_candidate_labels() -> None:
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
```

- [ ] **Step 2: Run the evaluator tests and verify the signature mismatch**

Run: `conda run -n codesearch pytest tests/unit/test_evaluation_script.py -q`

Expected: FAIL because `_score()` does not accept candidates or return rank labels.

- [ ] **Step 3: Implement file-level ranking metrics and labels**

```python
def _score(
    hits: Sequence[object],
    answers: Sequence[Mapping[str, object]],
    candidate_answers: Sequence[Mapping[str, object]],
    *,
    include_test_files: bool,
) -> tuple[dict[str, object], list[str]]:
    gold_files = _unique(_path(answer.get("file")) for answer in answers)
    candidate_files = set(
        _unique(_path(answer.get("file")) for answer in candidate_answers)
    ) - set(gold_files)
    hit_paths = [_path(getattr(hit, "file", "")) for hit in hits]
    labels = [
        "gold_hit"
        if path in set(gold_files)
        else "candidate_hit"
        if path in candidate_files
        else "unlabeled_hit"
        for path in hit_paths
    ]
    hit_files = _unique(hit_paths)
    matched_files = [file for file in gold_files if file in set(hit_files)]
    first_gold_rank = next(
        (index for index, label in enumerate(labels, 1) if label == "gold_hit"),
        None,
    )
    metrics = {
        "gold_files": gold_files,
        "matched_files": matched_files,
        "observed_file_precision": _ratio(len(matched_files), len(hit_files)),
        "file_recall": _ratio(len(matched_files), len(gold_files)),
        "first_gold_rank": first_gold_rank,
        "mrr": 1 / first_gold_rank if first_gold_rank is not None else 0.0,
    }
    return metrics, labels
```

Observed precision keeps the existing unique-file denominator. `first_gold_rank` uses the first ranked hit whose
normalized file is gold; `mrr` is `1 / first_gold_rank`, or `0.0` when no gold is retrieved. Preserve optional
function recall/precision under their current exact-match semantics.

- [ ] **Step 4: Attach labels without changing the search call**

```python
metrics, labels = _score(result.hits, answers, candidates, include_test_files=include_test_files)
route_result["hits"] = [
    {**_hit_dict(hit), "label": label} for hit, label in zip(result.hits, labels, strict=True)
]
route_result["metrics"] = metrics
```

Copy `candidate_answers` into successful and failed evaluation records. Do not alter
`project.search(query, route=route, limit=limit)`.

- [ ] **Step 5: Update route summaries**

```python
summary[route] = {
    "queries": len(results),
    "completed": len(valid),
    "errors": sum("error" in result for result in results),
    "observed_file_precision": _mean(
        metric.get("observed_file_precision") for metric in metrics
    ),
    "file_recall": _mean(metric.get("file_recall") for metric in metrics),
    "mrr": _mean(metric.get("mrr") for metric in metrics),
    "function_precision": _mean(metric.get("function_precision") for metric in metrics),
    "function_recall": _mean(metric.get("function_recall") for metric in metrics),
}
```

- [ ] **Step 6: Run the evaluator tests**

Run: `conda run -n codesearch pytest tests/unit/test_evaluation_script.py -q`

Expected: PASS and existing tests still prove all three routes receive the same query and Top-20 limit.

- [ ] **Step 7: Commit evaluation semantics**

```bash
git add scripts/evaluation.py tests/unit/test_evaluation_script.py
git commit -m "feat: score observed trace search gold"
```

---

### Task 7: Show Episodes, Candidates, Evidence, and Hit Labels

**Files:**
- Modify: `evaluation/query_viewer.py:95-210`
- Modify: `evaluation/live_results.py:100-310`
- Modify: `tests/unit/evaluation/test_query_viewer.py`
- Modify: `tests/unit/evaluation/test_live_results.py`

**Interfaces:**
- Consumes: the new query JSONL fields and evaluator hit labels.
- Produces: readable query cards with separate gold/candidate/evidence sections and trace-event highlighting; live evaluation cards with observed metrics and three hit styles.

- [ ] **Step 1: Extend viewer fixtures and add failing content assertions**

```python
record = _record("one")
record["candidate_answers"] = [{"file": "src/main/java/PoolConfig.java", "functions": []}]
record["usage_evidence"] = [
    {"file": "src/main/java/Pool.java", "functions": [], "event_index": 8, "kind": "opened"}
]
record["result_event_indices"] = [3]

page = query_viewer_page()
assert all(text in page for text in (
    "Gold answers", "Candidate answers", "Usage evidence", "result_event_indices"
))
```

For live results, assert `observed_file_precision`, `candidate_hit`, and `unlabeled_hit` are rendered with distinct
labels/classes.

- [ ] **Step 2: Run viewer tests and verify missing fields**

Run: `conda run -n codesearch pytest tests/unit/evaluation/test_query_viewer.py tests/unit/evaluation/test_live_results.py -q`

Expected: FAIL because both viewers know only patch gold and matched/extra hits.

- [ ] **Step 3: Update the query viewer with safe DOM rendering**

```javascript
const promptEvents = new Set(sequence(record.source_event_indices).map(Number));
const resultEvents = new Set(sequence(record.result_event_indices).map(Number));
```

Rename “Patch ground truth” to “Gold answers”; add candidate and evidence sections; mark prompt-visible and
result-only trace events separately. Continue using `textContent`; do not introduce `innerHTML`.

- [ ] **Step 4: Update live evaluation rendering**

```javascript
const status = String(hit.label || 'unlabeled_hit');
const className = status === 'gold_hit' ? 'matched' :
  status === 'candidate_hit' ? 'candidate' : 'extra';
```

Display `Observed file precision`, recall, MRR, and first gold rank. Show candidate answers below gold answers and
keep function details visible.

- [ ] **Step 5: Run viewer tests**

Run: `conda run -n codesearch pytest tests/unit/evaluation/test_query_viewer.py tests/unit/evaluation/test_live_results.py -q`

Expected: PASS.

- [ ] **Step 6: Commit viewer support**

```bash
git add evaluation/query_viewer.py evaluation/live_results.py tests/unit/evaluation/test_query_viewer.py tests/unit/evaluation/test_live_results.py
git commit -m "feat: visualize trace search supervision"
```

---

### Task 8: End-to-End Regression and Repository Verification

**Files:**
- Modify only if failures expose task-specific defects: files already listed in Tasks 1-7
- Test: `tests/unit/evaluation/test_semantic_trace_pipeline.py`

**Interfaces:**
- Consumes: one realistic resolved Open-SWE Java fixture with search result, later use, and one unused candidate.
- Produces: a serialized query row with the exact `query`, `answer`, and `candidate_answers` schema consumed by the evaluator, without any reference-patch dependency.

- [ ] **Step 1: Add the end-to-end acceptance test**

```python
def test_resolved_trace_becomes_episode_query_with_observed_gold() -> None:
    record = {
        "repo": "owner/repo",
        "language": "java",
        "instance_id": "issue-1",
        "trajectory_id": "trace-1",
        "resolved": 1,
        "trajectory": [
            {"role": "user", "content": "Pool reuse order can change request behavior."},
            {"role": "assistant", "content": "I will inspect the pool ordering policy."},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "search-1",
                        "function": {
                            "name": "execute_bash",
                            "arguments": json.dumps(
                                {"command": "rg isPoolLifo src/main/java"}
                            ),
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "search-1",
                "content": "src/main/java/Pool.java\nsrc/main/java/PoolConfig.java",
            },
            {
                "role": "assistant",
                "content": "I will read the selection implementation.",
                "tool_calls": [
                    {
                        "id": "read-1",
                        "function": {
                            "name": "str_replace_editor",
                            "arguments": json.dumps(
                                {"command": "view", "path": "src/main/java/Pool.java"}
                            ),
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "read-1",
                "content": "class Pool { void select() {} }",
            },
        ],
    }
    case = normalize_record(record)
    batch = mine_queries(case, _Generator())
    payload = batch.queries[0].to_dict()
    assert payload["answer"] == [{"file": "src/main/java/Pool.java", "functions": []}]
    assert payload["candidate_answers"] == [
        {"file": "src/main/java/PoolConfig.java", "functions": []}
    ]
    assert payload["query_id"] == "trace-1:2"
    assert "src/main/java/Pool.java" not in batch.queries[0].provenance["prompt"]
    assert "reference_patch" not in batch.queries[0].provenance["prompt"]
```

- [ ] **Step 2: Run the complete evaluation-focused suite**

Run: `conda run -n codesearch pytest tests/unit/evaluation tests/unit/test_evaluation_script.py tests/unit/test_mine_trace_queries_script.py -q`

Expected: PASS.

- [ ] **Step 3: Run Ruff checks on changed Python files**

Run: `conda run -n codesearch ruff check evaluation/models.py evaluation/trace_search.py evaluation/query_mining.py evaluation/trace_adapters/open_swe_traces.py evaluation/query_viewer.py evaluation/live_results.py scripts/mine_trace_queries.py scripts/evaluation.py tests/unit/evaluation tests/unit/test_evaluation_script.py tests/unit/test_mine_trace_queries_script.py`

Expected: PASS.

Run: `conda run -n codesearch ruff format --check evaluation/models.py evaluation/trace_search.py evaluation/query_mining.py evaluation/trace_adapters/open_swe_traces.py evaluation/query_viewer.py evaluation/live_results.py scripts/mine_trace_queries.py scripts/evaluation.py tests/unit/evaluation tests/unit/test_evaluation_script.py tests/unit/test_mine_trace_queries_script.py`

Expected: PASS.

- [ ] **Step 4: Run repository-wide required verification**

Run: `conda run -n codesearch ruff check .`

Expected: PASS, or report any pre-existing unrelated dirty-worktree failure separately.

Run: `conda run -n codesearch ruff format --check .`

Expected: PASS, or report any pre-existing unrelated dirty-worktree failure separately.

Run: `conda run -n codesearch pytest`

Expected: PASS, or report any independently reproduced pre-existing failure separately.

- [ ] **Step 5: Review the final diff for scope and secrets**

Run: `git diff --check`

Expected: no whitespace errors.

Run: `git status --short`

Expected: task commits contain only files from Tasks 1-7; unrelated pre-existing modifications and untracked files
remain unstaged. Confirm no API key, generated JSONL, repository clone, or model artifact is tracked.

- [ ] **Step 6: Commit the end-to-end regression if Step 1 changed the integration test after Task 4**

```bash
git add tests/unit/evaluation/test_semantic_trace_pipeline.py
git commit -m "test: cover trace search supervision pipeline"
```
