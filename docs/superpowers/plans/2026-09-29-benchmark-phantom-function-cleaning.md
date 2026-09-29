# Benchmark 幽灵函数清洗 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让挖掘产出的 benchmark 中，每个答案只保留在其 base_commit 版本里真实声明过的函数（含构造器），文件一律保留，从根源消除 `function_recall` 分母里的幽灵符号（如 `unquote`）。

**Architecture:** 新增 `evaluation/repo_cache.py`（评测与挖掘共享的、按 `{repo}__{commit}` 隔离的仓库检出解析器）与 `evaluation/answer_cleaning.py`（用 tree-sitter Java 扫描器判定函数是否声明、清洗答案）。清洗作为一步编排进 `scripts/mine_trace_queries.py`，一趟产出单一干净 benchmark；`scripts/evaluation.py` 改用共享解析器，评分逻辑不变。

**Tech Stack:** Python 3.11、tree-sitter（`codesense.lang.java.scanner.JavaDeclarationScanner`）、pytest、ruff、git。

**Spec:** `docs/superpowers/specs/2026-09-29-benchmark-phantom-function-cleaning-design.md`

## Global Constraints

- 运行任何命令前先激活 conda 环境 `codesearch`（`conda activate codesearch`）；测试用 `pytest`。
- LLM key 只从环境变量 `CODESENSE_API_KEY` 读，不写进代码/配置。
- 禁止硬编码机器相关绝对路径（AGENTS.md 规则 5）：脚本常量用 `_ROOT = Path(__file__).resolve().parents[1]` 推导。
- `scripts/` 只做编排、不写业务逻辑（规则 6）；业务逻辑放 `evaluation/`。
- 模块顶层只有定义、不执行 I/O（规则 7）。
- 每个 Task 结束前跑通相关测试；整个 plan 结束前 `ruff check .`、`ruff format --check .`、`pytest` 三关全绿。
- 提交信息用 Conventional Commits（`feat:` / `test:` / `refactor:` / `docs:`）。
- 函数名归一化口径固定为：`str(v or "").strip().split("(", 1)[0].rsplit(".", 1)[-1]`（与 `scripts/evaluation.py::_function_name` 一致）。
- "已声明函数" = Java 扫描器 `Declaration.kind ∈ {"method", "constructor"}`。

## File Structure

- **Create** `evaluation/repo_cache.py` — 共享仓库解析器：`resolve_repo`、`_checkout`、`_REPOSITORY`、`DEFAULT_PROJECTS_DIR`。
- **Create** `evaluation/answer_cleaning.py` — 答案清洗：`normalize_function_name`、`declared_functions`、`clean_record`、`count_answer_functions`、`_CALLABLE_KINDS`、`_ANSWER_FIELDS`。
- **Modify** `scripts/evaluation.py` — 删 `_project_path`/`_checkout`/`_REPOSITORY`，改用 `resolve_repo`；常量去绝对路径。
- **Modify** `scripts/mine_trace_queries.py` — `_run_cases` 内编排清洗；常量去绝对路径。
- **Create** `tests/unit/evaluation/test_repo_cache.py` — 解析器测试（含从 `test_evaluation_script.py` 迁移并改名的 4 个用例）。
- **Create** `tests/unit/evaluation/test_answer_cleaning.py` — 清洗测试。
- **Modify** `tests/unit/test_evaluation_script.py` — 删除已迁移的 4 个 `_project_path` 测试。
- **Modify** `tests/unit/test_mine_trace_queries_script.py` — 新增挖掘清洗集成测试。

---

### Task 1: 共享仓库解析器 `evaluation/repo_cache.py`

**Files:**
- Create: `evaluation/repo_cache.py`
- Test: `tests/unit/evaluation/test_repo_cache.py`

**Interfaces:**
- Consumes: 无（纯标准库）。
- Produces:
  - `resolve_repo(repo: str, base_commit: str | None = None, projects_dir: Path = DEFAULT_PROJECTS_DIR, *, manual_paths: Mapping[str, str] | None = None, run: Callable[..., object] = subprocess.run) -> Path`
  - `DEFAULT_PROJECTS_DIR: Path`

- [ ] **Step 1: 写失败测试**

创建 `tests/unit/evaluation/test_repo_cache.py`：

```python
"""Behavior tests for the shared repository checkout cache."""

from __future__ import annotations

from pathlib import Path

import pytest

from evaluation.repo_cache import resolve_repo


def _fake_run(calls: list[list[str]]):
    def run(command: list[str], *, check: bool) -> None:
        assert check is True
        calls.append(command)
        if command[1] == "clone":
            Path(command[-1]).mkdir(parents=True)
    return run


def test_manual_mapping_wins_and_is_never_checked_out(tmp_path: Path) -> None:
    manual = tmp_path / "manual"
    manual.mkdir()
    calls: list[list[str]] = []

    found = resolve_repo(
        "owner/repo", "abc123", tmp_path / "projects",
        manual_paths={"owner/repo": str(manual)}, run=_fake_run(calls),
    )

    assert found == manual.resolve()
    assert calls == []


def test_invalid_repository_name_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        resolve_repo("not-a-repo", None, tmp_path / "projects")


def test_missing_repo_without_commit_is_shallow_cloned_plain_name(tmp_path: Path) -> None:
    projects = tmp_path / "projects"
    calls: list[list[str]] = []

    found = resolve_repo("owner/repo", None, projects, run=_fake_run(calls))

    assert found == (projects / "owner__repo").resolve()
    assert calls == [[
        "git", "clone", "--depth", "1",
        "https://github.com/owner/repo.git", str(projects / "owner__repo"),
    ]]


def test_missing_repo_with_commit_is_full_cloned_then_checked_out(tmp_path: Path) -> None:
    projects = tmp_path / "projects"
    target = projects / "owner__repo__abc123"
    calls: list[list[str]] = []

    found = resolve_repo("owner/repo", "abc123", projects, run=_fake_run(calls))

    assert found == target.resolve()
    assert calls == [
        ["git", "clone", "https://github.com/owner/repo.git", str(target)],
        ["git", "-C", str(target), "checkout", "abc123"],
    ]


def test_existing_clone_with_commit_is_fetched_and_checked_out(tmp_path: Path) -> None:
    projects = tmp_path / "projects"
    target = projects / "owner__repo__abc123"
    target.mkdir(parents=True)
    calls: list[list[str]] = []

    found = resolve_repo("owner/repo", "abc123", projects, run=_fake_run(calls))

    assert found == target.resolve()
    assert calls == [
        ["git", "-C", str(target), "fetch", "--depth", "1", "origin", "abc123"],
        ["git", "-C", str(target), "checkout", "abc123"],
    ]
    assert not any(command[1] == "clone" for command in calls)


def test_two_commits_of_one_repo_get_distinct_directories(tmp_path: Path) -> None:
    projects = tmp_path / "projects"
    calls: list[list[str]] = []
    run = _fake_run(calls)

    first = resolve_repo("owner/repo", "c1", projects, run=run)
    second = resolve_repo("owner/repo", "c2", projects, run=run)

    assert first != second
    assert first == (projects / "owner__repo__c1").resolve()
    assert second == (projects / "owner__repo__c2").resolve()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/unit/evaluation/test_repo_cache.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'evaluation.repo_cache'`

- [ ] **Step 3: 写最小实现**

创建 `evaluation/repo_cache.py`：

```python
"""Shared repository checkout cache for the evaluation pipeline.

Both the miner and the evaluator need a working tree pinned to a specific
``base_commit``. Clones are cached under ``evaluation/projects`` and named
``{owner}__{repo}__{commit}`` so two revisions of one repository never share
(and overwrite) a single working tree.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path

__all__ = ["DEFAULT_PROJECTS_DIR", "resolve_repo"]

#: Repository root is the parent of this package directory.
_REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROJECTS_DIR = _REPO_ROOT / "evaluation" / "projects"

_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def resolve_repo(
    repo: str,
    base_commit: str | None = None,
    projects_dir: Path = DEFAULT_PROJECTS_DIR,
    *,
    manual_paths: Mapping[str, str] | None = None,
    run: Callable[..., object] = subprocess.run,
) -> Path:
    """Return a working tree for ``repo`` pinned to ``base_commit``.

    A manual path wins and is never checked out (moving a user-managed HEAD is
    destructive). Otherwise a cached clone under ``projects_dir`` is reused and
    moved onto ``base_commit``; when absent the repository is cloned -- in full
    when a commit is required, shallow otherwise. The directory name carries the
    commit so distinct revisions stay isolated.
    """
    manual = (manual_paths or {}).get(repo)
    if manual:
        path = Path(manual).expanduser().resolve()
        if not path.is_dir():
            raise NotADirectoryError(path)
        return path
    if not _REPOSITORY.fullmatch(repo):
        raise ValueError(f"invalid GitHub repository name: {repo!r}")

    projects_dir.mkdir(parents=True, exist_ok=True)
    name = repo.replace("/", "__")
    if base_commit:
        name = f"{name}__{base_commit}"
    target = projects_dir / name
    url = f"https://github.com/{repo}.git"
    if target.is_dir():
        if base_commit:
            _checkout(run, target, base_commit)
        return target.resolve()
    if base_commit:
        # A shallow clone of the default branch cannot reach an arbitrary
        # commit, so clone in full and then check the exact revision out.
        run(["git", "clone", url, str(target)], check=True)
        run(["git", "-C", str(target), "checkout", base_commit], check=True)
    else:
        run(["git", "clone", "--depth", "1", url, str(target)], check=True)
    if not target.is_dir():
        raise RuntimeError(f"git clone did not create {target}")
    return target.resolve()


def _checkout(run: Callable[..., object], target: Path, commit: str) -> None:
    """Move an existing clone onto ``commit``, fetching it if it is not local."""
    run(["git", "-C", str(target), "fetch", "--depth", "1", "origin", commit], check=True)
    run(["git", "-C", str(target), "checkout", commit], check=True)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/unit/evaluation/test_repo_cache.py -v`
Expected: PASS（6 个用例全绿）

- [ ] **Step 5: 提交**

```bash
git add evaluation/repo_cache.py tests/unit/evaluation/test_repo_cache.py
git commit -m "feat: add shared commit-isolated repository resolver"
```

---

### Task 2: `scripts/evaluation.py` 改用共享解析器 + 去绝对路径

**Files:**
- Modify: `scripts/evaluation.py`（常量区 ~L18-50、`_project_path`@L249-291、`_checkout`@L293-297、调用点@L103-108）
- Modify: `tests/unit/test_evaluation_script.py`（删除 L25-104 的 4 个 `_project_path` 测试）

**Interfaces:**
- Consumes: `evaluation.repo_cache.resolve_repo`（Task 1）。
- Produces: 无新接口（内部重构，行为对外不变，仅 clone 目录名带 commit 后缀）。

- [ ] **Step 1: 先改测试——删除已迁移的 4 个 `_project_path` 测试**

在 `tests/unit/test_evaluation_script.py` 删除这 4 个函数（它们已在 Task 1 以新签名/新命名迁到 `test_repo_cache.py`）：
`test_project_path_prefers_manual_mapping`、`test_project_path_clones_missing_repository`、
`test_project_path_clones_full_and_checks_out_base_commit`、
`test_project_path_checks_out_base_commit_in_an_existing_clone`（约 L25-L104）。

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/unit/test_evaluation_script.py -v`
Expected: 收集/运行正常但此时 `_project_path` 仍存在——本步只验证删测试后其余用例仍 PASS（删除本身不制造红灯；红灯在 Step 4 由删除 `_project_path` 触发，若有残留引用会 FAIL）。

- [ ] **Step 3: 改 `scripts/evaluation.py`**

3a. 常量区去绝对路径。把 L18-30 的 `BENCHMARK`/`PROJECTS_DIR`/`INDEXES_DIR`/`OUTPUT` 改为基于 `_ROOT`：

```python
from pathlib import Path  # 已在文件顶部导入

_ROOT = Path(__file__).resolve().parents[1]
BENCHMARK = str(_ROOT / "outputs/open_swe_traces/codesense-semantic-query.jsonl")
PROJECTS_DIR = str(_ROOT / "evaluation/projects")
INDEXES_DIR = f"{PROJECTS_DIR}/.indexes"
OUTPUT = str(_ROOT / "outputs/open_swe_traces/codesense-evaluation.json")
```

3b. 顶部 import 区加：

```python
from evaluation.repo_cache import resolve_repo
```

3c. 删除 `_project_path`（L249-291）、`_checkout`（L293-297）、`_REPOSITORY`（L50）三处定义（`_checkout`/`_REPOSITORY` 已移入 `repo_cache.py`）。

3d. 调用点（原 L103-108）改为：

```python
                    root = resolve_repo(
                        repo,
                        base_commit,
                        Path(PROJECTS_DIR).expanduser(),
                        manual_paths=PROJECT_PATHS,
                    )
```

3e. 若删除 `_REPOSITORY`/`_checkout` 后 `import re` 或 `import subprocess` 在本文件不再被使用，则一并删除该 import（用 `ruff check` 判定，见 Step 4）。

- [ ] **Step 4: 运行测试 + lint 确认通过**

Run: `pytest tests/unit/test_evaluation_script.py -v && ruff check scripts/evaluation.py`
Expected: PASS；ruff 无 unused-import / undefined-name 报错。若有 `re`/`subprocess` unused，按 3e 删除后重跑。

- [ ] **Step 5: 全量回归 + 提交**

```bash
pytest -q && ruff check . && ruff format --check .
git add scripts/evaluation.py tests/unit/test_evaluation_script.py
git commit -m "refactor: evaluation reuses shared commit-isolated repo resolver"
```

---

### Task 3: `evaluation/answer_cleaning.py` —— 函数名归一化 + 已声明函数解析

**Files:**
- Create: `evaluation/answer_cleaning.py`
- Test: `tests/unit/evaluation/test_answer_cleaning.py`

**Interfaces:**
- Consumes: `codesense.lang.java.scanner.JavaDeclarationScanner`（`.for_java().scan(src) -> ScanResult`，`ScanResult.declarations: tuple[Declaration, ...]`，`Declaration.name: str`、`Declaration.kind: str`）。
- Produces:
  - `normalize_function_name(value: object) -> str`
  - `declared_functions(source_text: str) -> set[str]`
  - `_CALLABLE_KINDS = frozenset({"method", "constructor"})`、`_ANSWER_FIELDS = ("answer", "trace_answer", "candidate_answers")`

- [ ] **Step 1: 写失败测试**

创建 `tests/unit/evaluation/test_answer_cleaning.py`：

```python
"""Tests for answer cleaning: which functions a Java file really declares."""

from __future__ import annotations

from evaluation.answer_cleaning import declared_functions, normalize_function_name

_JAVA = """
package com.amihaiemil.eoyaml;

public final class ReadPlainScalarValue {
    private final YamlLine line;

    public ReadPlainScalarValue(final YamlLine line) {
        this.line = line;
    }

    public String value() {
        return this.line.trimmed();
    }

    void helper(int x) { }

    void helper(String s) { }

    static class Inner {
        void innerMethod() { }
    }
}
"""


def test_normalize_function_name_strips_params_and_qualifiers() -> None:
    assert normalize_function_name("com.foo.Bar.value(int x)") == "value"
    assert normalize_function_name("  value() ") == "value"
    assert normalize_function_name(None) == ""


def test_declared_functions_includes_methods_and_constructors() -> None:
    names = declared_functions(_JAVA)
    assert "value" in names
    assert "helper" in names                 # overloads collapse to one name
    assert "innerMethod" in names            # inner-class method via recursion
    assert "ReadPlainScalarValue" in names   # constructor counts


def test_declared_functions_excludes_fields_and_type_names() -> None:
    names = declared_functions(_JAVA)
    assert "line" not in names               # field, not callable
    assert "Inner" not in names              # nested class, not callable
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/unit/evaluation/test_answer_cleaning.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'evaluation.answer_cleaning'`

- [ ] **Step 3: 写最小实现**

创建 `evaluation/answer_cleaning.py`：

```python
"""Drop answer functions that do not exist in the mined-against revision.

Trace answers come from the fix diff and the agent's final message, so they
reference symbols the fix *adds* -- e.g. ``unquote`` -- which are absent from
the ``base_commit`` tree the benchmark is retrieved against. Such phantom
functions can never be hit, so counting them in ``function_recall`` unfairly
caps the metric. This module removes them while keeping the (existing) file.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from codesense.lang.java.scanner import JavaDeclarationScanner

__all__ = [
    "clean_record",
    "count_answer_functions",
    "declared_functions",
    "normalize_function_name",
]

#: Answer fields whose ``functions`` are validated. Files are never removed.
_ANSWER_FIELDS = ("answer", "trace_answer", "candidate_answers")
#: Declaration kinds that count as a callable a query could name.
_CALLABLE_KINDS = frozenset({"method", "constructor"})


def normalize_function_name(value: object) -> str:
    """Reduce a function reference to its simple name.

    Mirrors ``scripts/evaluation.py::_function_name`` so cleaned answers and
    scored hits compare on the same key: drop parameters, then any qualifier.
    """
    return str(value or "").strip().split("(", 1)[0].rsplit(".", 1)[-1]


def declared_functions(source_text: str) -> set[str]:
    """Normalized names of methods and constructors declared in one Java file."""
    result = JavaDeclarationScanner.for_java().scan(source_text)
    return {
        normalize_function_name(declaration.name)
        for declaration in result.declarations
        if declaration.kind in _CALLABLE_KINDS and declaration.name
    }
```

（`clean_record` / `count_answer_functions` 在 Task 4 补；`__all__` 先行声明不影响本 Task 测试。）

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/unit/evaluation/test_answer_cleaning.py -v`
Expected: PASS（3 个用例全绿）

- [ ] **Step 5: 提交**

```bash
git add evaluation/answer_cleaning.py tests/unit/evaluation/test_answer_cleaning.py
git commit -m "feat: parse declared Java methods and constructors for answer cleaning"
```

---

### Task 4: `clean_record` + `count_answer_functions`

**Files:**
- Modify: `evaluation/answer_cleaning.py`（追加两个函数与一个私有助手）
- Test: `tests/unit/evaluation/test_answer_cleaning.py`（追加用例）

**Interfaces:**
- Consumes: `declared_functions`、`normalize_function_name`（Task 3）。
- Produces:
  - `clean_record(record: Mapping[str, object], repo_root: Path) -> dict[str, object]`
  - `count_answer_functions(record: Mapping[str, object]) -> int`

- [ ] **Step 1: 写失败测试**

在 `tests/unit/evaluation/test_answer_cleaning.py` 追加：

```python
from pathlib import Path

from evaluation.answer_cleaning import clean_record, count_answer_functions


def _write_repo(tmp_path: Path) -> Path:
    pkg = tmp_path / "src/main/java/com/amihaiemil/eoyaml"
    pkg.mkdir(parents=True)
    (pkg / "ReadPlainScalarValue.java").write_text(
        "package com.amihaiemil.eoyaml;\n"
        "public final class ReadPlainScalarValue {\n"
        "    public String value() { return \"\"; }\n"
        "}\n",
        encoding="utf-8",
    )
    return tmp_path


def test_clean_record_drops_absent_function_keeps_file(tmp_path: Path) -> None:
    repo = _write_repo(tmp_path)
    record = {
        "query": "q",
        "answer": [],
        "trace_answer": [{
            "file": "src/main/java/com/amihaiemil/eoyaml/ReadPlainScalarValue.java",
            "functions": ["value", "unquote"],
        }],
        "candidate_answers": [],
    }

    cleaned = clean_record(record, repo)

    location = cleaned["trace_answer"][0]
    assert location["functions"] == ["value"]                 # unquote dropped
    assert location["file"].endswith("ReadPlainScalarValue.java")  # file kept
    # input not mutated
    assert record["trace_answer"][0]["functions"] == ["value", "unquote"]


def test_clean_record_leaves_functions_when_file_is_missing(tmp_path: Path) -> None:
    repo = _write_repo(tmp_path)
    record = {
        "answer": [{"file": "src/main/java/DoesNotExist.java", "functions": ["a", "b"]}],
        "trace_answer": [],
        "candidate_answers": [],
    }

    cleaned = clean_record(record, repo)

    assert cleaned["answer"][0]["functions"] == ["a", "b"]


def test_clean_record_cleans_all_three_answer_fields(tmp_path: Path) -> None:
    repo = _write_repo(tmp_path)
    f = "src/main/java/com/amihaiemil/eoyaml/ReadPlainScalarValue.java"
    record = {
        "answer": [{"file": f, "functions": ["value", "ghost1"]}],
        "trace_answer": [{"file": f, "functions": ["ghost2"]}],
        "candidate_answers": [{"file": f, "functions": ["value"]}],
    }

    cleaned = clean_record(record, repo)

    assert cleaned["answer"][0]["functions"] == ["value"]
    assert cleaned["trace_answer"][0]["functions"] == []
    assert cleaned["candidate_answers"][0]["functions"] == ["value"]


def test_count_answer_functions_sums_all_fields() -> None:
    record = {
        "answer": [{"file": "a", "functions": ["x", "y"]}],
        "trace_answer": [{"file": "b", "functions": ["z"]}],
        "candidate_answers": [],
    }
    assert count_answer_functions(record) == 3
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/unit/evaluation/test_answer_cleaning.py -v`
Expected: FAIL —— `ImportError: cannot import name 'clean_record'`

- [ ] **Step 3: 写最小实现**

在 `evaluation/answer_cleaning.py` 追加：

```python
def clean_record(record: Mapping[str, object], repo_root: Path) -> dict[str, object]:
    """Return ``record`` with functions absent from ``repo_root`` removed.

    Only existing files are parsed; a location whose file is missing keeps its
    functions untouched (file-level validity is the evaluator's concern). The
    file list itself is never modified, and the input mapping is not mutated.
    """
    cleaned = dict(record)
    for field_name in _ANSWER_FIELDS:
        locations = cleaned.get(field_name)
        if not isinstance(locations, list):
            continue
        cleaned[field_name] = [_clean_location(loc, repo_root) for loc in locations]
    return cleaned


def _clean_location(location: object, repo_root: Path) -> object:
    if not isinstance(location, Mapping):
        return location
    file = str(location.get("file") or "")
    functions = location.get("functions")
    if not file or not isinstance(functions, list):
        return dict(location)
    source = _read_source(repo_root / file)
    if source is None:
        return dict(location)  # file absent at this revision: leave functions as-is
    declared = declared_functions(source)
    kept = [fn for fn in functions if normalize_function_name(fn) in declared]
    return {**dict(location), "functions": kept}


def _read_source(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def count_answer_functions(record: Mapping[str, object]) -> int:
    """Total functions listed across all answer fields (for run summaries)."""
    total = 0
    for field_name in _ANSWER_FIELDS:
        locations = record.get(field_name)
        if not isinstance(locations, list):
            continue
        for location in locations:
            if isinstance(location, Mapping):
                functions = location.get("functions")
                if isinstance(functions, list):
                    total += len(functions)
    return total
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/unit/evaluation/test_answer_cleaning.py -v`
Expected: PASS（全部用例绿）

- [ ] **Step 5: 提交**

```bash
git add evaluation/answer_cleaning.py tests/unit/evaluation/test_answer_cleaning.py
git commit -m "feat: clean phantom functions from benchmark answers, keeping files"
```

---

### Task 5: 清洗并入 `scripts/mine_trace_queries.py`

**Files:**
- Modify: `scripts/mine_trace_queries.py`（常量区 L14-19、import 区 L35-41、`_run_cases` L103-143、新增 `_clean_rows` 助手）
- Modify: `tests/unit/test_mine_trace_queries_script.py`（新增集成测试）

**Interfaces:**
- Consumes: `evaluation.repo_cache.resolve_repo`、`evaluation.answer_cleaning.clean_record`、`evaluation.answer_cleaning.count_answer_functions`。
- Produces: `_run_cases` 的 summary 新增键 `removed_functions: int`、`removed_functions_by_repo: dict[str, int]`。

- [ ] **Step 1: 写失败测试**

在 `tests/unit/test_mine_trace_queries_script.py` 追加（沿用文件已有的 `_load_script()` 与 importlib 模式）：

```python
def test_run_cases_drops_functions_absent_from_the_pinned_revision(
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
        "    public String value() { return \"\"; }\n"
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
    assert written["trace_answer"][0]["functions"] == ["value"]   # unquote dropped
    assert written["trace_answer"][0]["file"] == java_file        # file kept
    assert summary["removed_functions"] == 1
    assert summary["removed_functions_by_repo"] == {"decorators-squad/eo-yaml": 1}
```

在文件顶部 import 区补 `import json`（若尚未导入）。

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/unit/test_mine_trace_queries_script.py::test_run_cases_drops_functions_absent_from_the_pinned_revision -v`
Expected: FAIL —— `AttributeError: module ... has no attribute 'resolve_repo'`（monkeypatch 目标不存在），或写出的 `functions` 仍含 `unquote`。

- [ ] **Step 3: 写实现**

3a. 常量去绝对路径。把 `_ROOT = Path(__file__).resolve().parents[1]` 移到常量区**最前**（现 L30 的定义删除），并改 L14-19：

```python
_ROOT = Path(__file__).resolve().parents[1]
INPUT = str(_ROOT / "outputs/open_swe_traces/open_swe_java_sample.jsonl")
OUTPUT_DIR = str(_ROOT / "outputs/open_swe_traces")
OUTPUT = f"{OUTPUT_DIR}/codesense-semantic-query.jsonl"
```

（保留原 L31-33 的 `sys.path` 调整块，只是不再重复定义 `_ROOT`。）

3b. import 区（L35-41 附近）加：

```python
from evaluation.answer_cleaning import clean_record, count_answer_functions  # noqa: E402
from evaluation.repo_cache import resolve_repo  # noqa: E402
```

3c. `_run_cases` 里，在 `rows = [query.to_dict() for query in batch.queries]` 之后、写盘之前插入清洗；并在函数内维护缓存与计数。改造后的 `_run_cases` 关键片段：

```python
    processed = written = search_episodes = eligible_episodes = 0
    removed_functions = 0
    removed_by_repo: Counter[str] = Counter()
    repo_roots: dict[tuple[str, str], Path] = {}
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
                rows, removed, by_repo = _clean_rows(rows, repo_roots, skip_reasons)
                removed_functions += removed
                removed_by_repo.update(by_repo)
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
            if processed % 10 == 0:
                break
    return {
        "processed_traces": processed,
        "search_episodes": search_episodes,
        "eligible_episodes": eligible_episodes,
        "written_queries": written,
        "removed_functions": removed_functions,
        "removed_functions_by_repo": dict(removed_by_repo),
        "skipped": dict(skip_reasons),
    }
```

> 注意：保留现有 WIP 断点 `if processed % 10 == 0: break`（属用户调试代码，本 plan 不动）。

3d. 新增编排助手 `_clean_rows`（放 `_run_cases` 之后）：

```python
def _clean_rows(
    rows: list[dict[str, object]],
    repo_roots: dict[tuple[str, str], Path],
    skip_reasons: Counter[str],
) -> tuple[list[dict[str, object]], int, Counter[str]]:
    """Drop phantom functions from mined rows, resolving each repo once.

    A row without a repo/base_commit cannot be pinned to a revision, so it is
    written through untouched. A resolve failure degrades to "no cleaning" for
    that repository rather than aborting the whole mining run.
    """
    cleaned: list[dict[str, object]] = []
    removed_total = 0
    removed_by_repo: Counter[str] = Counter()
    for row in rows:
        repo = str(row.get("repo") or "")
        commit = row.get("base_commit")
        if not repo or not commit:
            cleaned.append(row)
            continue
        key = (repo, str(commit))
        root = repo_roots.get(key)
        if root is None:
            try:
                root = resolve_repo(repo, str(commit))
            except Exception as exc:  # noqa: BLE001 -- one repo must not stop mining
                skip_reasons[f"repo_resolve_failed:{type(exc).__name__}"] += 1
                cleaned.append(row)
                continue
            repo_roots[key] = root
        before = count_answer_functions(row)
        new_row = clean_record(row, root)
        removed = before - count_answer_functions(new_row)
        if removed:
            removed_total += removed
            removed_by_repo[repo] += removed
        cleaned.append(new_row)
    return cleaned, removed_total, removed_by_repo
```

- [ ] **Step 4: 运行测试确认通过（含既有用例不回归）**

Run: `pytest tests/unit/test_mine_trace_queries_script.py -v`
Expected: PASS —— 新集成测试绿；`test_run_cases_writes_every_query_returned_for_one_trace` 仍绿（其 rows 无 repo/base_commit → 走"透传不清洗"分支）；`test_script_imports_package_when_scripts_directory_precedes_root` 仍绿。

- [ ] **Step 5: 提交**

```bash
git add scripts/mine_trace_queries.py tests/unit/test_mine_trace_queries_script.py
git commit -m "feat: mine_trace_queries cleans phantom functions from benchmark answers"
```

---

### Task 6: 三关校验 + 真实数据验收

**Files:**
- 无新增（验证性任务）

**Interfaces:**
- Consumes: 前 5 个 Task 的产物。

- [ ] **Step 1: 三关全绿**

Run: `ruff check . && ruff format --check . && pytest -q`
Expected: 全部通过（注意：`test_run_cases_does_not_stop_after_ten_processed_traces` 若因用户 WIP 断点 `if processed % 10 == 0: break` 而失败，属既有 WIP 问题、不在本 plan 范围；如出现，向用户确认而非擅自改 WIP）。

- [ ] **Step 2: 真实数据验收（需网络，手动）**

```bash
conda activate codesearch
python scripts/mine_trace_queries.py     # 重挖，产出已清洗的单一 benchmark
python - <<'PY'
import json
p = "outputs/open_swe_traces/codesense-semantic-query.jsonl"
for line in open(p):
    r = json.loads(line)
    for loc in r.get("trace_answer", []):
        if loc["file"].endswith("ReadPlainScalarValue.java"):
            print(r["base_commit"][:10], loc["functions"])
PY
```
Expected: 对应 `95a4860` 的记录里 `ReadPlainScalarValue.java` 的 functions **只含 `value`、不含 `unquote`**；文件条目仍在。控制台 summary 打印 `removed_functions` > 0。

- [ ] **Step 3: 评测复跑确认分母干净（手动，可选）**

```bash
python scripts/evaluation.py
```
Expected: `codesense-evaluation.json` 中 case#4/#5 的 `trace_answer.function_recall` 分母不再计入 `unquote`；`evaluation/projects/` 下 `decorators-squad__eo-yaml__1987c13...` 与 `...__95a4860...` 为两个独立目录，互不覆盖。

- [ ] **Step 4: 收尾提交（如验收过程产生 CHANGELOG 记录）**

```bash
# 仅当按 AGENTS.md 规则 9 需要记录 CHANGELOG 时
git add CHANGELOG.md
git commit -m "docs: changelog for benchmark phantom-function cleaning"
```

---

## Self-Review

**1. Spec 覆盖：**
- ① 共享解析器 + commit 后缀命名 → Task 1、Task 2 ✓
- ② `declared_functions`/`normalize_function_name`/`clean_record`（含构造器、缺文件保留、不改文件、输入不变）→ Task 3、Task 4 ✓
- ③ 清洗并入挖掘 + `(repo,commit)` 缓存 + base_commit 空跳过 + dry_run 不清洗 + summary 计数 → Task 5 ✓
- ④ 评测改用共享 resolver + 去绝对路径 + BENCHMARK 路径不变 + 评分不改 → Task 2 ✓
- ⑤ 测试策略（declared_functions/clean_record/resolve_repo/挖掘集成/真实回归）→ Task 1/3/4/5/6 ✓
- 口径与边界（构造器、归一化、缺文件、None、单一文件、无审计、幂等）→ Global Constraints + Task 4/5 ✓
- 去绝对路径（规则 5）→ Task 2、Task 5 ✓

**2. 占位符扫描：** 无 TBD/TODO；每个代码步骤均给出完整可运行代码。

**3. 类型一致性：** `resolve_repo(repo, base_commit, projects_dir, *, manual_paths, run)` 在 Task 1 定义、Task 2 调用（`resolve_repo(repo, base_commit, Path(...), manual_paths=PROJECT_PATHS)`）、Task 5 调用（`resolve_repo(repo, str(commit))`）一致；`clean_record(record, repo_root) -> dict`、`count_answer_functions(record) -> int`、`declared_functions(str) -> set[str]`、`normalize_function_name(object) -> str` 跨 Task 3/4/5 命名与签名一致；summary 键 `removed_functions`/`removed_functions_by_repo` 在 Task 5 实现与测试一致。
