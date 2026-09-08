# File Target and Reference Graph Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `file` a first-class CodeSense result element and support returning Java files selected through `references`, `imports`, and `in_file` graph relations.

**Architecture:** Keep `Element` and `Frag` as the only data and operator types. Index file nodes beside declaration nodes, connect declarations to files with `in_file`, and use an evidence-preserving one-hop `project()` operator to transform semantic matches into file results. Extend the existing one-pass language scan and GraphBuilder to materialize project-local references without adding a second index or executor.

**Tech Stack:** Python 3, dataclasses, tree-sitter Java, existing CodeSense QL/Frag/Edge stores, pytest, Ruff.

**Spec:** `docs/superpowers/specs/2026-09-08-file-target-and-reference-graph-design.md`

## Global Constraints

- New implementation code stays under `codesense/`; entry scripts contain no business logic.
- Module imports perform no IO, process creation, grammar loading, or printing.
- Source files are parsed once; Java reference extraction reuses the declaration scan's tree-sitter AST.
- File nodes produce no postings in this version; declaration vocabulary and posting document frequency stay unchanged.
- The declaration population, not all Element nodes, remains the ICF and planner-specificity denominator.
- Default coherence traverses only `calls` and `contains`; it does not traverse `in_file` or `references`.
- Unresolved or excessively ambiguous references create no fictional edge and do not stop the index build.
- Existing test/generated/example source exclusion rules remain unchanged.
- Every implementation task follows red-green-refactor and stages only the files listed for that task.
- Run Python tooling through the `codesearch` conda environment.

## File Structure

- Create `codesense/ql/operators/project.py`: evidence-preserving, exact one-hop projection.
- Modify `codesense/ql/operators/__init__.py` and `codesense/ql/__init__.py`: export `project`.
- Modify `codesense/ql/context.py`: carry the injected declaration population used by planning.
- Modify `codesense/lang/base.py`: define `ReferenceUse` and `ScanResult`; update the language scan contract.
- Modify `codesense/lang/java/scanner.py` and `codesense/lang/java/__init__.py`: extract declarations and references in one parse.
- Modify `codesense/indexing/pipeline.py`: append file Elements, declaration population, and `in_file` edges.
- Modify `codesense/indexing/graph.py`: resolve and materialize `references` and `imports` edges.
- Modify `codesense/index.py`: bump the format and round-trip file/reference metadata including `Edge.site`.
- Modify `codesense/ql/compile/spec.py`, `build.py`, `validate.py`, `plan.py`, `planner.py`, and `emit.py`: typed relations, hard target contract, and target projection.
- Modify `codesense/llm/compiler.py` and `codesense/llm/codegen.py`: expose `target`, edge kinds, and `project` to model-generated queries.
- Modify `codesense/search.py` and `codesense/project.py`: normalize, enforce, report, and preserve file targets across fallback.
- Add focused unit tests under `tests/unit/ql/`, `tests/unit/indexing/`, and `tests/unit/llm/`; add one Java integration acceptance test.

---

### Task 1: Add the evidence-preserving `project` operator

**Files:**
- Create: `codesense/ql/operators/project.py`
- Modify: `codesense/ql/operators/__init__.py`
- Test: `tests/unit/ql/test_project_operator.py`

**Interfaces:**
- Consumes: `Frag`, `Evidence`, `UnitHit`, `EvalContext`, and `EdgeStore.in_edges/out_edges`.
- Produces: `project(src: Frag, ctx: EvalContext, *, edge: str | Sequence[str] = "in_file", direction: str = "forward", kind: str | Sequence[str] | None = None, include_self: bool = False, min_confidence: float = 0.0) -> Frag`.

- [ ] **Step 1: Write failing projection and evidence tests**

Create `tests/unit/ql/test_project_operator.py` with a context containing two methods in one file, one method in another file, and `in_file` edges. Pin the public behavior:

```python
def test_projects_methods_to_their_file_and_keeps_scores(ctx: EvalContext) -> None:
    source = Frag(
        nodes=ctx.symbols.get_many((1,)),
        evidence={1: scored("query", 0.8)},
    )

    result = project(source, ctx, edge="in_file", kind="file")

    assert [element.name for element in result] == ["Controller.java"]
    assert score_of(result, 10) == pytest.approx(0.8)
    assert any(hit.signal == "graph" for hit in result.evidence_for(10).unit_hits)


def test_same_unit_uses_strongest_contained_match_not_file_size(ctx: EvalContext) -> None:
    source = Frag(
        nodes=ctx.symbols.get_many((1, 2)),
        evidence={1: scored("query", 0.8), 2: scored("query", 0.4)},
    )

    result = project(source, ctx, edge="in_file", kind="file")

    assert score_of(result, 10) == pytest.approx(0.8)
```

Also test backward projection, several edge kinds, `min_confidence`, `kind` filtering, empty input, and these two `include_self` cases:

```python
assert set(project(page_request, ctx, edge="references", direction="backward").nodes) == {1}
assert 10 in project(file_frag, ctx, kind="file", include_self=True).nodes
```

- [ ] **Step 2: Run the new tests and verify the missing operator failure**

Run:

```bash
conda run -n codesearch pytest tests/unit/ql/test_project_operator.py -v
```

Expected: collection fails because `project` is not exported from `codesense.ql.operators`.

- [ ] **Step 3: Implement exact one-hop projection**

Implement `codesense/ql/operators/project.py` with validated directions and indexed edge lookup:

```python
def project(
    src: Frag,
    ctx: EvalContext,
    *,
    edge: str | Sequence[str] = "in_file",
    direction: str = "forward",
    kind: str | Sequence[str] | None = None,
    include_self: bool = False,
    min_confidence: float = 0.0,
) -> Frag:
    if direction not in {"forward", "backward"}:
        raise ValueError("direction must be 'forward' or 'backward'")
    kinds = (edge,) if isinstance(edge, str) else tuple(edge)
    wanted = None if kind is None else frozenset((kind,) if isinstance(kind, str) else kind)
    nodes: dict[int, Element] = {}
    evidence: dict[int, Evidence] = {}

    for source_id in sorted(src.nodes):
        if include_self and (wanted is None or src.nodes[source_id].kind in wanted):
            nodes[source_id] = src.nodes[source_id]
            evidence[source_id] = evidence.get(source_id, Evidence()).merge(
                src.evidence_for(source_id)
            )
        selected = (
            ctx.edges.out_edges(source_id, kinds=kinds, min_confidence=min_confidence)
            if direction == "forward"
            else ctx.edges.in_edges(source_id, kinds=kinds, min_confidence=min_confidence)
        )
        for relation in selected:
            target_id = relation.target_id if direction == "forward" else relation.source_id
            target = ctx.symbols.get(target_id)
            if target is None or (wanted is not None and target.kind not in wanted):
                continue
            graph_hit = UnitHit(
                unit="projection",
                signal="graph",
                detail=f"{direction} {relation.kind} via {relation.provenance}",
                field=relation.kind,
                score=0.0,
                span=relation.site,
            )
            carried = src.evidence_for(source_id).merge(Evidence(unit_hits=(graph_hit,)))
            nodes[target_id] = target
            evidence[target_id] = evidence.get(target_id, Evidence()).merge(carried)
    return Frag(nodes=nodes, evidence=evidence)
```

Export `project` from `codesense/ql/operators/__init__.py`. Keep the returned Frag node-only; graph evidence records how the projection happened without adding score.

- [ ] **Step 4: Run projection tests and QL regression tests**

Run:

```bash
conda run -n codesearch pytest tests/unit/ql/test_project_operator.py tests/unit/ql/test_frag.py tests/unit/ql/test_hop.py tests/unit/ql/test_select.py -v
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit the operator**

```bash
git add codesense/ql/operators/project.py codesense/ql/operators/__init__.py tests/unit/ql/test_project_operator.py
git commit -m "feat: add evidence preserving graph projection"
```

---

### Task 2: Index file Elements and `in_file` edges

**Files:**
- Modify: `codesense/indexing/pipeline.py`
- Modify: `tests/unit/lang/test_registry.py`
- Create: `tests/unit/indexing/test_file_nodes.py`

**Interfaces:**
- Consumes: the existing `Language.scan(source) -> Sequence[Declaration]` contract; Task 5 upgrades the return type later.
- Produces: payload keys `symbols`, `postings`, `edges`, and `declaration_count`; file rows use `kind="file"`; every declaration has one `in_file` edge.

- [ ] **Step 1: Write failing file-node pipeline tests**

Create `tests/unit/indexing/test_file_nodes.py` using a local toy adapter and temporary `.toy` files:

```python
def test_appends_one_file_element_after_declarations(tmp_path: Path) -> None:
    (tmp_path / "b.toy").write_text("run\n", encoding="utf-8")
    (tmp_path / "a.toy").write_text("", encoding="utf-8")

    result = build_index(tmp_path, languages=[ToyLanguage()])
    declarations = [row for row in result.payload["symbols"] if row["kind"] != "file"]
    files = [row for row in result.payload["symbols"] if row["kind"] == "file"]

    assert [row["name"] for row in declarations] == ["run"]
    assert [row["file"] for row in files] == ["a.toy", "b.toy"]
    assert files[0]["span"] == [1, 1]
    assert result.payload["declaration_count"] == 1


def test_every_declaration_points_to_exactly_one_file(tmp_path: Path) -> None:
    (tmp_path / "a.toy").write_text("read\nwrite\n", encoding="utf-8")
    result = build_index(tmp_path, languages=[ToyLanguage()])
    edges = [edge for edge in result.payload["edges"] if edge["kind"] == "in_file"]

    assert len(edges) == 2
    assert {edge["source_id"] for edge in edges} == {1, 2}
    assert len({edge["target_id"] for edge in edges}) == 1
    assert all(edge["confidence"] == 1.0 for edge in edges)
    assert all(edge["provenance"] == "source_path" for edge in edges)
```

Add a parse-failure test asserting no file node is emitted for the failed file, and update the existing registry expectations from two declarations to two declarations plus one file Element while keeping `declaration_count == 2`.

- [ ] **Step 2: Run the pipeline tests and verify they fail**

Run:

```bash
conda run -n codesearch pytest tests/unit/indexing/test_file_nodes.py tests/unit/lang/test_registry.py -v
```

Expected: tests fail because no file rows, `in_file` edges, or `declaration_count` exist.

- [ ] **Step 3: Collect file records and append stable file nodes**

Add a private pipeline record:

```python
@dataclass(frozen=True, slots=True)
class _IndexedFile:
    path: str
    language: str
    lines: int
    declaration_ids: tuple[int, ...]
```

During `_scan_language`, read source once, scan it, create declaration rows exactly as today, and append an `_IndexedFile`. After every language scan, append file rows sorted by `(path, language)`:

```python
file_id = len(symbols) + 1
symbols.append(
    {
        "symbol_id": file_id,
        "name": Path(record.path).name,
        "kind": "file",
        "file": record.path,
        "span": [1, record.lines],
        "signature": "",
        "container": "",
        "doc": "",
        "language": record.language,
        "modifiers": [],
    }
)
```

Use `max(1, len(source.splitlines()))` for the file span. Emit one edge per declaration ID:

```python
{
    "source_id": declaration_id,
    "target_id": file_id,
    "kind": "in_file",
    "site": None,
    "confidence": 1.0,
    "provenance": "source_path",
}
```

Set `stats.declarations` before file nodes are appended, `stats.symbols` after they are appended, and store `payload["declaration_count"]`.

- [ ] **Step 4: Run pipeline and language-extension regressions**

Run:

```bash
conda run -n codesearch pytest tests/unit/indexing/test_file_nodes.py tests/unit/indexing/test_index_build.py tests/unit/lang/test_registry.py -v
```

Expected: all selected tests pass; the toy adapter still requires no indexing-specific implementation.

- [ ] **Step 5: Commit file nodes**

```bash
git add codesense/indexing/pipeline.py tests/unit/indexing/test_file_nodes.py tests/unit/lang/test_registry.py
git commit -m "feat: index source files as elements"
```

---

### Task 3: Persist the new index shape without changing scoring populations

**Files:**
- Modify: `codesense/index.py`
- Modify: `codesense/project.py`
- Modify: `codesense/ql/context.py`
- Modify: `codesense/ql/compile/plan.py`
- Modify: `codesense/ql/compile/planner.py`
- Modify: `codesense/ql/compile/validate.py`
- Modify: `tests/unit/test_index.py`
- Modify: `tests/unit/ql/compile/test_planner.py`

**Interfaces:**
- Consumes: Task 2's `payload["declaration_count"]` and file nodes.
- Produces: index format v2, `IndexMeta.declarations`, `IndexMeta.files`, `EvalContext.population`, and full `Edge.site` round-trip.

- [ ] **Step 1: Write failing round-trip and population tests**

Extend the manual payload in `tests/unit/test_index.py` with a file node, `declaration_count: 2`, and `site: [20, 8]` on an edge. Add:

```python
def test_edges_keep_their_site(self) -> None:
    edge = make_index().to_context().edges.out_edges(1)[0]
    assert edge.site == (20, 8)


def test_context_separates_element_count_from_scoring_population(self) -> None:
    ctx = make_index().to_context()
    assert ctx.symbols.count() == 3
    assert ctx.population == 2
    assert ctx.postings.term_info("alloc").total_symbols == 2
```

In `tests/unit/ql/compile/test_planner.py`, construct 1,000 declaration nodes plus 200 file nodes with `declaration_count=1_000`, then assert a 900-row unit is treated as 90%, not 75%.

- [ ] **Step 2: Run persistence and planner tests and verify failures**

Run:

```bash
conda run -n codesearch pytest tests/unit/test_index.py tests/unit/ql/compile/test_planner.py -v
```

Expected: failures show that `Edge.site` is dropped and planning still reads `symbols.count()`.

- [ ] **Step 3: Add an injected declaration population**

Add to `EvalContext`:

```python
declaration_count: int | None = None

@property
def population(self) -> int:
    return self.symbols.count() if self.declaration_count is None else self.declaration_count
```

Replace scoring/selectivity uses of `ctx.symbols.count()` in `compile/plan.py`, `compile/planner.py`, and relation validation with `ctx.population`. Keep operations that genuinely describe all graph nodes on `symbols.count()`.

- [ ] **Step 4: Bump and round-trip the index format**

Set `FORMAT_VERSION = 2`. Add `declarations` and `files` fields to `IndexMeta`. In `Index.to_context()` use:

```python
declaration_count = self.payload.get(
    "declaration_count",
    sum(row["kind"] != "file" for row in self.payload["symbols"]),
)
```

Pass that value to both `InMemoryPostingIndex(total_symbols=...)` and `EvalContext(declaration_count=...)`. Restore an optional site with:

```python
site=tuple(e["site"]) if e.get("site") is not None else None
```

In `Project.build()`, populate the new meta fields and pass `stats.declarations`, not `stats.symbols`, to `ground_vocabulary_result()`.

- [ ] **Step 5: Run index, planner, grounding, and project tests**

Run:

```bash
conda run -n codesearch pytest tests/unit/test_index.py tests/unit/ql/compile/test_planner.py tests/unit/indexing/test_grounding.py tests/unit/test_search.py -v
```

Expected: all selected tests pass and term ICF remains based on declarations.

- [ ] **Step 6: Commit persistence and population changes**

```bash
git add codesense/index.py codesense/project.py codesense/ql/context.py codesense/ql/compile/plan.py codesense/ql/compile/planner.py codesense/ql/compile/validate.py tests/unit/test_index.py tests/unit/ql/compile/test_planner.py
git commit -m "feat: persist file graph index format"
```

---

### Task 4: Add the hard file-target contract to planning and search

**Files:**
- Modify: `codesense/ql/compile/spec.py`
- Modify: `codesense/ql/compile/build.py`
- Modify: `codesense/ql/compile/plan.py`
- Modify: `codesense/ql/compile/planner.py`
- Modify: `codesense/ql/compile/emit.py`
- Modify: `codesense/ql/compile/__init__.py`
- Modify: `codesense/search.py`
- Modify: `codesense/project.py`
- Modify: `tests/unit/ql/compile/test_planner.py`
- Modify: `tests/unit/ql/compile/test_emit.py`
- Modify: `tests/unit/test_search.py`
- Modify: `tests/unit/test_index.py`

**Interfaces:**
- Consumes: Task 1's `project()` and Task 2's `in_file` edges.
- Produces: `normalise_target(raw) -> tuple[str, ...]`, `QuerySpec.target`, `ProjectTarget` plan step, `search(..., target: str | Sequence[str] | None = None)`, and `SearchResult.target`.

- [ ] **Step 1: Write failing QuerySpec and plan-step tests**

Add target normalization tests:

```python
assert QuerySpec.from_dict(payload_with(target="file")).target == ("file",)
assert QuerySpec.from_dict(payload_with(target=["FILE", "unknown"])).target == ("file",)
assert QuerySpec.from_dict(payload_with()).target == ()
```

Add a planner context with a method -> file `in_file` edge and assert the target step follows semantic judging:

```python
steps = plan(spec(target=["file"], concept="judge"), ctx).steps
assert isinstance(steps[-1], ProjectTarget)
assert isinstance(steps[-2], Intent)
assert {element.kind for element in plan(spec(target=["file"]), ctx).run(ctx).current} == {
    "file"
}
```

Extend emitted-script equivalence to assert `project(` appears and running the script equals `Plan.run()`.

- [ ] **Step 2: Write failing public search target tests**

Extend `make_searchable_index()` with file nodes and `in_file` edges, then add:

```python
def test_explicit_file_target_returns_only_files(ctx: EvalContext) -> None:
    result = search("alloc", ctx, route="lexical", target="file")
    assert result.target == ("file",)
    assert {hit.kind for hit in result.hits} == {"file"}
    assert [hit.file for hit in result.hits] == ["a/Pooled.java"]


def test_default_search_never_leaks_file_nodes(ctx: EvalContext) -> None:
    result = search("alloc", ctx, route="lexical")
    assert all(hit.kind != "file" for hit in result.hits)


def test_fallback_preserves_file_target(ctx: EvalContext) -> None:
    result = search("alloc files", ctx, route="codegen", llm=Exploding())
    assert result.route == "lexical"
    assert result.target == ("file",)
    assert {hit.kind for hit in result.hits} == {"file"}
    assert any("lexical approximation" in note for note in result.notes)
```

- [ ] **Step 3: Run target tests and verify failures**

Run:

```bash
conda run -n codesearch pytest tests/unit/ql/compile/test_planner.py tests/unit/ql/compile/test_emit.py tests/unit/test_search.py -v
```

Expected: failures show that `QuerySpec` ignores target and public search cannot accept `target`.

- [ ] **Step 4: Implement target normalization and the plan step**

In `compile/spec.py`, accept only the current hard target:

```python
def normalise_target(raw: object) -> tuple[str, ...]:
    if isinstance(raw, str):
        values = [raw]
    elif isinstance(raw, (list, tuple, set, frozenset)):
        values = list(raw)
    else:
        values = []
    return ("file",) if any(str(value).strip().lower() == "file" for value in values) else ()
```

Add `target` to `QuerySpec` and `build_spec(..., target: object = None)`. Add `ProjectTarget` in `compile/plan.py`:

```python
@dataclass(slots=True)
class ProjectTarget(Step):
    target: tuple[str, ...]
    label: str = ""

    def __post_init__(self) -> None:
        self.label = f"target({'/'.join(self.target)})"

    def estimate(self, ctx: EvalContext, state: State) -> Estimate:
        return Estimate(rows=state.rows, cost=float(state.rows), detail="one graph hop")

    def apply(self, ctx: EvalContext, state: State) -> None:
        state.current = project(
            state.current,
            ctx,
            edge="in_file",
            kind=self.target,
            include_self=True,
        )
```

For a target without `concept`, place `ProjectTarget` before the final `Narrow`, so `limit` applies to aggregated
files rather than declaration candidates. For a target with `concept`, use the exact order
`Narrow(INTENT_INPUT_CAP) -> Intent -> ProjectTarget -> Narrow(spec.limit or 100)`: judging still sees code
elements, while the public result limit still counts files. Render the target step in `emit.py` with
`frag = project(...)` and import `project` in the generated header.

- [ ] **Step 5: Normalize and enforce target once at the public boundary**

Add a conservative helper matching `\bfiles?\b` or the Chinese noun `文件`, without using relation verbs:

```python
def _query_target(query: str) -> tuple[str, ...]:
    return ("file",) if re.search(r"\bfiles?\b|文件", query, re.IGNORECASE) else ()
```

Explicit `search(target=...)` wins over inference. Pass the effective tuple through runners; after route fallback and judging, enforce it with Task 1's `project`. With no target, remove any file nodes through `Frag.induced`. Add the target to `SearchResult.explain()`.

Define the failing-route helper at module scope so every target test can use it:

```python
class Exploding:
    model = "boom"

    def __getattr__(self, name: str) -> object:
        raise RuntimeError("endpoint is down")
```

- [ ] **Step 6: Fix the existing planned-route call while threading target**

Replace the invalid `_planned()` call with explicit arguments and tuple unpacking:

```python
spec, validation_notes = build_spec(
    query,
    understood["terms"],
    ctx,
    concept=understood.get("concept", ""),
    annotations=understood.get("annotations", ()),
    groups=understood.get("groups"),
    relations=understood.get("relations", ()),
    target=target or understood.get("target"),
)
execution = plan(spec, ctx)
```

Return `validation_notes` together with execution reasoning. Add a fake `QueryUnderstanding.understand()` regression proving the route remains `planned` instead of falling back.

- [ ] **Step 7: Run search, planner, emitter, and facade tests**

Run:

```bash
conda run -n codesearch pytest tests/unit/test_search.py tests/unit/test_index.py tests/unit/ql/compile/test_planner.py tests/unit/ql/compile/test_emit.py -v
```

Expected: all selected tests pass; every file target returns `Hit.kind == "file"`.

- [ ] **Step 8: Commit the target contract**

```bash
git add codesense/ql/compile/spec.py codesense/ql/compile/build.py codesense/ql/compile/plan.py codesense/ql/compile/planner.py codesense/ql/compile/emit.py codesense/ql/compile/__init__.py codesense/search.py codesense/project.py tests/unit/ql/compile/test_planner.py tests/unit/ql/compile/test_emit.py tests/unit/test_search.py tests/unit/test_index.py
git commit -m "feat: add hard file result targets"
```

---

### Task 5: Upgrade the language scan contract and extract Java references once

**Files:**
- Modify: `codesense/lang/base.py`
- Modify: `codesense/lang/__init__.py`
- Modify: `codesense/lang/java/scanner.py`
- Modify: `codesense/lang/java/__init__.py`
- Modify: `codesense/indexing/pipeline.py`
- Modify: `tests/unit/lang/test_registry.py`
- Modify: `tests/unit/lang/test_java_scanner.py`

**Interfaces:**
- Consumes: Java source text and the existing injected tree-sitter parser.
- Produces: `ReferenceUse`, `ScanResult`, and `Language.scan(source) -> ScanResult`; `ScanResult.references` includes type and import uses with occurrence sites.

- [ ] **Step 1: Write failing contract and Java extraction tests**

Update `ToyLanguage.scan()` to return `ScanResult(declarations=...)`. Change the Java fixture to include:

```java
import org.springframework.data.domain.PageRequest;

class Controller {
    PageRequest list() {
        return PageRequest.ofSize(20);
    }
}
```

Add:

```python
def test_scan_result_keeps_declarations_and_references(scanner) -> None:
    result = scanner.scan(REFERENCE_SOURCE)
    assert {declaration.name for declaration in result.declarations} >= {"Controller", "list"}
    page_request = [use for use in result.references if use.name == "PageRequest"]
    assert {use.relation for use in page_request} >= {"imports", "references"}
    assert all(use.line > 0 and use.column >= 0 for use in page_request)


def test_import_keeps_the_qualified_target(scanner) -> None:
    imported = next(use for use in scanner.scan(REFERENCE_SOURCE).references if use.relation == "imports")
    assert imported.qualified_name == "org.springframework.data.domain.PageRequest"
    assert imported.target_kind == "type"
```

- [ ] **Step 2: Run language tests and verify the contract failure**

Run:

```bash
conda run -n codesearch pytest tests/unit/lang/test_registry.py tests/unit/lang/test_java_scanner.py -v
```

Expected: failures show that `ScanResult` and `ReferenceUse` do not exist.

- [ ] **Step 3: Define the language-neutral scan types**

Add to `codesense/lang/base.py`:

```python
@dataclass(frozen=True, slots=True)
class ReferenceUse:
    name: str
    line: int
    column: int = 0
    relation: str = "references"
    target_kind: str = ""
    qualified_name: str = ""


@dataclass(frozen=True, slots=True)
class ScanResult:
    declarations: tuple[Declaration, ...]
    references: tuple[ReferenceUse, ...] = ()
```

Export both types and change `Language.scan()` to return `ScanResult`. Update pipeline consumption from iterating the scan result directly to `scan_result.declarations`.

- [ ] **Step 4: Extract declarations and references from the same Java tree**

In `JavaDeclarationScanner.scan()`, parse once and retain the root:

```python
def scan(self, source: str) -> ScanResult:
    data = source.encode("utf-8")
    tree = self._parser.parse(data)
    root = tree.root_node
    return ScanResult(
        declarations=tuple(self._walk(root, data)),
        references=tuple(_reference_uses(root, data)),
    )
```

`_reference_uses()` must:

- convert each `import_declaration` to one `ReferenceUse(relation="imports", target_kind="type")`;
- collect `type_identifier` nodes as `references` uses;
- collect uppercase simple receivers such as `PageRequest` in `PageRequest.ofSize(...)` as type references;
- keep `Invocation.line` from each `method_invocation` so derived call/reference edges can persist their site;
- deduplicate by `(name, line, column, relation, qualified_name)` while preserving traversal order;
- record `line = node.start_point[0] + 1` and `column = node.start_point[1]`;
- ignore empty names and wildcard-only import suffixes.

Update `JavaLanguage.scan()` to return `ScanResult` and make `annotations(source)` read `scan(source).declarations`.

- [ ] **Step 5: Run language and pipeline tests**

Run:

```bash
conda run -n codesearch pytest tests/unit/lang/test_java_scanner.py tests/unit/lang/test_registry.py tests/unit/indexing/test_file_nodes.py -v
```

Expected: all selected tests pass, including the minimal adapter protocol test.

- [ ] **Step 6: Commit the scan contract**

```bash
git add codesense/lang/base.py codesense/lang/__init__.py codesense/lang/java/scanner.py codesense/lang/java/__init__.py codesense/indexing/pipeline.py tests/unit/lang/test_registry.py tests/unit/lang/test_java_scanner.py
git commit -m "feat: extract references during language scans"
```

---

### Task 6: Resolve and materialize `references` and `imports` edges

**Files:**
- Modify: `codesense/indexing/graph.py`
- Modify: `codesense/indexing/pipeline.py`
- Modify: `codesense/indexing/__init__.py`
- Create: `tests/unit/indexing/test_reference_graph.py`
- Modify: `tests/unit/indexing/test_file_nodes.py`

**Interfaces:**
- Consumes: Task 5's `ScanResult.references`, per-file declaration IDs/spans, and Task 2's file IDs.
- Produces: `GraphBuilder.observe_file(path, file_id, declarations, references)`, project-local `references`/`imports` edges, and reference-specific build statistics.

- [ ] **Step 1: Write failing graph-resolution tests**

Create a direct GraphBuilder test with a `PageRequest` type, a method spanning lines 5-10, a file node, and three uses. Assert:

```python
def test_method_type_reference_points_to_the_type() -> None:
    edges = built_reference_edges()
    assert edge_keys(edges) >= {(2, 1, "references")}


def test_import_is_owned_by_the_file_and_also_counts_as_a_reference() -> None:
    edges = built_reference_edges()
    assert edge_keys(edges) >= {
        (10, 1, "imports"),
        (10, 1, "references"),
    }


def test_ambiguous_reference_above_the_cap_is_not_materialized() -> None:
    builder = builder_with_nine_same_named_targets()
    assert not [edge for edge in builder.build() if edge["kind"] == "references"]
    assert builder.stats.reference_ambiguous == 1
```

Also assert exact `site`, unique-qualified-name preference, smallest enclosing declaration ownership, file fallback outside declaration spans, and unresolved external type counts.

- [ ] **Step 2: Run graph tests and verify missing API failures**

Run:

```bash
conda run -n codesearch pytest tests/unit/indexing/test_reference_graph.py -v
```

Expected: tests fail because GraphBuilder has no file/reference observation API.

- [ ] **Step 3: Extend GraphBuilder's symbol and file observations**

Add reference-specific state without changing existing calls/contains tables:

```python
MAX_REFERENCE_CANDIDATES = 8
QUALIFIED_REFERENCE_CONFIDENCE = 1.0
SIMPLE_REFERENCE_CONFIDENCE = 0.8

@dataclass
class GraphStats:
    # existing fields remain
    references: int = 0
    imports: int = 0
    reference_ambiguous: int = 0
    reference_unresolved: int = 0
```

Record every indexable declaration by qualified and simple name, not only callable declarations. Add this
pending file observation and `observe_file()` implementation:

```python
@dataclass(frozen=True, slots=True)
class _FileReferences:
    path: str
    file_id: int
    declarations: tuple[tuple[Declaration, int], ...]
    references: tuple[ReferenceUse, ...]


def observe_file(
    self,
    path: str,
    file_id: int,
    declarations: Sequence[tuple[Declaration, int]],
    references: Sequence[ReferenceUse],
) -> None:
    self._file_references.append(
        _FileReferences(
            path=path,
            file_id=file_id,
            declarations=tuple(declarations),
            references=tuple(references),
        )
    )
```

Initialize `self._file_references: list[_FileReferences] = []` in the constructor. Precompute each file's
declaration intervals once. For every use, choose the containing declaration with the smallest
`(end_line - line, symbol_id)`; use `file_id` when none contains the occurrence.

- [ ] **Step 4: Resolve target candidates and deduplicate edges**

Resolve `qualified_name` first, otherwise simple `name`. Skip empty candidate sets and sets larger than `MAX_REFERENCE_CANDIDATES`. For accepted candidates, use `ceiling / len(candidates)` and emit:

```python
{
    "source_id": owner_id,
    "target_id": target_id,
    "kind": use.relation,
    "site": [use.line, use.column],
    "confidence": round(confidence, 4),
    "provenance": provenance,
}
```

For `imports`, emit both `imports` and `references`. When existing call resolution emits a `calls` edge, also emit a broad `references` edge with the same endpoints. Deduplicate all graph rows by `(source_id, target_id, kind)`, keeping the highest-confidence row and its site.

Set call and derived-reference sites from `Invocation.line` as `[call.line, 0]`; preserve `None` only for old
hand-built declarations that carry no line.

- [ ] **Step 5: Wire pending file records into GraphBuilder**

Extend Task 2's `_IndexedFile` to retain `tuple[tuple[Declaration, int], ...]` and `references`. After file IDs are assigned, call the language's GraphBuilder `observe_file()` before `build()`. Aggregate the new statistics into pipeline `Stats`.

- [ ] **Step 6: Run graph, pipeline, and operator tests**

Run:

```bash
conda run -n codesearch pytest tests/unit/indexing/test_reference_graph.py tests/unit/indexing/test_file_nodes.py tests/unit/ql/test_project_operator.py -v
```

Expected: all selected tests pass; `project(target, direction="backward", edge="references")` reaches the recorded owner.

- [ ] **Step 7: Commit reference graph construction**

```bash
git add codesense/indexing/graph.py codesense/indexing/pipeline.py codesense/indexing/__init__.py tests/unit/indexing/test_reference_graph.py tests/unit/indexing/test_file_nodes.py
git commit -m "feat: materialize project reference edges"
```

---

### Task 7: Teach compiler and codegen about target and typed relations

**Files:**
- Modify: `codesense/llm/compiler.py`
- Modify: `codesense/llm/codegen.py`
- Modify: `codesense/ql/compile/build.py`
- Modify: `codesense/ql/compile/validate.py`
- Modify: `codesense/ql/compile/spec.py`
- Modify: `codesense/search.py`
- Create: `tests/unit/llm/test_compiler.py`
- Create: `tests/unit/llm/test_codegen_prompt.py`
- Create: `tests/unit/ql/compile/test_relations.py`

**Interfaces:**
- Consumes: Task 4's target tuple and Task 6's edge kinds.
- Produces: relation proposal tuple `(src: str, dst: str, edge: tuple[str, ...])`; validation restricted to proposed edge kinds; codegen access to `project`.

- [ ] **Step 1: Write failing compiler parsing tests**

Use a fake HTTP session returning:

```json
{
  "terms": {"page": 1.0, "request": 1.0},
  "groups": {"page request": ["page", "request"]},
  "relations": [
    {"src": "clients", "dst": "page request", "edge": ["references"]}
  ],
  "target": ["file"],
  "annotations": [],
  "concept": "Files whose code references PageRequest"
}
```

Assert:

```python
understood = QueryUnderstanding(config, session=fake).understand(query, "demo", vocabulary)
assert understood["target"] == ("file",)
assert understood["relations"] == [("clients", "page request", ("references",))]
```

Also pin backward compatibility: a two-item relation list becomes `(src, dst, ("calls", "contains"))`, malformed edge values are discarded, and relation verbs alone do not set target.

- [ ] **Step 2: Write failing edge-specific validation tests**

Create contexts where the same node pairs have only `calls` edges or only `references` edges. Assert:

```python
assert relation_lift(left, right, ctx, edge=("references",))[0] == 0.0
assert relation_lift(left, right, ctx, edge=("calls",))[0] >= LIFT_FLOOR
```

Then assert an accepted `Relation` retains its edge tuple and `build_spec()` copies it into `GraphConstraint.edge`.

- [ ] **Step 3: Run compiler and validation tests and verify failures**

Run:

```bash
conda run -n codesearch pytest tests/unit/llm/test_compiler.py tests/unit/ql/compile/test_relations.py -v
```

Expected: failures show that relation edge kinds and target are currently discarded.

- [ ] **Step 4: Parse target and typed relation proposals**

Update the compiler prompt's JSON contract to include `target` and relation objects. Normalize model output to:

```python
relation = (src, dst, tuple(valid_edge_names) or ("calls", "contains"))
```

Return `target=normalise_target(payload.get("target"))`. In `compile/validate.py`, add `edge: tuple[str, ...]` to `Relation`, pass `kinds=edge` to `out_edges()` and `degree()`, and retain the tuple after acceptance. In `build.py`, construct `GraphConstraint(..., edge=r.edge)`.

- [ ] **Step 5: Write and run failing codegen prompt/namespace tests**

Assert `PROMPT` documents `project`, `references`, `imports`, and `in_file`, and assert a generated script calling `project()` passes `run_script()` through `search._namespace()`.

Run:

```bash
conda run -n codesearch pytest tests/unit/llm/test_codegen_prompt.py tests/unit/ql/test_script.py -v
```

Expected before implementation: the prompt test fails and `project` is absent from the search namespace.

- [ ] **Step 6: Expose project to safe generated scripts**

Add to the codegen operator reference:

```text
project(frag, ctx, *, edge="in_file", direction="forward",
        kind=None, include_self=False, min_confidence=0.0) -> Frag
```

Include the PageRequest backward-reference then forward-`in_file` example. Add `project` to `search._namespace()`; the script validator already derives allowed top-level calls from the supplied namespace, so do not create a second whitelist source.

- [ ] **Step 7: Run compiler, script, planner, and search regressions**

Run:

```bash
conda run -n codesearch pytest tests/unit/llm/test_compiler.py tests/unit/llm/test_codegen_prompt.py tests/unit/ql/compile/test_relations.py tests/unit/ql/compile/test_planner.py tests/unit/ql/compile/test_emit.py tests/unit/ql/test_script.py tests/unit/test_search.py -v
```

Expected: all selected tests pass.

- [ ] **Step 8: Commit compiler and codegen integration**

```bash
git add codesense/llm/compiler.py codesense/llm/codegen.py codesense/ql/compile/build.py codesense/ql/compile/validate.py codesense/ql/compile/spec.py codesense/search.py tests/unit/llm/test_compiler.py tests/unit/llm/test_codegen_prompt.py tests/unit/ql/compile/test_relations.py
git commit -m "feat: compile file targets and reference relations"
```

---

### Task 8: Prove the PageRequest query end to end and finish documentation

**Files:**
- Create: `tests/integration/test_file_target_queries.py`
- Modify: `docs/design/03-data-model.md`
- Modify: `docs/design/05-operators.md`
- Modify: `docs/design/10-graph.md`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Consumes: all prior tasks.
- Produces: an executable acceptance case for `Find Java files containing references to the PageRequest class.` and updated user/developer documentation.

- [ ] **Step 1: Write the failing Java acceptance fixture**

In a temporary project write four files:

```java
// PageRequest.java
package demo;
public class PageRequest {}

// Controller.java
package demo;
import demo.PageRequest;
public class Controller {
    public PageRequest list() { return new PageRequest(); }
}

// Unrelated.java
package demo;
public class Unrelated { public String value() { return "none"; } }

// Empty.java is empty
```

Build an in-memory project index. Test the graph directly:

```python
page_request = only(
    eval_unit(page_request_unit, project.context),
    kind="class",
)
referencers = project_op(
    page_request,
    project.context,
    edge="references",
    direction="backward",
)
files = project_op(referencers, project.context, edge="in_file", kind="file")
assert {element.file for element in files} == {"Controller.java"}
```

Use the alias `project_op` in this test so it does not shadow the `Project` fixture.

- [ ] **Step 2: Add a deterministic codegen-route acceptance test**

Build the project with `llm=object()` so search does not degrade before the monkeypatched generator runs.
Monkeypatch `ScriptGenerator.generate()` to return this fixed QL script:

```python
page_request_unit = QueryUnit(
    "page_request",
    satisfiers=(LexicalSatisfier(terms=(Term("page"), Term("request"))),),
)
page_request = only(eval_unit(page_request_unit, ctx), kind="class")
referencers = project(
    page_request,
    ctx,
    edge="references",
    direction="backward",
)
answer = project(
    referencers,
    ctx,
    edge="in_file",
    kind="file",
    include_self=True,
)
```

Then call:

```python
result = project.search(
    "Find Java files containing references to the PageRequest class.",
    route="codegen",
    target="file",
)

assert result.route == "codegen"
assert [hit.file for hit in result.hits] == ["Controller.java"]
assert all(hit.kind == "file" for hit in result.hits)
assert "PageRequest.java" not in {hit.file for hit in result.hits}
```

Add a second script using `edge="imports"` and assert only `Controller.java` is returned. Mark the module `pytest.mark.slow` and call `pytest.importorskip("tree_sitter_languages")` so missing optional grammar support is reported as a skip, not a false product failure.

- [ ] **Step 3: Run the acceptance test and verify failures before final wiring fixes**

Run:

```bash
conda run -n codesearch pytest tests/integration/test_file_target_queries.py -v
```

Expected on the first run: any missed end-to-end wiring fails at the exact file/result assertion. Apply only the wiring corrections required by the observed failure, then rerun until the module passes.

- [ ] **Step 4: Update permanent design documentation**

Document these exact contracts:

- `Element.kind="file"` is a real node, not a display-only result;
- `declaration --in_file--> file` is physical ownership and excluded from default coherence;
- `source element --references--> target element` uses the smallest indexable source;
- `project()` performs an evidence-preserving one-hop target conversion;
- `target=("file",)` is hard while `kinds` remains a preference;
- direct path/glob postings and arbitrary content regex remain unsupported.

Add one completed feature entry to `CHANGELOG.md`, after implementation and acceptance tests are green, per repository policy.

- [ ] **Step 5: Run focused affected tests**

Run:

```bash
conda run -n codesearch pytest tests/unit/ql tests/unit/indexing tests/unit/lang tests/unit/llm tests/unit/test_index.py tests/unit/test_search.py tests/integration/test_file_target_queries.py -v
```

Expected: all affected tests pass, with only documented optional-dependency skips.

- [ ] **Step 6: Run full repository verification**

Run:

```bash
conda run -n codesearch ruff check .
conda run -n codesearch ruff format --check .
conda run -n codesearch pytest
```

Expected: each command exits 0. If an unrelated pre-existing failure remains, record its exact command, file, and output separately; do not describe the repository-wide check as passing.

- [ ] **Step 7: Inspect the final change set for scope and artifacts**

Run:

```bash
git status --short
git diff --check
git diff --stat 79b4154..HEAD
```

Expected: no generated indexes, model weights, `output/`, secrets, or unrelated files are included. Confirm the declared file population, file nodes, target contract, project operator, reference edges, route integration, tests, docs, and changelog are all present.

- [ ] **Step 8: Commit acceptance tests and documentation**

```bash
git add tests/integration/test_file_target_queries.py docs/design/03-data-model.md docs/design/05-operators.md docs/design/10-graph.md CHANGELOG.md
git commit -m "test: cover file reference search end to end"
```
