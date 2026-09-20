# Language Adapter Relations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 复用 Java `Declaration.supertypes` 构建类型级 `extends` / `implements` 和启发式方法级 `overrides` / `implements` 边，同时建立可供后续语言 adapter 复用的通用关系事实、校验与存储流程。

**Architecture:** 语言 adapter 分两阶段工作：`scan()` 产生声明级语法事实，`derive_relations(RelationContext)` 在项目符号 ID 完整后产生统一 `RelationBatch`。Java adapter 负责所有 Java 名称解析和关系分类；通用 indexing 层只校验、确定性去重、统计并把 `RelationFact` 转成现有 edge row，QL 继续遍历开放字符串 edge kind。

**Tech Stack:** Python 3.10+、标准库 dataclasses/collections/logging/math、tree-sitter Java、pytest、ruff、现有 CodeSense EdgeStore 与 QL operators。

**Spec:** `docs/superpowers/specs/2026-09-20-language-adapter-relations-design.md`

## Global Constraints

- 第一版只使用 tree-sitter 和现有索引，不增加 JDTLS、javac、CodeQL 或第三方依赖。
- `Declaration.supertypes` 继续保存末尾 simple name；只修复泛型参数误提取和 interface extends 漏失。
- `extends`、`implements`、`overrides` 方向一律为 concrete/source -> abstract/target。
- 只物化直接类型边；多跳继承由 `reach()` 完成，不预计算传递闭包。
- Java adapter 决定 relation kind、候选和 confidence；通用 pipeline 不包含 Java kind 判断。
- 无法唯一解析的类型或方法关系宁缺毋滥，必须进入 diagnostics，不能连接全部同名候选。
- `supertypes` 和 `parameter_types` 不写入 Element 或 symbol row；索引构建时必须物化真实 edge。
- 现有工作区含未提交且与 `codegen.py`、`compiler.py`、`CHANGELOG.md` 重叠的用户修改；实施时在当前 checkout 增量修改，不覆盖或重排无关内容。
- 每次提交只暂存该任务列出的路径，不使用 `git add .`。
- 对实施前已经 dirty 的重叠文件使用 `git add -p <paths>`，只选择本任务新增的 relation hunks；每次 commit 前用 `git diff --cached --name-only` 和 `git diff --cached` 核对内容。
- 所有 Python、pytest 和 ruff 命令通过 `conda run -n codesearch ...` 执行。
- 完成前必须通过 `ruff check .`、`ruff format --check .` 和 `pytest`。

## Review Focus

- `class Child<T> extends Base<T>` 且项目恰好声明 `T` 时，只能连接 `Base`，不能产生 `Child -> T`。
- 两个 package 都声明同名父类型时，同 package 候选应优先；无同 package 唯一候选时必须标记 ambiguous 并跳过。
- `interface Child extends Parent` 必须进入 `supertypes` 并产生 `extends`，不能继续沿用当前漏失行为。
- 父类型有多个相同 name/arity overload 时，精确参数列表可消歧；无法精确匹配时必须跳过而不是任选一个。
- 一个 adapter 的 `derive_relations()` 抛异常时，其他语言和现有 contains/calls/references/in_file 仍必须成功建索引。

## File Structure

| 文件 | 责任 |
|---|---|
| `codesense/lang/base.py` | 语言无关的 `IndexedDeclaration`、`RelationContext`、`RelationFact`、diagnostics/batch 契约和 `Language.derive_relations()` 协议 |
| `codesense/lang/__init__.py` | 导出新增语言关系契约 |
| `codesense/indexing/relations.py` | 通用 relation fact 校验、确定性去重和 edge row 转换 |
| `codesense/indexing/__init__.py` | 导出通用 relation builder API |
| `codesense/lang/java/scanner.py` | 修复 `supertypes`，提取标准化 `parameter_types` |
| `codesense/lang/java/relations.py` | Java 类型名称解析、类型关系分类和方法覆盖/实现启发式 |
| `codesense/lang/java/__init__.py` | `JavaLanguage.derive_relations()` 入口 |
| `codesense/indexing/pipeline.py` | 按语言构造 context、调用 adapter、聚合 diagnostics 和关系边 |
| `codesense/index.py` | 提升索引格式版本，强制旧索引重建 |
| `codesense/llm/schema.py` | planned strict schema 开放新 relation kind |
| `codesense/llm/compiler.py` | planned prompt 描述新关系 |
| `codesense/llm/codegen.py` | codegen operator reference 描述新关系和方向 |
| `docs/ql-operator-reference.md` | 记录关系语义、方向和示例 |
| `CHANGELOG.md` | 功能完成后的用户可见变更记录 |
| `tests/unit/indexing/test_relations.py` | 通用 relation builder 契约 |
| `tests/unit/lang/test_java_scanner.py` | Java scanner 的父类型与方法参数事实 |
| `tests/unit/lang/test_java_relations.py` | Java 类型和方法关系解析 |
| `tests/unit/indexing/test_file_nodes.py` | pipeline relation hook、故障隔离和统计 |
| `tests/unit/test_index.py` | 索引版本和新边 round-trip |
| `tests/unit/ql/test_project_operator.py` | 新关系的正反向投影 |
| `tests/unit/llm/test_schema.py`、`test_compiler.py`、`test_codegen_prompt.py` | LLM relation vocabulary 契约 |

---

### Task 1: 建立语言无关关系契约与通用 edge builder

**Files:**
- Modify: `codesense/lang/base.py`
- Modify: `codesense/lang/__init__.py`
- Modify: `codesense/lang/java/__init__.py`
- Create: `codesense/indexing/relations.py`
- Modify: `codesense/indexing/__init__.py`
- Modify: `tests/unit/lang/test_registry.py`
- Create: `tests/unit/indexing/test_relations.py`

**Interfaces:**
- Produces: `IndexedDeclaration(symbol_id, file, declaration)`、`RelationContext(declarations)`、`RelationFact(source_id, target_id, kind, site, confidence, provenance)`、`RelationDiagnostics`、`RelationBatch`。
- Produces: `build_relation_edges(facts, symbol_ids) -> list[dict[str, object]]`。
- Produces: `deduplicate_edge_rows(rows) -> list[dict[str, object]]`，供 pipeline 合并现有图边和 adapter 边。
- Produces: `Language.derive_relations(context) -> RelationBatch`。
- Consumes: 现有 `Declaration` 和现有 edge row shape。

- [ ] **Step 1: 写通用契约和 edge builder 的失败测试**

在 `tests/unit/indexing/test_relations.py` 添加：

```python
from __future__ import annotations

import math

import pytest

from codesense.indexing.relations import build_relation_edges, deduplicate_edge_rows
from codesense.lang import RelationFact


def test_builds_open_relation_kinds_and_keeps_evidence() -> None:
    rows = build_relation_edges(
        (
            RelationFact(
                2,
                1,
                "implements",
                site=(7, 4),
                confidence=0.7,
                provenance="java_supertypes_simple",
            ),
        ),
        symbol_ids={1, 2},
    )

    assert rows == [
        {
            "source_id": 2,
            "target_id": 1,
            "kind": "implements",
            "site": [7, 4],
            "confidence": 0.7,
            "provenance": "java_supertypes_simple",
        }
    ]


def test_duplicate_relation_keeps_strongest_fact_deterministically() -> None:
    rows = build_relation_edges(
        (
            RelationFact(2, 1, "extends", confidence=0.7, provenance="z"),
            RelationFact(2, 1, "extends", confidence=0.9, provenance="b"),
            RelationFact(2, 1, "extends", confidence=0.9, provenance="a"),
        ),
        symbol_ids={1, 2},
    )

    assert len(rows) == 1
    assert rows[0]["confidence"] == 0.9
    assert rows[0]["provenance"] == "a"


def test_edge_row_deduplication_also_handles_existing_graph_rows() -> None:
    rows = deduplicate_edge_rows(
        (
            {
                "source_id": 2,
                "target_id": 1,
                "kind": "implements",
                "site": None,
                "confidence": 0.6,
                "provenance": "graph",
            },
            {
                "source_id": 2,
                "target_id": 1,
                "kind": "implements",
                "site": [7, 4],
                "confidence": 0.8,
                "provenance": "adapter",
            },
        )
    )

    assert len(rows) == 1
    assert rows[0]["confidence"] == 0.8
    assert rows[0]["provenance"] == "adapter"


@pytest.mark.parametrize(
    "fact",
    (
        RelationFact(9, 1, "extends"),
        RelationFact(1, 9, "extends"),
        RelationFact(1, 1, "extends"),
        RelationFact(2, 1, ""),
        RelationFact(2, 1, "extends", confidence=-0.1),
        RelationFact(2, 1, "extends", confidence=1.1),
        RelationFact(2, 1, "extends", confidence=math.nan),
    ),
)
def test_invalid_relation_contract_is_rejected(fact: RelationFact) -> None:
    with pytest.raises(ValueError):
        build_relation_edges((fact,), symbol_ids={1, 2})
```

在 `tests/unit/lang/test_registry.py` 给 `ToyLanguage` 增加空 relation hook，并扩展 protocol 测试：

```python
def derive_relations(self, context: RelationContext) -> RelationBatch:
    assert all(item.declaration.kind == "function" for item in context.declarations)
    return RelationBatch()


def test_relation_fact_types_are_language_neutral(self) -> None:
    declaration = Declaration("Child", "class", supertypes=("Base",))
    indexed = IndexedDeclaration(2, "Child.toy", declaration)
    context = RelationContext((indexed,))

    assert context.declarations == (indexed,)
    assert RelationBatch().facts == ()
```

- [ ] **Step 2: 运行测试确认契约尚不存在**

Run:

```bash
conda run -n codesearch pytest -q tests/unit/indexing/test_relations.py tests/unit/lang/test_registry.py
```

Expected: FAIL，导入 `RelationFact`、`RelationContext` 或 `codesense.indexing.relations` 失败。

- [ ] **Step 3: 在语言层实现通用 dataclass 和二阶段协议**

在 `Declaration.qualified_name` 后加入 callable 参数事实，并在 `Declaration` 定义后加入关系类型：

```python
#: Normalized callable parameter types used by language relation resolvers.
parameter_types: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class IndexedDeclaration:
    symbol_id: int
    file: str
    declaration: Declaration


@dataclass(frozen=True, slots=True)
class RelationContext:
    declarations: tuple[IndexedDeclaration, ...]


@dataclass(frozen=True, slots=True)
class RelationFact:
    source_id: int
    target_id: int
    kind: str
    site: tuple[int, int] | None = None
    confidence: float = 1.0
    provenance: str = ""


@dataclass(frozen=True, slots=True)
class RelationDiagnostics:
    unresolved: int = 0
    ambiguous: int = 0
    skipped: int = 0


@dataclass(frozen=True, slots=True)
class RelationBatch:
    facts: tuple[RelationFact, ...] = ()
    diagnostics: RelationDiagnostics = field(default_factory=RelationDiagnostics)
```

在 `Language` protocol 中加入：

```python
def derive_relations(self, context: RelationContext) -> RelationBatch:
    """Resolve language-specific project relations after symbol IDs exist."""
    ...
```

同步 `codesense.lang.base.__all__` 和 `codesense/lang/__init__.py` 的 import/`__all__`。为保证 Task 1
提交后 Java adapter 仍满足 runtime-checkable protocol，在 `JavaLanguage` 临时增加：

```python
def derive_relations(self, context: RelationContext) -> RelationBatch:
    """Return no extra relations until the Java resolver is installed."""
    return RelationBatch()
```

Task 3 将该方法替换为 `derive_java_relations(context)`，不是保留第二条关系路径。
同步把 Java adapter 的 base import 改为：

```python
from codesense.lang.base import Declaration, RelationBatch, RelationContext, ScanResult
```

- [ ] **Step 4: 实现通用关系校验和确定性去重**

创建 `codesense/indexing/relations.py`，实现以下规则：

```python
def build_relation_edges(
    facts: Sequence[RelationFact],
    symbol_ids: Collection[int],
) -> list[dict[str, object]]:
    known = set(symbol_ids)
    unique: dict[tuple[int, int, str], RelationFact] = {}
    for fact in facts:
        if fact.source_id not in known or fact.target_id not in known:
            raise ValueError("relation endpoint is not in the index")
        if fact.source_id == fact.target_id:
            raise ValueError("relation cannot be a self edge")
        if not fact.kind:
            raise ValueError("relation kind cannot be empty")
        if not math.isfinite(fact.confidence) or not 0.0 <= fact.confidence <= 1.0:
            raise ValueError("relation confidence must be finite and in [0, 1]")
        key = (fact.source_id, fact.target_id, fact.kind)
        current = unique.get(key)
        if current is None or _fact_order(fact) < _fact_order(current):
            unique[key] = fact
    return [_edge_row(unique[key]) for key in sorted(unique)]


def _fact_order(fact: RelationFact) -> tuple[float, str, tuple[int, int]]:
    return (-fact.confidence, fact.provenance, fact.site or (-1, -1))
```

`_edge_row()` 必须把 tuple site 转成 JSON-compatible list，并完整保留 confidence/provenance。
`deduplicate_edge_rows()` 使用相同 `(source_id, target_id, kind)` key 和“最高 confidence、同分时最小
provenance/site”规则处理任意现有 edge rows，最后按 key 排序。同步 indexing package 导出两个函数。

- [ ] **Step 5: 运行定向测试和格式检查**

Run:

```bash
conda run -n codesearch pytest -q tests/unit/indexing/test_relations.py tests/unit/lang/test_registry.py
conda run -n codesearch ruff check codesense/lang/base.py codesense/lang/__init__.py codesense/lang/java/__init__.py codesense/indexing/relations.py codesense/indexing/__init__.py tests/unit/lang/test_registry.py tests/unit/indexing/test_relations.py
conda run -n codesearch ruff format --check codesense/lang/base.py codesense/lang/__init__.py codesense/lang/java/__init__.py codesense/indexing/relations.py codesense/indexing/__init__.py tests/unit/lang/test_registry.py tests/unit/indexing/test_relations.py
```

Expected: 全部通过。

- [ ] **Step 6: 提交通用关系契约**

```bash
git add codesense/lang/base.py codesense/lang/__init__.py codesense/lang/java/__init__.py codesense/indexing/relations.py codesense/indexing/__init__.py tests/unit/lang/test_registry.py tests/unit/indexing/test_relations.py
git commit -m "feat: add language relation contracts"
```

---

### Task 2: 修复 Java supertypes 并提取参数类型

**Files:**
- Modify: `codesense/lang/java/scanner.py`
- Modify: `tests/unit/lang/test_java_scanner.py`

**Interfaces:**
- Consumes: tree-sitter Java declaration nodes。
- Produces: `Declaration.supertypes` 的直接 simple-name 父类型序列。
- Produces: `Declaration.parameter_types` 的泛型擦除参数列表。

- [ ] **Step 1: 写父类型和参数归一化失败测试**

在 slow scanner 测试类加入：

```python
def test_supertypes_keep_only_direct_root_types_and_include_interface_extends(
    self, scanner: JavaDeclarationScanner
) -> None:
    result = scanner.scan(
        """class T {}
class Child<T> extends Base<T> implements One, pkg.Two {}
interface Sub extends One, pkg.Two {}
enum Mode implements One { VALUE }
record Row(int id) implements One {}
"""
    )
    declarations = {item.name: item for item in result.declarations}

    assert declarations["Child"].supertypes == ("Base", "One", "Two")
    assert "T" not in declarations["Child"].supertypes
    assert declarations["Sub"].supertypes == ("One", "Two")
    assert declarations["Mode"].supertypes == ("One",)
    assert declarations["Row"].supertypes == ("One",)


def test_method_parameter_types_are_erased_without_parsing_the_display_signature(
    self, scanner: JavaDeclarationScanner
) -> None:
    result = scanner.scan(
        """class Worker {
    @Override
    void save(java.util.List<User> users, String... names, int[] flags) {}
}
"""
    )
    method = next(item for item in result.declarations if item.name == "save")

    assert method.parameter_types == ("java.util.List", "String[]", "int[]")
```

- [ ] **Step 2: 运行 scanner 测试确认当前泛型和 interface 行为失败**

Run:

```bash
conda run -n codesearch pytest -q tests/unit/lang/test_java_scanner.py -k "supertypes or parameter_types" -v
```

Expected: FAIL；当前 `Child.supertypes` 包含类型参数、`Sub.supertypes` 为空，且没有 `parameter_types`。

- [ ] **Step 3: 用 AST 直接子节点重写 `_supertypes()`**

实现 clause 收集和 simple-name 提取，不再对整段文本执行 `_WORD.findall()`：

```python
def _supertypes(node: object, data: bytes) -> tuple[str, ...]:
    clauses = [
        child
        for child in (
            node.child_by_field_name("superclass"),
            node.child_by_field_name("interfaces"),
        )
        if child is not None
    ]
    clauses.extend(child for child in node.children if child.type == "extends_interfaces")
    found: dict[str, None] = {}
    for clause in clauses:
        for type_node in _direct_supertype_nodes(clause):
            name = _root_type_name(type_node, data)
            if name:
                found.setdefault(name, None)
    return tuple(found)
```

`_direct_supertype_nodes()` 只返回 superclass 的一个类型表达式或 type_list 的直接成员；
`_root_type_name()` 对 `generic_type` 读取类型参数之前的第一个 type-shaped child，对 scoped spelling
取最后一段。不要递归遍历 `type_arguments`。

- [ ] **Step 4: 实现参数类型提取和泛型擦除**

在构造 method/constructor Declaration 时填写：

```python
parameter_types=(
    _parameter_types(node, data) if kind in _CALLABLE_KINDS else ()
),
```

用深度计数移除 `<...>`，再移除空白并把 `...` 转成 `[]`：

```python
def _normalize_parameter_type(value: str, *, varargs: bool) -> str:
    kept: list[str] = []
    depth = 0
    for character in value:
        if character == "<":
            depth += 1
        elif character == ">":
            depth = max(0, depth - 1)
        elif depth == 0 and not character.isspace():
            kept.append(character)
    normalized = "".join(kept)
    return f"{normalized}[]" if varargs and not normalized.endswith("[]") else normalized
```

`_parameter_types()` 只处理 formal/spread parameters，使用 AST 的 `type` field，参数名和注解不进入结果。

- [ ] **Step 5: 运行完整 Java scanner 测试**

Run:

```bash
conda run -n codesearch pytest -q tests/unit/lang/test_java_scanner.py
conda run -n codesearch ruff check codesense/lang/java/scanner.py tests/unit/lang/test_java_scanner.py
conda run -n codesearch ruff format --check codesense/lang/java/scanner.py tests/unit/lang/test_java_scanner.py
```

Expected: 全部通过，包括 `test_scan_parses_the_source_once`。

- [ ] **Step 6: 提交 Java scanner 事实修复**

```bash
git add codesense/lang/java/scanner.py tests/unit/lang/test_java_scanner.py
git commit -m "feat: extract Java relation facts"
```

---

### Task 3: 在 Java adapter 中推导类型关系

**Files:**
- Create: `codesense/lang/java/relations.py`
- Modify: `codesense/lang/java/__init__.py`
- Create: `tests/unit/lang/test_java_relations.py`

**Interfaces:**
- Consumes: `RelationContext` 中的 indexed Java declarations 与 `Declaration.supertypes`。
- Produces: `derive_java_relations(context: RelationContext) -> RelationBatch`。
- Produces: type `extends` / `implements` facts，方向为 child -> parent。

- [ ] **Step 1: 写类型分类、同包优先和歧义失败测试**

创建测试 helper：

```python
def indexed(
    symbol_id: int,
    name: str,
    kind: str,
    *,
    package: str = "demo",
    supertypes: tuple[str, ...] = (),
) -> IndexedDeclaration:
    qualified = f"{package}.{name}" if package else name
    return IndexedDeclaration(
        symbol_id,
        f"{name}.java",
        Declaration(
            name,
            kind,
            line=symbol_id,
            column=2,
            end_line=symbol_id + 1,
            qualified_name=qualified,
            supertypes=supertypes,
        ),
    )
```

加入断言：

```python
def test_classifies_type_relations_from_source_and_target_kinds() -> None:
    context = RelationContext(
        (
            indexed(1, "Base", "class"),
            indexed(2, "Port", "interface"),
            indexed(3, "Child", "class", supertypes=("Base", "Port")),
            indexed(4, "SubPort", "interface", supertypes=("Port",)),
            indexed(5, "Mode", "enum", supertypes=("Port",)),
        )
    )

    batch = derive_java_relations(context)

    assert {(f.source_id, f.target_id, f.kind) for f in batch.facts} == {
        (3, 1, "extends"),
        (3, 2, "implements"),
        (4, 2, "extends"),
        (5, 2, "implements"),
    }


def test_same_package_wins_but_global_simple_name_ambiguity_is_skipped() -> None:
    same_package = RelationContext(
        (
            indexed(1, "Base", "class", package="a"),
            indexed(2, "Base", "class", package="b"),
            indexed(3, "Child", "class", package="b", supertypes=("Base",)),
        )
    )
    ambiguous = RelationContext(
        (
            indexed(1, "Base", "class", package="a"),
            indexed(2, "Base", "class", package="b"),
            indexed(3, "Child", "class", package="c", supertypes=("Base",)),
        )
    )

    assert {(f.source_id, f.target_id) for f in derive_java_relations(same_package).facts} == {
        (3, 2)
    }
    result = derive_java_relations(ambiguous)
    assert result.facts == ()
    assert result.diagnostics.ambiguous == 1
```

再加入外部父类型 unresolved、非法 source/target kind skipped，以及 type edge site 使用 source declaration 起点的测试。

- [ ] **Step 2: 运行类型 resolver 测试确认模块缺失**

Run:

```bash
conda run -n codesearch pytest -q tests/unit/lang/test_java_relations.py -v
```

Expected: FAIL，`codesense.lang.java.relations` 尚不存在。

- [ ] **Step 3: 实现项目类型索引和 package 解析**

在 `relations.py` 定义 Java 类型集合和轻量索引：

```python
_TYPE_KINDS = frozenset({"class", "interface", "enum", "record", "annotation_type"})


def _structural_name(declaration: Declaration) -> str:
    return (
        f"{declaration.container}.{declaration.name}"
        if declaration.container
        else declaration.name
    )


def _package(declaration: Declaration) -> str:
    structural = _structural_name(declaration)
    qualified = declaration.qualified_name
    if qualified == structural:
        return ""
    suffix = f".{structural}"
    return qualified[: -len(suffix)] if qualified.endswith(suffix) else ""
```

建立 `qualified_name -> IndexedDeclaration`、`simple name -> tuple[candidates]` 和 symbol ID -> declaration map；所有 map 在一次调用中构造一次。

- [ ] **Step 4: 实现 supertypes 解析、分类和 diagnostics**

使用固定分类表：

```python
_TYPE_RELATIONS = {
    ("class", "class"): "extends",
    ("class", "interface"): "implements",
    ("interface", "interface"): "extends",
    ("enum", "interface"): "implements",
    ("record", "interface"): "implements",
}
```

同包命中输出 confidence `0.8` / provenance `java_supertypes_same_package`；全项目唯一 simple-name 命中输出 `0.7` / `java_supertypes_simple`。没有候选增加 unresolved；多个候选增加 ambiguous；不存在于分类表增加 skipped。site 使用 `(source.line, source.column)`。

`derive_java_relations()` 先返回类型 facts；Task 4 再在同一函数中追加方法 facts。

- [ ] **Step 5: 在 JavaLanguage 上暴露二阶段入口**

在 `codesense/lang/java/__init__.py` 加入：

```python
def derive_relations(self, context: RelationContext) -> RelationBatch:
    return derive_java_relations(context)
```

同步 import 和 `__all__`，保持 scanner 仍然 lazy 加载 grammar。

- [ ] **Step 6: 运行 Java 类型关系测试**

Run:

```bash
conda run -n codesearch pytest -q tests/unit/lang/test_java_relations.py tests/unit/lang/test_registry.py
conda run -n codesearch ruff check codesense/lang/java/relations.py codesense/lang/java/__init__.py tests/unit/lang/test_java_relations.py
conda run -n codesearch ruff format --check codesense/lang/java/relations.py codesense/lang/java/__init__.py tests/unit/lang/test_java_relations.py
```

Expected: 全部通过。

- [ ] **Step 7: 提交 Java 类型关系**

```bash
git add codesense/lang/java/relations.py codesense/lang/java/__init__.py tests/unit/lang/test_java_relations.py
git commit -m "feat: derive Java type relations"
```

---

### Task 4: 用 AST 方法事实推导 overrides 和 implements

**Files:**
- Modify: `codesense/lang/java/relations.py`
- Modify: `tests/unit/lang/test_java_relations.py`

**Interfaces:**
- Consumes: Task 3 的直接类型 facts、owner type、method name、`parameter_types`、annotations 和 modifiers。
- Produces: method `overrides` / `implements` facts，方向为 concrete method -> ancestor method。

- [ ] **Step 1: 写精确参数、arity fallback、双重关系和修饰符失败测试**

增加 method helper：

```python
def method(
    symbol_id: int,
    owner: str,
    name: str,
    parameters: tuple[str, ...],
    *,
    package: str = "demo",
    annotations: tuple[AnnotationUse, ...] = (),
    modifiers: frozenset[str] = frozenset(),
) -> IndexedDeclaration:
    return IndexedDeclaration(
        symbol_id,
        f"{owner}.java",
        Declaration(
            name,
            "method",
            line=symbol_id,
            end_line=symbol_id,
            container=owner,
            qualified_name=f"{package}.{owner}.{name}",
            parameter_types=parameters,
            annotations=annotations,
            modifiers=modifiers,
        ),
    )
```

覆盖以下断言：

```python
def test_method_can_override_a_class_and_implement_an_interface() -> None:
    context = RelationContext(
        (
            indexed(1, "Base", "class"),
            indexed(2, "Port", "interface"),
            indexed(3, "Child", "class", supertypes=("Base", "Port")),
            method(10, "Base", "run", ("String",)),
            method(11, "Port", "run", ("String",)),
            method(
                12,
                "Child",
                "run",
                ("String",),
                annotations=(AnnotationUse("Override"),),
            ),
        )
    )

    facts = derive_java_relations(context).facts

    assert {(f.source_id, f.target_id, f.kind) for f in facts} >= {
        (12, 10, "overrides"),
        (12, 11, "implements"),
    }
```

再增加：精确参数从两个同 arity overload 中选择唯一目标；参数列表为空/不完整且有多个 overload 时 ambiguous 并不建方法边；最近 class 声明胜过更远祖先；interface extends 的方法关系叫 overrides；source static/private 与 target static/private/final 被跳过。

- [ ] **Step 2: 运行方法关系测试确认只有类型边**

Run:

```bash
conda run -n codesearch pytest -q tests/unit/lang/test_java_relations.py -k "method or overload or modifier" -v
```

Expected: FAIL，方法关系集合为空或 diagnostics 不匹配。

- [ ] **Step 3: 建立 owner/method 索引并遍历有限祖先图**

从 method `qualified_name` 去掉末尾 method name 得到 owner qualified name，再解析到 type ID；建立：

```python
methods_by_owner: dict[int, dict[tuple[str, int], list[IndexedDeclaration]]]
parents_by_type: dict[int, tuple[int, ...]]
```

祖先遍历使用 BFS、visited set 和 `MAX_ANCESTOR_DEPTH = 8`。class 链在首次找到匹配声明的深度后不再连接更远 class 方法；interface 分支保留不同接口中的合法同签名目标。每个 `(method, ancestor owner)` 候选数超过 `MAX_METHOD_CANDIDATES = 8` 时记 ambiguous 并跳过。

- [ ] **Step 4: 实现签名选择、关系分类和证据等级**

候选选择严格按以下顺序：

```python
exact = [
    candidate
    for candidate in candidates
    if candidate.declaration.parameter_types == source.declaration.parameter_types
]
if len(exact) == 1:
    selected = exact
    confidence = 0.95 if _has_override(source.declaration) else 0.90
    provenance = "java_override_exact" if _has_override(source.declaration) else "java_signature_match"
elif not exact and len(candidates) == 1:
    selected = candidates
    confidence = 0.80 if _has_override(source.declaration) else 0.65
    provenance = "java_override_arity" if _has_override(source.declaration) else "java_name_arity"
else:
    selected = []
```

target owner 是 interface 时，class/enum/record source 输出 `implements`；interface source 输出 `overrides`；target owner 是 class 时输出 `overrides`。构造器不进入 method index。修饰符检查在建 fact 前完成。

- [ ] **Step 5: 运行 Java relation 全部测试**

Run:

```bash
conda run -n codesearch pytest -q tests/unit/lang/test_java_relations.py tests/unit/lang/test_java_scanner.py
conda run -n codesearch ruff check codesense/lang/java/relations.py tests/unit/lang/test_java_relations.py
conda run -n codesearch ruff format --check codesense/lang/java/relations.py tests/unit/lang/test_java_relations.py
```

Expected: 全部通过。

- [ ] **Step 6: 提交 Java 方法关系**

```bash
git add codesense/lang/java/relations.py tests/unit/lang/test_java_relations.py
git commit -m "feat: derive Java method relations"
```

---

### Task 5: 把 adapter 关系接入通用 pipeline 和磁盘索引

**Files:**
- Modify: `codesense/indexing/pipeline.py`
- Modify: `codesense/index.py`
- Modify: `tests/unit/indexing/test_file_nodes.py`
- Modify: `tests/unit/test_index.py`
- Modify: `tests/unit/ql/test_project_operator.py`

**Interfaces:**
- Consumes: `Language.derive_relations(RelationContext) -> RelationBatch` 和 `build_relation_edges()`。
- Produces: `Stats.relation_counts`、`relation_unresolved`、`relation_ambiguous`、`relation_skipped`、`relation_failures`。
- Produces: index format v3，包含持久化的新关系边。

- [ ] **Step 1: 写 fake adapter pipeline 和故障隔离失败测试**

给 `ToyLanguage` 增加默认空 hook，新增 `RelationLanguage`：

```python
class RelationLanguage(ToyLanguage):
    indexed_kinds = frozenset({"interface", "class"})

    def scan(self, source: str) -> ScanResult:
        kind, name = source.split()
        return ScanResult((Declaration(name, kind, line=1, end_line=2),))

    def derive_relations(self, context: RelationContext) -> RelationBatch:
        ids = {item.declaration.name: item.symbol_id for item in context.declarations}
        return RelationBatch(
            (
                RelationFact(
                    ids["Worker"],
                    ids["Port"],
                    "implements",
                    confidence=0.7,
                    provenance="toy_relation",
                ),
            ),
            RelationDiagnostics(unresolved=1, ambiguous=2, skipped=3),
        )
```

测试 build 后 edge row、统计和反向 `project()`；再定义一个抛 `RuntimeError("relation failure")` 的 adapter，断言 symbols、in_file edges 和 build result 仍存在且 `relation_failures == 1`。

- [ ] **Step 2: 写索引版本和 round-trip 失败测试**

把版本断言改为：

```python
def test_current_format_is_v3(self) -> None:
    assert FORMAT_VERSION == 3
```

在手写 payload 加入一条 `implements` edge，并断言 `Index.save/load/to_context()` 后 site、confidence、provenance 保持完整。给 `test_project_operator.py` fixture 增加 `Edge(30, 20, "implements", confidence=0.7)` 和 class/interface elements，断言从 interface backward project 得到 class、从 class forward project 得到 interface。

- [ ] **Step 3: 运行测试确认 pipeline 尚未调用 adapter 且版本仍为 2**

Run:

```bash
conda run -n codesearch pytest -q tests/unit/indexing/test_file_nodes.py tests/unit/test_index.py tests/unit/ql/test_project_operator.py
```

Expected: FAIL，缺少 relation edges/stats 且 `FORMAT_VERSION == 2`。

- [ ] **Step 4: 在 pipeline 中构造每语言 RelationContext 并隔离失败**

在所有 declaration ID 已分配后，按 `_IndexedFile.language` 收集：

```python
context = RelationContext(
    tuple(
        IndexedDeclaration(symbol_id, record.path, declaration)
        for record in indexed_files
        if record.language == language.name
        for declaration, symbol_id in record.declarations
    )
)
```

调用 adapter，验证 batch，再合并：

```python
try:
    batch = language.derive_relations(context)
    relation_edges = build_relation_edges(
        batch.facts,
        symbol_ids={row["symbol_id"] for row in symbols},
    )
except Exception as exc:  # noqa: BLE001 -- one relation pass must not sink the index
    stats.relation_failures += 1
    _log.warning("%s relation derivation failed: %s: %s", language.name, type(exc).__name__, exc)
    relation_edges = []
else:
    stats.relation_unresolved += batch.diagnostics.unresolved
    stats.relation_ambiguous += batch.diagnostics.ambiguous
    stats.relation_skipped += batch.diagnostics.skipped
```

按最终 relation rows 更新 `relation_counts`。使用
`deduplicate_edge_rows(graph_edges + relation_edges + in_file_edges)` 生成最终 payload；如不同来源出现
同一 `(source_id, target_id, kind)`，保留最高 confidence。

- [ ] **Step 5: 更新所有测试 fake adapters 和 build stats**

`tests/unit/indexing/test_file_nodes.py` 与 `tests/unit/lang/test_registry.py` 中参与 `build_index()` 的 adapter 都实现空或真实 `derive_relations()`。`Stats` 使用 `field(default_factory=dict)` 声明 relation counts，避免实例共享。

- [ ] **Step 6: 提升格式版本并验证存储无需新 schema**

把 `codesense/index.py` 的 `FORMAT_VERSION` 从 2 改为 3。保持 edge JSON 和 `Index.to_context()` 不变；新 edge kind 应自然进入 `InMemoryEdgeStore`。

- [ ] **Step 7: 运行 pipeline、index 和 QL 定向回归**

Run:

```bash
conda run -n codesearch pytest -q tests/unit/indexing/test_relations.py tests/unit/indexing/test_file_nodes.py tests/unit/test_index.py tests/unit/ql/test_project_operator.py tests/unit/lang/test_registry.py
conda run -n codesearch ruff check codesense/indexing/pipeline.py codesense/index.py tests/unit/indexing/test_file_nodes.py tests/unit/test_index.py tests/unit/ql/test_project_operator.py
conda run -n codesearch ruff format --check codesense/indexing/pipeline.py codesense/index.py tests/unit/indexing/test_file_nodes.py tests/unit/test_index.py tests/unit/ql/test_project_operator.py
```

Expected: 全部通过。

- [ ] **Step 8: 提交通用 pipeline 集成**

```bash
git add codesense/indexing/pipeline.py codesense/index.py tests/unit/indexing/test_file_nodes.py tests/unit/test_index.py tests/unit/ql/test_project_operator.py
git commit -m "feat: persist adapter-derived relations"
```

---

### Task 6: 向 planned、codegen 和算子文档开放新关系

**Files:**
- Modify: `codesense/llm/schema.py`
- Modify: `codesense/llm/compiler.py`
- Modify: `codesense/llm/codegen.py`
- Modify: `tests/unit/llm/test_schema.py`
- Modify: `tests/unit/llm/test_compiler.py`
- Modify: `tests/unit/llm/test_codegen_prompt.py`
- Modify: `docs/ql-operator-reference.md`

**Interfaces:**
- Produces: strict query-understanding schema 接受 `extends`、`implements`、`overrides`。
- Produces: codegen/planned prompt 说明 relation direction 和启发式置信度。
- Consumes: 已持久化的新 edge kind；不新增算子或 Java 特例。

- [ ] **Step 1: 写 schema 和 prompt 失败测试**

在 schema 测试中逐个替换 relation edge 并验证解析：

```python
@pytest.mark.parametrize("edge", ("extends", "implements", "overrides"))
def test_type_and_method_relations_are_supported(edge: str) -> None:
    raw = payload()
    raw["relations"][0]["edges"] = [edge]  # type: ignore[index]

    understood = QueryUnderstandingResult.model_validate(raw)

    assert understood.relations[0].edges[0].value == edge
```

保留 `inherits` 作为未知值拒绝用例。把 compiler/codegen prompt 断言扩展为三个新关系，并断言包含 `concrete`、`abstract` 或等价方向说明。

- [ ] **Step 2: 运行 LLM 契约测试确认新枚举被拒绝**

Run:

```bash
conda run -n codesearch pytest -q tests/unit/llm/test_schema.py tests/unit/llm/test_compiler.py tests/unit/llm/test_codegen_prompt.py
```

Expected: FAIL，Pydantic enum 和 prompts 尚未包含新关系。

- [ ] **Step 3: 扩展 RelationKind 和两个 prompt**

在 `RelationKind` 增加：

```python
EXTENDS = "extends"
IMPLEMENTS = "implements"
OVERRIDES = "overrides"
```

planned prompt 的显式 relation 列表加入三者。codegen `OPERATOR_SPEC` 的 project edge 文档加入：

```text
Type and method hierarchy edges point from concrete to abstract:
extends, implements, and overrides. Use backward projection from an abstract
type or method to find implementations/overrides. These lightweight Java
relations may have confidence below 1.0.
```

不要加入项目词表，也不要增加新的顶层 prompt 自夸句。

- [ ] **Step 4: 更新 QL 边表和查询示例**

在 `docs/ql-operator-reference.md` 的现役边表增加：

```markdown
| `extends` | 子类型 → 父类型 | Java `supertypes` 的低成本近似关系 |
| `implements` | 实现类型/方法 → 接口/接口方法 | 类型和方法共享 edge kind，由端点 kind 区分 |
| `overrides` | 覆盖方法 → 被覆盖方法 | Java 签名启发式关系 |
```

增加从接口反向投影实现类和实现方法的短例子，并说明 `min_confidence` 可排除弱边。

- [ ] **Step 5: 运行 LLM 与文档定向检查**

Run:

```bash
conda run -n codesearch pytest -q tests/unit/llm/test_schema.py tests/unit/llm/test_compiler.py tests/unit/llm/test_codegen_prompt.py
conda run -n codesearch ruff check codesense/llm/schema.py codesense/llm/compiler.py codesense/llm/codegen.py tests/unit/llm/test_schema.py tests/unit/llm/test_compiler.py tests/unit/llm/test_codegen_prompt.py
conda run -n codesearch ruff format --check codesense/llm/schema.py codesense/llm/compiler.py codesense/llm/codegen.py tests/unit/llm/test_schema.py tests/unit/llm/test_compiler.py tests/unit/llm/test_codegen_prompt.py
```

Expected: 全部通过；`rg -n "extends|implements|overrides" docs/ql-operator-reference.md` 命中新边表和示例。

- [ ] **Step 6: 提交查询表面和文档**

```bash
git add codesense/llm/schema.py tests/unit/llm/test_schema.py docs/ql-operator-reference.md
git add -p codesense/llm/compiler.py codesense/llm/codegen.py tests/unit/llm/test_compiler.py tests/unit/llm/test_codegen_prompt.py
git diff --cached --name-only
git diff --cached
git commit -m "feat: expose hierarchy relations to queries"
```

交互暂存时只选择包含 `extends`、`implements`、`overrides` relation vocabulary 或方向说明的 hunk；
已有 intent、judge、model、script logging 等修改保持 unstaged。

---

### Task 7: 端到端 Java 索引回归、CHANGELOG 和全仓门禁

**Files:**
- Create: `tests/integration/test_java_relations.py`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Consumes: scanner、Java resolver、pipeline、Index 与 QL 的完整实现。
- Produces: 真实临时 Java 项目到可查询 implements/extends/overrides edges 的无网络验收。

- [ ] **Step 1: 写真实临时项目端到端测试**

创建以下源码 fixture：

```python
def test_java_project_builds_queryable_type_and_method_relations(tmp_path: Path) -> None:
    (tmp_path / "Port.java").write_text(
        "package demo; interface Port { void run(String value); }",
        encoding="utf-8",
    )
    (tmp_path / "Base.java").write_text(
        "package demo; class Base { public void run(String value) {} }",
        encoding="utf-8",
    )
    (tmp_path / "Worker.java").write_text(
        """package demo;
class Worker extends Base implements Port {
    @Override public void run(String value) {}
}
""",
        encoding="utf-8",
    )

    result = build_index(tmp_path, languages=[JavaLanguage()])
    rows = {row["name"]: row for row in result.payload["symbols"] if row["kind"] != "file"}
    context = Index(
        IndexMeta("demo", str(tmp_path), "2026-09-20T00:00:00+00:00"),
        result.payload,
    ).to_context()

    port = Frag(nodes=context.symbols.get_many((rows["Port"]["symbol_id"],)))
    implementations = project(
        port,
        context,
        edge="implements",
        direction="backward",
        kind="class",
    )

    assert [item.name for item in implementations] == ["Worker"]
    assert result.stats.relation_counts["extends"] == 1
    assert result.stats.relation_counts["implements"] >= 2
    assert result.stats.relation_counts["overrides"] == 1
```

测试中按 `(name, kind, container)` 选择 overloaded method ID，不能用简单 dict 覆盖同名方法；另加 `reach()` 从 Worker 沿 extends 一跳到 Base 的断言。

- [ ] **Step 2: 运行端到端测试并修复只属于本功能的问题**

Run:

```bash
conda run -n codesearch pytest -q tests/integration/test_java_relations.py
```

Expected: PASS。若失败，只修复本计划列出的 scanner/resolver/pipeline 边界，不扩大到外部依赖解析。

- [ ] **Step 3: 更新 CHANGELOG**

在顶部增加 `2026-09-20 — Java 类型与方法关系`：

```markdown
## 2026-09-20 — Java 类型与方法关系

- Java adapter 复用修正后的 `Declaration.supertypes`，构建低置信度、可解释的
  `extends` / `implements` 类型边，并用方法名、参数数量和擦除参数类型近似推导
  `overrides` / `implements` 方法边。
- 语言 adapter 新增项目级 `derive_relations()` 阶段；通用 indexing 层统一校验、去重、
  统计和持久化 relation facts，为后续语言复用同一关系存储流程。
- planned、codegen 与 QL 文档开放新 edge vocabulary；边方向统一为 concrete -> abstract，
  可从抽象类型或方法反向投影实现。
- 索引格式提升到 v3，旧索引需要重新构建。
```

保留同日已有“条件化语义判断兜底”条目，不能覆盖用户现有 CHANGELOG 修改。

- [ ] **Step 4: 运行所有定向关系测试**

Run:

```bash
conda run -n codesearch pytest -q tests/unit/indexing/test_relations.py tests/unit/indexing/test_file_nodes.py tests/unit/lang/test_java_scanner.py tests/unit/lang/test_java_relations.py tests/unit/ql/test_project_operator.py tests/unit/llm/test_schema.py tests/unit/llm/test_compiler.py tests/unit/llm/test_codegen_prompt.py tests/unit/test_index.py tests/integration/test_java_relations.py
```

Expected: 全部通过。

- [ ] **Step 5: 运行仓库提交门禁**

Run:

```bash
conda run -n codesearch ruff check .
conda run -n codesearch ruff format --check .
conda run -n codesearch pytest
```

Expected: 三条命令退出码均为 0。若失败，分别报告本功能失败与工作区原有无关失败，不隐藏未运行或未通过的门禁。

- [ ] **Step 6: 检查 diff、密钥和索引产物**

Run:

```bash
git diff --check
git status --short
git diff --name-only
rg -n "CODESENSE_API_KEY|api[_-]?key" codesense tests docs CHANGELOG.md
```

Expected: 无 whitespace error；没有 `.indexes`、`index.json`、模型权重、输出报告或密钥进入任务 diff；`api_key` 只出现在既有配置/测试占位值中。

- [ ] **Step 7: 提交端到端验收和变更记录**

```bash
git add tests/integration/test_java_relations.py
git add -p CHANGELOG.md
git diff --cached --name-only
git diff --cached
git commit -m "test: verify Java hierarchy relations"
```

`CHANGELOG.md` 只选择“Java 类型与方法关系”新条目；已有“条件化语义判断兜底”及其他未提交内容
保持未暂存状态。

- [ ] **Step 8: 对照 spec 做最终人工核对**

确认以下事实均可由测试或最终 diff 指向：

1. Java 关系识别只存在于 `codesense/lang/java/`；
2. `codesense/indexing/relations.py` 不导入 Java；
3. 搜索阶段没有读取或临时映射 `Declaration.supertypes`；
4. 所有新边都带 confidence/provenance；
5. 旧索引加载明确要求 rebuild；
6. `project()` 和 `reach()` 本身没有 Java 特例；
7. CHANGELOG 与 QL 文档说明了近似精度和边方向。
