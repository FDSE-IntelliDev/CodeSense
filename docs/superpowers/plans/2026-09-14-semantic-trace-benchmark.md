# Open-SWE-Traces Semantic Benchmark Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 resolved 的 Open-SWE-Traces Java 轨迹转换为“一条轨迹一条语义 query”，并用隐藏 reference patch 中修改的既有生产 Java 文件和可确定函数作为评测答案。

**Architecture:** Open-SWE adapter 负责确定性解析原始 issue、trace 和 patch ground truth；`query_mining` 只把 issue 与搜索事件局部窗口交给 LLM，生成唯一语义 query；扁平 `PreparedQuery` 随后由批量 evaluator、静态 viewer 和实时 viewer 直接消费。答案始终来自程序解析 patch，LLM 不接触 patch，也不生成答案。

**Tech Stack:** Python 3.10+、标准库 `dataclasses/json/re/pathlib`、pytest、ruff、现有 OpenAI-compatible HTTP generator、现有 CodeSense `Project.search()`。

**Spec:** `docs/superpowers/specs/2026-09-14-semantic-trace-benchmark-design.md`

## Global Constraints

- 只处理 `language == "java"` 且严格整数 `resolved == 1` 的记录。
- 一条有效 trace 只调用一次 LLM，只输出一条英语语义 query。
- prompt 包含完整原始 issue 和搜索事件前一条/当前/后一条的 assistant/tool 事件，不包含 reference patch。
- 主答案只包含 patch 修改的既有生产 Java 文件；新增、删除和测试文件不进入 `answer`。
- 无法确定函数时保留文件，使用空 `functions`，不得让 LLM 猜函数。
- 目标 query 可以使用 `Find the logic ...`，但不得退化成文件名、符号名、引用、实现或调用关系的直接查找。
- 搜索固定 `limit=20`；requested route 降级后不计作该 route 的有效成绩。
- `scripts/mine_trace_queries.py` 和 `scripts/evaluation.py` 保持硬编码调试参数，不改造成 CLI。
- 不加入 baseline、二次 LLM 修复、query 排序、人工筛选、Java 完整 checkout 解析或新依赖。
- 当前 `dev` 工作区的相关文件已有未提交内容；实施时在当前工作区增量修改，不创建会遗漏这些改动的干净 worktree，不覆盖无关改动。
- 每次提交只暂存当前任务 `Files` 中列出的路径；不要使用 `git add .`。
- 所有 Python 测试和脚本命令通过 `conda run -n codesearch ...` 执行。

## File Structure

| 文件 | 责任 |
|---|---|
| `evaluation/models.py` | `TraceCase`、patch 代码位置和扁平 `PreparedQuery` 数据契约 |
| `evaluation/trace_adapters/open_swe_traces.py` | Open-SWE 记录规范化、strict resolved 过滤和 unified diff ground truth 提取 |
| `evaluation/query_mining.py` | 搜索事件识别、局部窗口、semantic prompt、LLM 响应解析和 query 校验 |
| `scripts/mine_trace_queries.py` | 硬编码参数入口、dry-run、逐 trace 生成和 skip reason 汇总 |
| `evaluation/query_viewer.py` | 动态读取 query JSONL 的本地自动刷新 Web viewer |
| `scripts/evaluation.py` | 仓库准备、每条扁平 query 的多 route Top-20 搜索与指标汇总 |
| `evaluation/live_results.py` | evaluator 运行时的本地 HTTP/SSE 展示 |
| `evaluation/README.md` | 新 pipeline、schema 和调试方式 |
| `CHANGELOG.md` | 完成功能后的变更记录 |

---

### Task 1: 从 reference patch 构造确定性的生产代码答案

**Files:**
- Modify: `evaluation/models.py`
- Modify: `evaluation/trace_adapters/open_swe_traces.py`
- Test: `tests/unit/evaluation/test_models.py`
- Test: `tests/unit/evaluation/test_open_swe_traces.py`

**Interfaces:**
- Consumes: Open-SWE record 的 `metadata.reference_patch.patch` unified diff 文本。
- Produces: `extract_patch_locations(patch: str) -> tuple[CodeLocation, ...]`；`TraceCase.answer: tuple[CodeLocation, ...]`；`TraceCase.gold_error: str | None`。

- [ ] **Step 1: 给 adapter fixture 加入 reference patch，并写失败测试**

在 `tests/unit/evaluation/test_open_swe_traces.py` 的 `_record()` 中加入：

```python
"metadata": {
    "reference_patch": {
        "patch": """diff --git a/src/main/java/example/Navigation.java b/src/main/java/example/Navigation.java
--- a/src/main/java/example/Navigation.java
+++ b/src/main/java/example/Navigation.java
@@ -10,3 +10,4 @@ public PageRequest afterCursor(Cursor cursor) {
-    return withCursor(cursor);
+    return withCursor(cursor).withoutPage();
 }
diff --git a/src/main/java/example/Config.java b/src/main/java/example/Config.java
--- a/src/main/java/example/Config.java
+++ b/src/main/java/example/Config.java
@@ -3,2 +3,2 @@ class Config {
-    private int page = 1;
+    private int page = 0;
diff --git a/src/test/java/example/NavigationTest.java b/src/test/java/example/NavigationTest.java
--- a/src/test/java/example/NavigationTest.java
+++ b/src/test/java/example/NavigationTest.java
@@ -5,2 +5,2 @@ void switchesMode() {
-    assertOld();
+    assertNew();
diff --git a/src/main/java/example/NewMode.java b/src/main/java/example/NewMode.java
new file mode 100644
--- /dev/null
+++ b/src/main/java/example/NewMode.java
@@ -0,0 +1 @@
+class NewMode {}
"""
    }
},
```

新增断言，固定生产文件、函数和排除规则：

```python
def test_normalize_record_extracts_existing_production_java_patch_gold() -> None:
    case = normalize_record(_record())

    assert case.answer == (
        CodeLocation("src/main/java/example/Navigation.java", ("afterCursor",)),
        CodeLocation("src/main/java/example/Config.java", ()),
    )
    assert case.gold_error is None


def test_normalize_record_marks_missing_or_unusable_patch_without_rejecting_trace() -> None:
    missing = _record()
    missing.pop("metadata")
    assert normalize_record(missing).gold_error == "missing_reference_patch"

    tests_only = _record()
    tests_only["metadata"]["reference_patch"]["patch"] = """diff --git a/src/test/java/A.java b/src/test/java/A.java
--- a/src/test/java/A.java
+++ b/src/test/java/A.java
@@ -1 +1 @@ void testA() {
-old();
+newer();
"""
    assert normalize_record(tests_only).gold_error == "no_production_java_gold"
```

再增加一个完整 issue 保留测试，防止 adapter 擅自清洗答案提示：

```python
def test_normalize_record_preserves_the_complete_original_issue() -> None:
    record = _record()
    issue = (
        "<issue_description>Fix navigation state.\n"
        "New interfaces introduced: Navigation#afterCursor</issue_description>"
    )
    trajectory = record["trajectory"]
    assert isinstance(trajectory, list)
    first = trajectory[0]
    assert isinstance(first, dict)
    first["content"] = issue

    assert normalize_record(record).issue_statement == issue
```

给测试文件补充 `from evaluation.models import CodeLocation`。

- [ ] **Step 2: 运行 adapter 测试，确认因字段和解析函数缺失而失败**

Run:

```bash
conda run -n codesearch pytest -q tests/unit/evaluation/test_open_swe_traces.py
```

Expected: FAIL，错误至少包含 `TraceCase` 没有 `answer`/`gold_error`，或 patch 尚未解析。

- [ ] **Step 3: 扩展 `TraceCase` 的 patch gold 字段和序列化测试**

把 `CodeLocation` 放到 `TraceCase` 之前定义，并在 `TraceCase` 末尾增加默认字段，避免其他测试
fixture 立即失效：

```python
@dataclass(frozen=True, slots=True)
class CodeLocation:
    file: str
    functions: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {"file": self.file, "functions": list(self.functions)}


@dataclass(frozen=True, slots=True)
class TraceCase:
    repo: str
    language: str
    instance_id: str
    trajectory_id: str
    issue_statement: str
    base_commit: str | None
    events: tuple[TraceEvent, ...]
    raw: Mapping[str, object]
    answer: tuple[CodeLocation, ...] = ()
    gold_error: str | None = None
```

在 `TraceCase.to_dict()` 中加入：

```python
"answer": [location.to_dict() for location in self.answer],
"gold_error": self.gold_error,
```

在 `tests/unit/evaluation/test_models.py` 增加一个最小序列化断言：

```python
def test_trace_case_serializes_patch_gold() -> None:
    case = TraceCase(
        "owner/repo",
        "java",
        "issue-1",
        "trace-1",
        "Fix navigation state.",
        None,
        (),
        {},
        (CodeLocation("src/main/java/Navigation.java", ("afterCursor",)),),
        None,
    )

    assert case.to_dict()["answer"] == [
        {"file": "src/main/java/Navigation.java", "functions": ["afterCursor"]}
    ]
```

- [ ] **Step 4: 在 Open-SWE adapter 中实现最小 unified diff parser**

在 `evaluation/trace_adapters/open_swe_traces.py` 增加 `re`、`PurePosixPath` 和 `Sequence`
导入，导入 `CodeLocation`，并导出解析函数：

```python
__all__ = ["extract_patch_locations", "iter_jsonl", "normalize_record"]

_DIFF_HEADER = re.compile(r"^diff --git a/(.+) b/(.+)$")
_HUNK_HEADER = re.compile(r"^@@ .*? @@\s*(.*)$")
_IDENTIFIER = re.compile(r"[A-Za-z_$][\w$]*")
_CONTROL_WORDS = {"catch", "do", "for", "if", "new", "return", "switch", "throw", "while"}
_IGNORED_DIRS = {"build", "generated", "target", "test", "tests"}


def extract_patch_locations(patch: str) -> tuple[CodeLocation, ...]:
    """Extract existing production Java files and declared functions from a unified diff."""
    if not patch.strip():
        return ()
    sections = re.split(r"(?=^diff --git )", patch, flags=re.MULTILINE)
    by_file: dict[str, list[str]] = {}
    saw_diff = False
    for section in sections:
        lines = section.splitlines()
        if not lines:
            continue
        header = _DIFF_HEADER.match(lines[0])
        if not header:
            continue
        saw_diff = True
        _old_path, new_path = header.groups()
        if _is_added_or_deleted(lines) or not _is_production_java(new_path):
            continue
        functions = by_file.setdefault(new_path, [])
        for function in _changed_functions(lines):
            if function not in functions:
                functions.append(function)
    if not saw_diff:
        raise ValueError("reference patch is not a unified diff")
    return tuple(CodeLocation(file, tuple(functions)) for file, functions in by_file.items())


def _is_added_or_deleted(lines: Sequence[str]) -> bool:
    return any(
        line in {"new file mode 100644", "deleted file mode 100644", "--- /dev/null", "+++ /dev/null"}
        or line.startswith(("new file mode ", "deleted file mode "))
        for line in lines
    )


def _is_production_java(path: str) -> bool:
    parsed = PurePosixPath(path)
    if parsed.suffix.lower() != ".java":
        return False
    if any(part.lower() in _IGNORED_DIRS for part in parsed.parts):
        return False
    if parsed.parts and parsed.parts[0].lower() in {"example", "examples"}:
        return False
    return re.search(r"(?:Test|Tests|TestCase|IT)\.java$", parsed.name) is None


def _changed_functions(lines: Sequence[str]) -> tuple[str, ...]:
    candidates: list[str] = []
    for line in lines:
        hunk = _HUNK_HEADER.match(line)
        if hunk:
            candidates.append(hunk.group(1))
        elif line[:1] in {" ", "+", "-"} and not line.startswith(("+++", "---")):
            candidates.append(line[1:])
    found: list[str] = []
    for candidate in candidates:
        name = _declared_function(candidate)
        if name and name not in found:
            found.append(name)
    return tuple(found)


def _declared_function(line: str) -> str | None:
    text = line.strip()
    if not text or text.startswith(("//", "/*", "*", "@")) or "(" not in text:
        return None
    before, after = text.split("(", 1)
    if "." in before or "=" in before or "->" in text:
        return None
    words = _IDENTIFIER.findall(before)
    if not words or words[-1] in _CONTROL_WORDS:
        return None
    # A bare ``foo();`` is a call. Constructors are accepted when a body opens.
    if len(words) == 1 and "{" not in after:
        return None
    if words[0] in _CONTROL_WORDS:
        return None
    return words[-1]
```

在 `normalize_record()` 构造 `TraceCase` 前读取 patch，并保留稳定错误原因：

```python
patch = _reference_patch(record)
gold_error = None
if patch is None:
    answer = ()
    gold_error = "missing_reference_patch"
else:
    try:
        answer = extract_patch_locations(patch)
    except ValueError:
        answer = ()
        gold_error = "invalid_reference_patch"
    if not answer and gold_error is None:
        gold_error = "no_production_java_gold"
```

并把 `answer=answer, gold_error=gold_error` 传给 `TraceCase`。新增 helper：

```python
def _reference_patch(record: Mapping[str, object]) -> str | None:
    metadata = record.get("metadata")
    if not isinstance(metadata, Mapping):
        return None
    reference = metadata.get("reference_patch")
    if not isinstance(reference, Mapping):
        return None
    return _as_optional_text(reference.get("patch"))
```

- [ ] **Step 5: 运行 Task 1 测试并修正格式**

Run:

```bash
conda run -n codesearch pytest -q tests/unit/evaluation/test_models.py tests/unit/evaluation/test_open_swe_traces.py
conda run -n codesearch ruff check evaluation/models.py evaluation/trace_adapters/open_swe_traces.py tests/unit/evaluation/test_models.py tests/unit/evaluation/test_open_swe_traces.py
conda run -n codesearch ruff format --check evaluation/models.py evaluation/trace_adapters/open_swe_traces.py tests/unit/evaluation/test_models.py tests/unit/evaluation/test_open_swe_traces.py
```

Expected: 全部 PASS。

- [ ] **Step 6: 只提交 patch gold 相关文件**

```bash
git add evaluation/models.py evaluation/trace_adapters/open_swe_traces.py tests/unit/evaluation/test_models.py tests/unit/evaluation/test_open_swe_traces.py
git commit -m "feat: derive Java search gold from reference patches"
```

---

### Task 2: 生成唯一语义 query 并迁移为扁平 schema

**Files:**
- Modify: `evaluation/models.py`
- Modify: `evaluation/query_mining.py`
- Modify: `scripts/mine_trace_queries.py`
- Test: `tests/unit/evaluation/test_models.py`
- Test: `tests/unit/evaluation/test_query_mining.py`

**Interfaces:**
- Consumes: Task 1 的 `TraceCase.answer`、`TraceCase.gold_error` 和 `TraceEvent`。
- Produces: `search_context(events: Sequence[TraceEvent]) -> tuple[TraceEvent, ...]`；`mine_query(case: TraceCase, generator: QueryGenerator, *, prompt_version: str = "semantic-query-v1") -> MiningOutcome`；扁平 `PreparedQuery.to_dict()`。

- [ ] **Step 1: 把 model 和 mining 测试改成最终扁平契约**

在 `tests/unit/evaluation/test_models.py` 中把旧 `SearchQuery` fixture 改为：

```python
query = PreparedQuery(
    query_id="trace-1",
    repo="acme/project",
    instance_id="issue-1",
    trajectory_id="trace-1",
    issue_statement="Navigation mode can leave stale state.",
    query=(
        "Find the logic that can leave navigation state inconsistent when switching "
        "between cursor-based and page-based access."
    ),
    answer=(CodeLocation("src/main/java/Navigation.java", ("afterCursor",)),),
    source_event_indices=(1, 2, 3),
    strategy="semantic-generated",
    source_events=(event,),
    provenance={"prompt_version": "semantic-query-v1", "query_reason": "behavioral"},
)
```

断言序列化结果包含顶层 `query`、`answer`、`issue_statement`、`source_event_indices`，并且不含
`searches`、`final_answer`。

在 `tests/unit/evaluation/test_query_mining.py` 的 `_case()` 中设置：

```python
answer=(CodeLocation("src/main/java/example/Navigation.java", ("afterCursor",)),),
gold_error=None,
```

并将文件开头导入更新为：

```python
import json

import pytest

from evaluation.models import CodeLocation, TraceCase, TraceEvent
from evaluation.query_mining import build_prompt, is_search_event, mine_query, search_context
```

把主要测试改为一次模型响应：

```python
def test_mine_query_returns_one_semantic_query_with_patch_gold() -> None:
    generator = _Generator(
        '{"status":"valid",'
        '"query":"Find the logic that can leave navigation state inconsistent when '
        'switching between cursor-based and page-based access.",'
        '"reason":"It describes a state transition failure without code identifiers."}'
    )

    outcome = mine_query(_case(), generator)

    assert outcome.skip_reason is None
    assert outcome.query is not None
    assert outcome.query.answer == _case().answer
    assert outcome.query.source_event_indices == (1, 2, 3, 4, 5, 6, 7)
    assert outcome.query.strategy == "semantic-generated"
    assert len(generator.prompts) == 1
```

添加 prompt 边界测试：

```python
def test_prompt_contains_full_issue_and_semantic_few_shots_but_not_patch() -> None:
    original = _case()
    case = TraceCase(
        repo=original.repo,
        language=original.language,
        instance_id=original.instance_id,
        trajectory_id=original.trajectory_id,
        issue_statement=original.issue_statement,
        base_commit=original.base_commit,
        events=original.events,
        raw={
            "metadata": {
                "reference_patch": {
                    "patch": "SECRET_PATCH src/main/java/example/Navigation.java"
                }
            }
        },
        answer=original.answer,
        gold_error=original.gold_error,
    )

    prompt = build_prompt(case, prompt_version="semantic-query-v1")

    assert case.issue_statement in prompt
    assert "Find functions whose behavior can affect disk performance." in prompt
    assert "Find Java files that reference PageRequest." in prompt
    assert "Find the logic that can leave navigation state inconsistent" in prompt
    assert "SECRET_PATCH" not in prompt
```

添加校验测试，明确接受目标示例并拒绝直接查找：

```python
@pytest.mark.parametrize(
    "query",
    [
        "Find Java files that reference PageRequest.",
        "Find implementations of LoadBalance.",
        "Locate calls to isPoolLifo.",
        "Search for Navigation.java.",
        "rg Navigation src/main/java",
    ],
)
def test_mine_query_rejects_direct_lookup_or_gold_leakage(query: str) -> None:
    response = json.dumps({"status": "valid", "query": query, "reason": "direct lookup"})

    outcome = mine_query(_case(), _Generator(response))

    assert outcome.query is None
    assert outcome.skip_reason in {"non_semantic_query", "query_leaks_gold_identifier"}
```

保留现有结构化 shell command 搜索识别测试；删除旧 multi-search、LLM answer、最终回答必须有
函数的断言。用下面三个测试固定失败分类和“不重试”语义：

```python
def test_mine_query_reports_generator_failure_without_retry() -> None:
    generator = _Generator(None)

    outcome = mine_query(_case(), generator)

    assert outcome == MiningOutcome(None, "generator_failed")
    assert len(generator.prompts) == 1


def test_mine_query_reports_malformed_json_without_retry() -> None:
    generator = _Generator("not valid JSON")

    outcome = mine_query(_case(), generator)

    assert outcome == MiningOutcome(None, "invalid_model_json")
    assert len(generator.prompts) == 1


def test_mine_query_accepts_explicit_invalid_trace_without_retry() -> None:
    generator = _Generator('{"status":"invalid trace"}')

    outcome = mine_query(_case(), generator)

    assert outcome == MiningOutcome(None, "non_semantic_query")
    assert len(generator.prompts) == 1
```

测试导入列表同步加入 `MiningOutcome`。

- [ ] **Step 2: 运行 model/mining 测试，确认旧 schema 导致失败**

```bash
conda run -n codesearch pytest -q tests/unit/evaluation/test_models.py tests/unit/evaluation/test_query_mining.py
```

Expected: FAIL，错误指向 `PreparedQuery` 参数、缺少 `mine_query` 或旧 prompt 内容。

- [ ] **Step 3: 将 `PreparedQuery` 改成唯一 query + answer**

在 `evaluation/models.py` 删除 `SearchQuery`，更新 `__all__`，把 `PreparedQuery` 定义为：

```python
@dataclass(frozen=True, slots=True)
class PreparedQuery:
    query_id: str
    repo: str
    instance_id: str
    trajectory_id: str
    issue_statement: str
    query: str
    answer: tuple[CodeLocation, ...]
    source_event_indices: tuple[int, ...]
    strategy: str
    source_events: tuple[TraceEvent, ...]
    provenance: Mapping[str, object]

    def to_dict(self) -> dict[str, Any]:
        return {
            "query_id": self.query_id,
            "repo": self.repo,
            "instance_id": self.instance_id,
            "trajectory_id": self.trajectory_id,
            "issue_statement": self.issue_statement,
            "query": self.query,
            "answer": [location.to_dict() for location in self.answer],
            "source_event_indices": list(self.source_event_indices),
            "strategy": self.strategy,
            "source_events": [event.to_dict() for event in self.source_events],
            "provenance": dict(self.provenance),
        }
```

- [ ] **Step 4: 用搜索局部窗口和已确认 few-shot 重写 prompt**

在 `evaluation/query_mining.py` 删除 `_TraceExtraction`、`SearchQuery`、`find_episodes()`、最终
assistant answer 校验和 LLM locations 解析。新增：

```python
@dataclass(frozen=True, slots=True)
class _GeneratedQuery:
    query: str
    reason: str


@dataclass(frozen=True, slots=True)
class MiningOutcome:
    query: PreparedQuery | None
    skip_reason: str | None = None


def search_context(events: Sequence[TraceEvent]) -> tuple[TraceEvent, ...]:
    search_positions = {position for position, event in enumerate(events) if is_search_event(event)}
    positions = {
        neighbor
        for position in search_positions
        for neighbor in (position - 1, position, position + 1)
        if 0 <= neighbor < len(events)
    }
    return tuple(
        event
        for position, event in enumerate(events)
        if position in positions and event.role.lower() in {"assistant", "tool"}
    )
```

`build_prompt()` 使用 `case.issue_statement` 和 `search_context(case.events)`。prompt 中必须原样
写入以下正反示例和输出契约：

```python
context = search_context(case.events)
rendered_context = [rendered for event in context if (rendered := _render_context(event))]
search_hints = [event.index for event in case.events if is_search_event(event)]
return f"""You create one semantic code-search query for a Java repository.

Prompt version: {prompt_version}
Issue statement:
{case.issue_statement}

Search-related assistant/tool trace windows:
{chr(10).join(rendered_context) or "(none)"}

Search event hints: {search_hints or "(none)"}

A good semantic query describes behavior, responsibility, state transitions, side effects,
performance impact, or a failure mechanism. Relevant code should not be obtainable merely by
copying a path, class, method, reference, implementation, or call relation from the trace.

Good example:
Find the logic that can leave navigation state inconsistent when switching between
cursor-based and page-based access.
Why: it describes a state transition and failure mode without revealing code identifiers.

Good example:
Find functions whose behavior can affect disk performance.
Why: relevant code may say I/O, buffering, flushing, persistence, synchronization, or storage
without containing the words disk performance.

Bad examples:
Find Java files that reference PageRequest.
List Java files containing getDoubleEvaluation.
Find implementations of LoadBalance.
Locate calls to isPoolLifo.
Search for RetryUtils.java.

Return one JSON object only:
{{"status":"valid","query":"...","reason":"..."}}
If no semantic query can be supported by the issue and trace, return:
{{"status":"invalid trace"}}
Do not return files, functions, answers, shell commands, code fences, or multiple queries.
""".strip()
```

- [ ] **Step 5: 实现单 query 响应解析和轻量校验**

新增固定模式和 helper：

```python
_DIRECT_LOOKUP = re.compile(
    r"\b(?:files?\s+(?:that\s+)?(?:contain|reference)|implementations?\s+of|"
    r"calls?\s+to|(?:class|method)\s+named)\b",
    re.IGNORECASE,
)


def _parse_result(raw: str | None) -> tuple[_GeneratedQuery | None, str | None]:
    if not isinstance(raw, str):
        return None, "generator_failed"
    payload = _json_payload(raw)
    if not isinstance(payload, Mapping):
        return None, "invalid_model_json"
    status = str(payload.get("status") or "").strip().lower().replace("_", " ")
    if status == "invalid trace":
        return None, "non_semantic_query"
    query = payload.get("query")
    reason = payload.get("reason")
    if status != "valid" or not isinstance(query, str) or not isinstance(reason, str):
        return None, "invalid_model_json"
    query = query.strip()
    reason = reason.strip()
    if not query or not reason:
        return None, "invalid_model_json"
    return _GeneratedQuery(query, reason), None


def _query_error(query: str, answer: Sequence[CodeLocation]) -> str | None:
    if "\n" in query or "`" in query or re.search(r"(?:^|\s)/[^\s]+", query):
        return "non_semantic_query"
    if re.match(r"\s*(?:rg|grep|find|git\s+grep)\b", query, re.IGNORECASE):
        return "non_semantic_query"
    if re.search(r"\b[\w./-]+\.java\b", query, re.IGNORECASE) or _DIRECT_LOOKUP.search(query):
        return "non_semantic_query"
    identifiers = {
        identifier
        for location in answer
        for identifier in (PurePosixPath(location.file).stem, *location.functions)
        if identifier
    }
    for identifier in identifiers:
        # Case-sensitive matching lets ordinary prose say "navigation" while still rejecting
        # an exact Java identifier such as ``Navigation`` or ``afterCursor``.
        if re.search(rf"(?<![\w$]){re.escape(identifier)}(?![\w$])", query):
            return "query_leaks_gold_identifier"
    return None
```

补充 `PurePosixPath` 导入。保留 `_json_payload()` 对 JSON code fence 的兼容，因为这只是响应
格式容错，不是第二次模型调用。

- [ ] **Step 6: 实现 `mine_query()`，保证一次调用和稳定 skip reason**

```python
def mine_query(
    case: TraceCase,
    generator: QueryGenerator,
    *,
    prompt_version: str = "semantic-query-v1",
) -> MiningOutcome:
    if case.gold_error:
        return MiningOutcome(None, case.gold_error)
    if not case.answer:
        return MiningOutcome(None, "no_production_java_gold")
    context = search_context(case.events)
    if not context:
        return MiningOutcome(None, "no_search_events")

    prompt = build_prompt(case, prompt_version=prompt_version)
    generated, error = _parse_result(generator.generate(prompt))
    if generated is None:
        return MiningOutcome(None, error)
    if error := _query_error(generated.query, case.answer):
        return MiningOutcome(None, error)

    indices = tuple(event.index for event in context)
    provenance: dict[str, object] = {
        "prompt_version": prompt_version,
        "prompt": prompt,
        "query_reason": generated.reason,
        "search_event_indices": [event.index for event in case.events if is_search_event(event)],
    }
    if model := getattr(generator, "model", None):
        provenance["model"] = model
    return MiningOutcome(
        PreparedQuery(
            query_id=case.trajectory_id,
            repo=case.repo,
            instance_id=case.instance_id,
            trajectory_id=case.trajectory_id,
            issue_statement=case.issue_statement,
            query=generated.query,
            answer=case.answer,
            source_event_indices=indices,
            strategy="semantic-generated",
            source_events=case.events,
            provenance=provenance,
        )
    )
```

更新 `__all__` 为 `MiningOutcome`、`OpenAIQueryGenerator`、`QueryGenerator`、`build_prompt`、
`is_search_event`、`mine_query`、`search_context`。不要保留旧 `mine_queries()` 兼容层，因为当前
仓库内调用点会在本任务一起迁移。

- [ ] **Step 7: 更新硬编码 mining 入口和 dry-run**

在 `scripts/mine_trace_queries.py` 修改：

```python
OUTPUT = f"{OUTPUT_DIR}/codesense-semantic-query.jsonl"
PROMPT_VERSION = "semantic-query-v1"
```

将循环改为一次 case 一次 outcome，并汇总原因：

```python
from collections import Counter

skip_reasons: Counter[str] = Counter()
written = 0
with output_path.open("w", encoding="utf-8") as output:
    for case in iter_jsonl(input_path):
        if LIMIT and processed >= LIMIT:
            break
        if DRY_RUN:
            rows = _prompt_rows(case, PROMPT_VERSION)
            if not rows:
                skip_reasons[case.gold_error or "no_search_events"] += 1
        else:
            outcome = mine_query(case, generator, prompt_version=PROMPT_VERSION)
            rows = [outcome.query.to_dict()] if outcome.query is not None else []
            if outcome.skip_reason:
                skip_reasons[outcome.skip_reason] += 1
        for row in rows:
            output.write(json.dumps(row, ensure_ascii=False) + "\n")
            written += 1
        processed += 1
print(json.dumps({"processed": processed, "written": written, "skipped": skip_reasons}))
```

`_prompt_rows()` 改用 `search_context()`，只有 `case.gold_error is None`、`case.answer` 非空且存在
context 时返回一条 prompt 记录。实现为：

```python
def _prompt_rows(case, prompt_version: str) -> list[dict[str, object]]:
    from evaluation.query_mining import build_prompt, is_search_event, search_context

    context = search_context(case.events)
    if case.gold_error or not case.answer or not context:
        return []
    return [
        {
            "type": "prompt",
            "repo": case.repo,
            "instance_id": case.instance_id,
            "trajectory_id": case.trajectory_id,
            "search_event_indices": [
                event.index for event in case.events if is_search_event(event)
            ],
            "source_event_indices": [event.index for event in context],
            "prompt": build_prompt(case, prompt_version=prompt_version),
        }
    ]
```

prompt record 不序列化 patch 文本或 `answer`。

- [ ] **Step 8: 运行 Task 2 定向测试和 lint**

```bash
conda run -n codesearch pytest -q tests/unit/evaluation/test_models.py tests/unit/evaluation/test_query_mining.py
conda run -n codesearch ruff check evaluation/models.py evaluation/query_mining.py scripts/mine_trace_queries.py tests/unit/evaluation/test_models.py tests/unit/evaluation/test_query_mining.py
conda run -n codesearch ruff format --check evaluation/models.py evaluation/query_mining.py scripts/mine_trace_queries.py tests/unit/evaluation/test_models.py tests/unit/evaluation/test_query_mining.py
```

Expected: 全部 PASS；测试确认 generator 每个 case 只被调用一次。

- [ ] **Step 9: 只提交 semantic mining 相关文件**

```bash
git add evaluation/models.py evaluation/query_mining.py scripts/mine_trace_queries.py tests/unit/evaluation/test_models.py tests/unit/evaluation/test_query_mining.py
git commit -m "feat: mine one semantic query per Java trace"
```

---

### Task 3: 将 query viewer 改为动态读取 JSONL 的本地服务

> 2026-09-14 范围调整：用户确认不新增 `scripts/query_viewer.py`，直接让
> `evaluation/query_viewer.py` 可执行。原先“一次生成静态 HTML”的步骤由以下验收契约替代：
> 每个 `/api/cases` 请求重新读取 query JSONL，浏览器每 2 秒轮询；页面展示顶层 issue、query、
> query reason、answer 和高亮 source events；服务只绑定 `127.0.0.1`。实现与测试仍限制在本
> Task 的两个文件中。

**Files:**
- Modify: `evaluation/query_viewer.py`
- Test: `tests/unit/evaluation/test_query_viewer.py`

**Interfaces:**
- Consumes: `PreparedQuery.to_dict()` 的顶层 `issue_statement`、`query`、`answer`、`source_event_indices`、`source_events`、`provenance.query_reason`。
- Produces: `render_query_viewer(records: Sequence[Mapping[str, object]]) -> str`，输出安全转义的独立 HTML。

- [ ] **Step 1: 用新 schema 重写 viewer fixture 和失败断言**

把 `tests/unit/evaluation/test_query_viewer.py` 的 record 改成：

```python
record = {
    "query_id": "trace-1",
    "repo": "example/repo",
    "instance_id": "example__repo-1",
    "trajectory_id": "trace-1",
    "issue_statement": "Navigation can retain stale state after changing access mode.",
    "query": (
        "Find the logic that can leave navigation state inconsistent when switching "
        "between cursor-based and page-based access."
    ),
    "answer": [
        {"file": "src/main/java/example/Navigation.java", "functions": ["afterCursor"]}
    ],
    "source_event_indices": [1, 2, 3],
    "provenance": {"query_reason": "State transition and failure mode."},
    "source_events": [
        {"index": 1, "role": "assistant", "text": "I will inspect navigation state."},
        {"index": 2, "role": "assistant", "tool_name": "search", "tool_input": "grep cursor src"},
        {"index": 3, "role": "tool", "tool_output": "src/main/java/example/Navigation.java"},
        {"index": 4, "role": "assistant", "text": "Now edit the implementation."},
    ],
}
```

断言页面包含 issue、唯一 query、query reason、patch answer；只有事件 1/2/3 的
`data-search-event="true"`，事件 4 不高亮；页头显示 `1 条 trace · 1 条语义 query`。

- [ ] **Step 2: 运行 viewer 测试，确认旧 nested searches 渲染失败**

```bash
conda run -n codesearch pytest -q tests/unit/evaluation/test_query_viewer.py
```

Expected: FAIL，页面缺少顶层 query、issue 或事件高亮不符合新 schema。

- [ ] **Step 3: 修改 renderer，只渲染一个 query 和一组答案**

在 `render_query_viewer()` 中删除 `record.get("searches")` 遍历，改为：

```python
query_count = sum(bool(_text(record.get("query"))) for record in records)
for case_number, record in enumerate(records, 1):
    case_id = f"case-{case_number}"
    repo = _text(record.get("repo"), "unknown repository")
    instance_id = _text(record.get("instance_id"), "unknown instance")
    case_links.append(f'<a href="#{case_id}"><strong>{repo}</strong><span>{instance_id}</span></a>')
    cases.append(_render_case(record, case_id, case_number))
```

将 `_render_case()` 签名改为：

```python
def _render_case(record: Mapping[str, object], case_id: str, case_number: int) -> str:
```

其核心数据读取固定为：

```python
selected = set(_integers(record.get("source_event_indices")))
events = _mappings(record.get("source_events"))
rendered_events = [_render_event(event, selected) for event in events]
provenance = record.get("provenance")
reason = provenance.get("query_reason") if isinstance(provenance, Mapping) else ""
```

HTML 主体按以下结构生成，顺序固定为 `Issue statement`、`Semantic query`、
`Why semantic`、`Patch ground truth`、`Original trace`：

```python
return f"""<section class="case" id="{case_id}">
  <div class="case-head"><div><span class="pill">Case {case_number}</span>
    <h2>{_text(record.get("repo"), "unknown repository")}</h2>
    <div class="meta">instance: {_text(record.get("instance_id"), "unknown")}<br>
    trajectory: {_text(record.get("trajectory_id"), "unknown")}</div></div>
    <div class="pill">{len(events)} events · 1 semantic query</div></div>
  <section class="section"><h3>Issue statement</h3>
    <pre>{_text(record.get("issue_statement"), "(missing issue)")}</pre></section>
  <section class="section"><h3>Semantic query</h3>
    <div class="query"><div class="query-head">
      <h4>{_text(record.get("query"), "(empty query)")}</h4></div></div></section>
  <section class="section"><h3>Why semantic</h3>
    <p>{_text(reason, "(missing reason)")}</p></section>
  <section class="section"><h3>Patch ground truth</h3>
    {_render_locations(record.get("answer"))}</section>
  <section class="section"><div class="section-title"><h3>Original trace</h3>
    <span class="pill">黄色为 query 构造事件</span></div>
    <div class="trace">{"".join(rendered_events) or '<p class="empty">没有事件。</p>'}</div>
  </section>
</section>"""
```

`_render_event()` 第二个参数改为 `selected: set[int]`，以
`index in selected` 决定黄色高亮和 `用于 query 构造` 标记。保留 `_text()` 的 `html.escape`
路径，任何 issue、query、trace、文件和函数都不得直接插入未转义 HTML。

- [ ] **Step 4: 运行静态 viewer 测试和 lint**

```bash
conda run -n codesearch pytest -q tests/unit/evaluation/test_query_viewer.py
conda run -n codesearch ruff check evaluation/query_viewer.py tests/unit/evaluation/test_query_viewer.py
conda run -n codesearch ruff format --check evaluation/query_viewer.py tests/unit/evaluation/test_query_viewer.py
```

Expected: 全部 PASS，包括恶意 `<script>` 内容保持 inert。

- [ ] **Step 5: 提交静态 viewer 迁移**

```bash
git add evaluation/query_viewer.py tests/unit/evaluation/test_query_viewer.py
git commit -m "feat: show semantic trace queries in the static viewer"
```

---

### Task 4: 让批量 evaluator 每条记录只执行一个 query

**Files:**
- Modify: `scripts/evaluation.py`
- Test: `tests/unit/test_evaluation_script.py`

**Interfaces:**
- Consumes: 顶层 `query: str`、`answer: list[{file, functions}]`、`source_event_indices: list[int]`。
- Produces: `_evaluate_query(project, record, *, routes, limit, include_test_files=False) -> dict[str, object]`；report 中 `cases` 一条输入对应一条 case result。

- [ ] **Step 1: 把 evaluator 测试 fixture 改成单 query schema**

把 `_evaluate_search` 测试改名并传入：

```python
record = {
    "query": "Find logic that can leave navigation state inconsistent.",
    "source_event_indices": [4, 5, 6],
    "answer": [{"file": "src/main/java/example/Client.java", "functions": ["send"]}],
}
result = module._evaluate_query(
    Project(), record, routes=("lexical", "planned", "codegen"), limit=20
)
```

断言三条 route 各调用一次、返回 `source_event_indices` 和 `answer`，fallback 不产生 metrics。

把 main 集成 fixture 改为一行一个 query：

```python
benchmark.write_text(
    json.dumps(
        {
            "query_id": "query-1",
            "repo": "owner/repo",
            "instance_id": "instance-1",
            "trajectory_id": "trajectory-1",
            "query": "Find behavior that can leave client state inconsistent.",
            "answer": [{"file": "src/Client.java", "functions": []}],
            "source_event_indices": [3],
        }
    )
    + "\n",
    encoding="utf-8",
)
```

断言 `len(report["cases"]) == 1`、viewer 只收到一个 key `"1"`，并且
`viewer.records[0]["evaluation"] == report["cases"][0]["evaluation"]`。

- [ ] **Step 2: 运行 evaluator 测试，确认 nested loop 造成失败**

```bash
conda run -n codesearch pytest -q tests/unit/test_evaluation_script.py
```

Expected: FAIL，缺少 `_evaluate_query` 或 main 没有读取顶层 query。

- [ ] **Step 3: 将 `_evaluate_search()` 改成顶层 `_evaluate_query()`**

实现使用最终字段名：

```python
def _evaluate_query(
    project: object,
    record: Mapping[str, object],
    *,
    routes: Sequence[str],
    limit: int,
    include_test_files: bool = False,
) -> dict[str, object]:
    query = str(record.get("query") or "").strip()
    answer = list(_mappings(record.get("answer")))
    usable_answer = [
        location
        for location in answer
        if include_test_files or not _is_test_file(str(location.get("file") or ""))
    ]
    base = {
        "query": query,
        "source_event_indices": list(_integers(record.get("source_event_indices"))),
        "answer": answer,
    }
    if not usable_answer:
        return {**base, "skipped": True, "skip_reason": "no production-code gold", "routes": {}}

    route_results: dict[str, object] = {}
    for route in routes:
        try:
            result = project.search(query, route=route, limit=limit)
            route_result = {
                "actual_route": result.route,
                "elapsed": result.elapsed,
                "notes": list(result.notes),
                "hits": [_hit_dict(hit) for hit in result.hits],
            }
            if result.route == route:
                route_result["metrics"] = _score(
                    result.hits, answer, include_test_files=include_test_files
                )
            else:
                route_result["error"] = f"requested {route} but search used {result.route}"
            route_results[route] = route_result
        except Exception as exc:  # noqa: BLE001 -- preserve other route results
            route_results[route] = {"error": f"{type(exc).__name__}: {exc}", "hits": []}
    return {**base, "skipped": False, "routes": route_results}
```

`_failed_search()` 同步改名为 `_failed_query()`，读取顶层 `query`、`answer` 和
`source_event_indices`。

- [ ] **Step 4: 去掉 main 的 nested `searches` 循环**

每条 input record 只构造一次 `evaluation`：

```python
if QUERY_LIMIT and evaluated >= QUERY_LIMIT:
    break
if repo in projects:
    evaluation = _evaluate_query(
        projects[repo],
        record,
        routes=ROUTES,
        limit=SEARCH_LIMIT,
        include_test_files=INCLUDE_TEST_FILES,
    )
else:
    evaluation = _failed_query(record, ROUTES, project_errors.get(repo, "project unavailable"))

case_result = {
    "query_id": record.get("query_id"),
    "repo": repo,
    "instance_id": record.get("instance_id"),
    "trajectory_id": record.get("trajectory_id"),
    "evaluation": evaluation,
}
cases.append(case_result)
if viewer_active and viewer is not None:
    viewer_active = _publish_viewer(viewer, {"key": str(case_number), **case_result})
evaluated += not evaluation.get("skipped", False)
```

`_summarize()` 改为从 `_mapping(case.get("evaluation"))` 直接读取 route：

```python
results = [
    _mapping(_mapping(_mapping(case.get("evaluation")).get("routes")).get(route))
    for case in cases
    if not _mapping(case.get("evaluation")).get("skipped")
]
```

保留 `_score()` 的文件/函数语义不变；当 `gold_functions` 为空时函数 Precision/Recall 仍为
`None`。不要在这一任务修改项目下载、索引或 viewer 生命周期。

- [ ] **Step 5: 运行 evaluator 测试和 lint**

```bash
conda run -n codesearch pytest -q tests/unit/test_evaluation_script.py
conda run -n codesearch ruff check scripts/evaluation.py tests/unit/test_evaluation_script.py
conda run -n codesearch ruff format --check scripts/evaluation.py tests/unit/test_evaluation_script.py
```

Expected: 全部 PASS；测试确认每条 JSONL 只发布一次 viewer event。

- [ ] **Step 6: 提交 evaluator schema 迁移**

```bash
git add scripts/evaluation.py tests/unit/test_evaluation_script.py
git commit -m "feat: evaluate flat semantic query records"
```

---

### Task 5: 让实时 viewer 展示顶层 answer 和唯一 query

**Files:**
- Modify: `evaluation/live_results.py`
- Test: `tests/unit/evaluation/test_live_results.py`

**Interfaces:**
- Consumes: Task 4 发布的 `{key, query_id, repo, instance_id, trajectory_id, evaluation}`；`evaluation` 包含 `query`、`answer`、`routes`。
- Produces: 现有 `LiveEvaluationViewer` HTTP/SSE API 不变；页面正确渲染扁平 schema。

- [ ] **Step 1: 更新实时页面 contract 测试**

在 `tests/unit/evaluation/test_live_results.py` 保留 store/SSE/HTTP 测试不变，只将示例 record
从 `search` 改成 `evaluation`：

```python
record = {
    "key": "1",
    "query_id": "trace-1",
    "repo": "owner/repo",
    "evaluation": {
        "query": "Find logic that can leave navigation state inconsistent.",
        "answer": [{"file": "src/main/java/Navigation.java", "functions": ["switchMode"]}],
        "routes": {},
    },
}
```

在页面 contract 测试中增加：

```python
assert "record.evaluation" in page
assert "evaluation.answer" in page
assert "search.answers" not in page
```

- [ ] **Step 2: 运行 live viewer 测试，确认旧 JS 字段导致失败**

```bash
conda run -n codesearch pytest -q tests/unit/evaluation/test_live_results.py
```

Expected: FAIL，页面仍读取 `record.search` 或 `search.answers`。

- [ ] **Step 3: 只迁移 embedded JavaScript 的数据字段**

保留 `LiveResultStore`、HTTP handler、SSE 和生命周期实现不变。将页面 helper 改成：

```javascript
function rawAnswers(evaluation) {
  const rows = [];
  sequence(evaluation.answer).forEach(answer => {
    const file = String(answer.file || 'unknown file');
    rows.push(element('div', 'row neutral', file));
    sequence(answer.functions).forEach(name =>
      rows.push(element('div', 'row neutral', file + ' :: ' + name)));
  });
  return rows;
}
```

`renderRoute` 和 `renderRecord` 使用统一的 `evaluation` 变量：

```javascript
function renderRoute(evaluation, routeName, rawResult) {
  const routeResult = mapping(rawResult);
  const metrics = mapping(routeResult.metrics);
  const section = element('section', 'route');
  section.append(element('h3', '', routeName));
  const actual = routeResult.actual_route ? 'actual: ' + routeResult.actual_route : 'not completed';
  section.append(element('div', 'route-meta', actual + ' · ' + metric(routeResult.elapsed) + 's'));
  if (routeResult.error) section.append(element('div', 'error', routeResult.error));
  renderMetrics(section, metrics);
  section.append(element('div', 'block-title', 'Answer diff'));
  appendRows(section, Object.keys(metrics).length ? goldDiff(metrics) : rawAnswers(evaluation),
    '没有标准答案');
  section.append(element('div', 'block-title', 'Search hits'));
  appendRows(section, hitRows(routeResult, metrics), '没有搜索结果');
  return section;
}

function renderRecord(record) {
  if (!record || seen.has(record.key)) return;
  seen.add(record.key);
  const evaluation = mapping(record.evaluation);
  const card = element('article', 'card');
  const head = element('div', 'card-head');
  head.append(element('div', 'eyebrow', String(record.repo || '') + ' · ' +
    String(record.instance_id || '') + ' · query ' + String(record.key || '')));
  head.append(element('h2', '', evaluation.query || '(empty query)'));
  card.append(head);
  const answers = element('section', 'answers');
  answers.append(element('h3', '', 'Gold answers'));
  appendRows(answers, rawAnswers(evaluation), '没有标准答案');
  card.append(answers);
  const routes = element('div', 'routes');
  const values = mapping(evaluation.routes);
  const names = routeOrder.length ? routeOrder : Object.keys(values);
  names.forEach(name => routes.append(renderRoute(evaluation, name, values[name])));
  card.append(routes);
  document.getElementById('records').append(card);
  document.getElementById('progress').textContent = '已完成 ' + seen.size + ' 条 query';
}
```

上面注释所指的是原函数中已经存在的 DOM 代码，原样保留，不新建 HTML 字符串注入路径。
继续只用 `textContent`，不得引入 `innerHTML`。

- [ ] **Step 4: 运行 live viewer 测试和 lint**

```bash
conda run -n codesearch pytest -q tests/unit/evaluation/test_live_results.py
conda run -n codesearch ruff check evaluation/live_results.py tests/unit/evaluation/test_live_results.py
conda run -n codesearch ruff format --check evaluation/live_results.py tests/unit/evaluation/test_live_results.py
```

Expected: 全部 PASS，HTTP/SSE 行为和安全 DOM 断言保持不变。

- [ ] **Step 5: 提交实时 viewer 迁移**

```bash
git add evaluation/live_results.py tests/unit/evaluation/test_live_results.py
git commit -m "feat: stream flat semantic query evaluations"
```

---

### Task 6: 增加端到端 fixture、更新说明并完成全仓验证

**Files:**
- Create: `tests/unit/evaluation/test_semantic_trace_pipeline.py`
- Modify: `evaluation/README.md`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Consumes: Task 1–5 的 adapter、`mine_query()`、扁平序列化和 evaluator schema。
- Produces: 一个不调用真实网络/LLM 的 trace → semantic query JSON fixture 验收；面向手动实验的最终文档。

- [ ] **Step 1: 写完整的无网络 pipeline fixture**

创建 `tests/unit/evaluation/test_semantic_trace_pipeline.py`：

```python
import json

from evaluation.query_mining import mine_query
from evaluation.trace_adapters.open_swe_traces import normalize_record


class _Generator:
    model = "fixture-model"

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return json.dumps(
            {
                "status": "valid",
                "query": (
                    "Find the logic that can leave navigation state inconsistent when "
                    "switching between cursor-based and page-based access."
                ),
                "reason": "It describes a state transition failure without code identifiers.",
            }
        )


def test_resolved_trace_becomes_one_semantic_query_with_hidden_patch_gold() -> None:
    record = {
        "repo": "owner/repo",
        "language": "java",
        "instance_id": "issue-1",
        "trajectory_id": "trace-1",
        "resolved": 1,
        "trajectory": [
            {"role": "user", "content": "Switching navigation modes can retain stale state."},
            {"role": "assistant", "content": "I will inspect navigation transitions."},
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_call",
                        "name": "rg",
                        "arguments": {"pattern": "cursor", "path": "src/main/java"},
                    }
                ],
            },
            {"role": "tool", "name": "rg", "content": "src/main/java/Navigation.java"},
        ],
        "metadata": {
            "reference_patch": {
                "patch": """diff --git a/src/main/java/Navigation.java b/src/main/java/Navigation.java
--- a/src/main/java/Navigation.java
+++ b/src/main/java/Navigation.java
@@ -10 +10 @@ public PageRequest afterCursor(Cursor cursor) {
-return withCursor(cursor);
+return withCursor(cursor).withoutPage();
"""
            }
        },
    }
    case = normalize_record(record)
    generator = _Generator()

    outcome = mine_query(case, generator)

    assert outcome.skip_reason is None
    assert outcome.query is not None
    payload = outcome.query.to_dict()
    assert payload["query_id"] == "trace-1"
    assert payload["answer"] == [
        {"file": "src/main/java/Navigation.java", "functions": ["afterCursor"]}
    ]
    assert payload["source_event_indices"] == [1, 2, 3]
    assert len(generator.prompts) == 1
    assert "withoutPage" not in generator.prompts[0]
    assert "diff --git" not in generator.prompts[0]
```

- [ ] **Step 2: 运行完整 evaluation 测试集合**

```bash
conda run -n codesearch pytest -q tests/unit/evaluation tests/unit/test_evaluation_script.py
```

Expected: 全部 PASS。若 fixture 暴露接口不一致，只修改 Task 1–5 已定义的字段和调用点，不增加
旧 schema 兼容分支。

- [ ] **Step 3: 更新实验 README**

将 `evaluation/README.md` 开头的旧 multi-search 说明替换为：

```markdown
当前 query mining 只读取严格满足 `resolved == 1` 的 Open-SWE-Traces Java 记录。一条 trace
最多调用一次 LLM，并产生一条行为/职责/失效机制导向的英语语义 query。模型输入由完整原始
issue 和每个搜索事件前一条、当前、后一条 assistant/tool 事件组成；reference patch 不进入
prompt。

输出使用扁平 schema：顶层 `query` 是传给 `Project.search()` 的唯一查询，顶层 `answer`
来自 reference patch 中被修改的既有生产 Java 文件和可确定函数。新增、删除、测试文件不进入
主答案；函数无法确定时保留文件并令 `functions` 为空。
```

把运行说明中的输出名改为 `codesense-semantic-query.jsonl`；批量搜索部分改成“一条 JSONL
记录运行一次 query”，保留硬编码参数、项目缓存、Top-20、viewer 和 route fallback 说明。

- [ ] **Step 4: 在 CHANGELOG 顶部记录完成的功能**

在 `CHANGELOG.md` 标题后增加：

```markdown
## 2026-09-14 — Open-SWE-Traces 语义检索 Benchmark

- resolved Java trace 改为一条轨迹生成一条行为/职责/失效机制导向的英语语义 query；prompt
  使用完整 issue 和搜索事件局部窗口，并加入语义 query 正反 few-shot。
- reference patch 在程序侧提取被修改的既有生产 Java 文件和可确定函数作为隐藏答案；新增、
  删除和测试文件不进入主 gold，LLM 不再生成答案。
- query 数据、批量 evaluator、静态审计页和实时结果页统一迁移到顶层 `query + answer` 扁平
  schema。
```

- [ ] **Step 5: 运行新增集成测试、全仓测试和三个门禁**

```bash
conda run -n codesearch pytest -q tests/unit/evaluation/test_semantic_trace_pipeline.py
conda run -n codesearch ruff check .
conda run -n codesearch ruff format --check .
conda run -n codesearch pytest
```

Expected: 四条命令全部退出 0。若全仓存在与本功能无关的既有失败，保存失败测试名和完整错误，
同时再次运行下面的受影响集合确认本功能为绿：

```bash
conda run -n codesearch pytest -q tests/unit/evaluation tests/unit/test_evaluation_script.py
```

- [ ] **Step 6: 检查 schema 残留和 prompt 泄漏**

```bash
rg -n 'record\.get\("searches"\)|record\.get\("final_answer"\)|search\.answers|searches\[\]' evaluation scripts tests/unit/evaluation tests/unit/test_evaluation_script.py
rg -n 'reference_patch|diff --git' evaluation/query_mining.py
git diff --check
```

Expected: 第一条在现役实现和相应测试中无旧 schema 命中；第二条不显示任何把 patch 内容插入
prompt 的代码（校验参数或注释提及该名称可以人工确认）；`git diff --check` 无输出。

- [ ] **Step 7: 只提交集成测试和文档**

```bash
git add tests/unit/evaluation/test_semantic_trace_pipeline.py evaluation/README.md CHANGELOG.md
git commit -m "docs: finalize semantic trace benchmark workflow"
```

- [ ] **Step 8: 核对最终提交和工作区，不误报无关改动**

```bash
git log --oneline -6
git status --short
```

Expected: 日志包含本计划六个功能提交；`git status` 中可以仍有用户原先的无关改动，但本计划
列出的 Python、测试和文档文件不应残留未提交的本功能修改。交付说明必须分别列出全仓门禁
结果、定向测试结果和未触碰的既有工作区改动。
