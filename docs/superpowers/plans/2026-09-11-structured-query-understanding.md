# Structured Query Understanding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace planned search's loose JSON response with a Pydantic-defined semantic query IR that supports grounded out-of-vocabulary terms, multiple result kinds, and relations whose returned endpoint is explicit.

**Architecture:** Keep the existing `Project.search -> planned -> build_spec -> plan -> Plan.run` pipeline. Pydantic owns only the LLM boundary; a standard-library term-resolution module makes exact and expanded surfaces consistent across compilation and execution, while existing `project()` supplies both result-relation and file-target projection.

**Tech Stack:** Python 3.10+, Pydantic 2, OpenAI-compatible Chat Completions JSON Schema, CodeSense QL/Frag operators, pytest, ruff.

**Spec:** `docs/superpowers/specs/2026-09-11-structured-query-understanding-design.md`

## Global Constraints

- Preserve the codegen/planned/lexical route structure and lexical fallback behavior.
- Keep Pydantic in `codesense.llm`; `codesense.ql` remains standard-library only.
- LLM terms are semantic inputs and may be absent from the project vocabulary.
- The prompt vocabulary is representative context, not an output whitelist.
- Result targets use union semantics; only `file` triggers `in_file` projection.
- At most one relation may contain a `result` endpoint in the first implementation.
- `unit -> unit` relations remain validated ranking boosts; result-bound relations generate candidates through real graph edges.
- Parameters flow from CLI to constructors; no new configuration file or hard-coded machine path.
- Do not include the API key, full prompt, or unbounded model output in logs.
- Preserve all unrelated dirty-worktree changes and stage only files owned by each task.
- Run Python commands in the `codesearch` conda environment.

---

### Task 1: Define the strict Pydantic query-understanding contract

**Files:**
- Create: `codesense/llm/schema.py`
- Create: `tests/unit/llm/test_schema.py`
- Modify: `codesense/llm/__init__.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Consumes: Pydantic 2 `BaseModel`, `ConfigDict`, `Field`, and `model_validator`.
- Produces: `TermSource`, `TargetKind`, `RelationKind`, `EndpointKind`, `SemanticTerm`, `SemanticUnit`, `RelationEndpoint`, `QueryRelation`, `QueryUnderstandingResult`, and `query_understanding_response_format() -> dict[str, object]`.

- [ ] **Step 1: Write failing schema tests**

Create tests that build one valid PageRequest payload and assert typed parsing, strict nested objects, endpoint validation, and response-format generation:

```python
def payload() -> dict[str, object]:
    return {
        "units": [{
            "name": "page_request",
            "concept": "the PageRequest type",
            "query_terms": ["PageRequest"],
            "terms": [{
                "value": "PageRequest",
                "source": "literal",
                "weight": 1.0,
                "related_query_terms": ["PageRequest"],
                "reason": "named by the query",
            }],
        }],
        "relations": [{
            "source": {"kind": "result", "unit": None},
            "target": {"kind": "unit", "unit": "page_request"},
            "edges": ["references"],
        }],
        "targets": ["file"],
        "annotations": [],
        "criterion": "A file whose code references PageRequest",
    }


def test_valid_payload_parses_to_typed_model() -> None:
    understood = QueryUnderstandingResult.model_validate(payload())
    assert understood.targets == [TargetKind.FILE]
    assert understood.relations[0].source.kind is EndpointKind.RESULT


def test_response_format_is_strict_json_schema() -> None:
    response_format = query_understanding_response_format()
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is True
```

Parametrize failures for an extra nested field, missing field, unknown source/target/edge, weight outside `[0, 1]`, unknown unit endpoint, two result endpoints, same unit on both ends, and two result-bound relations. Assert `pydantic.ValidationError`.

- [ ] **Step 2: Run schema tests and confirm failure**

Run:

```bash
conda run -n codesearch pytest tests/unit/llm/test_schema.py -v
```

Expected: FAIL because `codesense.llm.schema` does not exist.

- [ ] **Step 3: Implement enums, strict models, and cross-field normalization**

Use `ConfigDict(extra="forbid")` on a shared base. Make every provider-returned field required. In `QueryUnderstandingResult` use an `after` validator to validate endpoint references and retain only the first duplicate target or canonical `value.casefold()` term across all units while preserving order. The global term uniqueness is intentional because current QL groups and weights identify terms by canonical value; it prevents conflicting source metadata for one key. Raise for contract ambiguities rather than repairing unknown enum values or invalid endpoint roles.

The endpoint invariant is:

```python
if endpoint.kind is EndpointKind.RESULT and endpoint.unit is not None:
    raise ValueError("result endpoint unit must be null")
if endpoint.kind is EndpointKind.UNIT and endpoint.unit not in unit_names:
    raise ValueError("unit endpoint must name an existing unit")
```

Generate the response format in an `@cache` function so importing the module executes no schema generation. Export the public types from `codesense.llm`.

- [ ] **Step 4: Add the direct Pydantic dependency**

Add `"pydantic>=2.7"` beside the LLM dependencies in `pyproject.toml`; do not rely on `openai` installing it transitively.

- [ ] **Step 5: Run focused tests and lint**

Run:

```bash
conda run -n codesearch pytest tests/unit/llm/test_schema.py -v
conda run -n codesearch ruff check codesense/llm/schema.py tests/unit/llm/test_schema.py pyproject.toml
```

Expected: PASS.

- [ ] **Step 6: Commit the schema contract**

```bash
git add pyproject.toml codesense/llm/schema.py codesense/llm/__init__.py tests/unit/llm/test_schema.py
git commit -m "feat: define structured query understanding schema"
```

---

### Task 2: Send and parse strict Structured Outputs

**Files:**
- Modify: `codesense/llm/compiler.py`
- Modify: `tests/unit/llm/test_compiler.py`

**Interfaces:**
- Consumes: Task 1's `QueryUnderstandingResult` and `query_understanding_response_format()`.
- Produces: `QueryUnderstanding.understand(query: str, project: str, vocabulary: Sequence[tuple[str, int]]) -> QueryUnderstandingResult | None`.

- [ ] **Step 1: Replace loose-response tests with strict-boundary tests**

Make `FakeSession` capture the posted JSON. Return `json.dumps(valid_payload)` as message content and assert:

```python
understood = QueryUnderstanding(config, session=session).understand(
    query, "demo", (("page", 20), ("request", 8))
)
assert isinstance(understood, QueryUnderstandingResult)
body = session.json
assert body["response_format"]["type"] == "json_schema"
assert body["response_format"]["json_schema"]["strict"] is True
assert "page:20" in body["messages"][0]["content"]
```

Add cases for refusal, missing content, invalid JSON, schema-invalid JSON, and HTTP failure; each returns None. Delete tests whose only purpose was accepting list-shaped terms or a missing target field.

- [ ] **Step 2: Run compiler tests and confirm failure**

Run:

```bash
conda run -n codesearch pytest tests/unit/llm/test_compiler.py -v
```

Expected: FAIL because the compiler still posts no response format and returns a dict.

- [ ] **Step 3: Rewrite the prompt as semantic instructions**

Remove the hand-written JSON example and the statement that terms may only come from the vocabulary. Keep concise rules that distinguish literal, synonym, and derived terms; explain that project entries are `term:df` context; require explicit result endpoints for relational result queries; list supported relation and target meanings without duplicating the JSON shape.

- [ ] **Step 4: Implement strict request and parsing**

Post `response_format=query_understanding_response_format()`. After `raise_for_status()`, inspect the first message:

```python
if message.get("refusal"):
    _log.warning("query understanding was refused")
    return None
content = message.get("content")
if not isinstance(content, str) or not content:
    _log.warning("query understanding response has no content")
    return None
return QueryUnderstandingResult.model_validate_json(content)
```

Catch `pydantic.ValidationError` separately as `schema validation failed`; retain the broad request exception guard so planned search can degrade. Do not find braces or retry without schema.

- [ ] **Step 5: Run focused tests and lint**

Run:

```bash
conda run -n codesearch pytest tests/unit/llm/test_schema.py tests/unit/llm/test_compiler.py -v
conda run -n codesearch ruff check codesense/llm/compiler.py tests/unit/llm/test_compiler.py
```

Expected: PASS.

- [ ] **Step 6: Commit the structured HTTP boundary**

```bash
git add codesense/llm/compiler.py tests/unit/llm/test_compiler.py
git commit -m "feat: request strict query understanding output"
```

---

### Task 3: Supply a bounded representative project vocabulary

**Files:**
- Modify: `codesense/project.py`
- Modify: `codesense/cli.py`
- Modify: `codesense/search.py`
- Modify: `tests/unit/test_index.py`
- Modify: `tests/unit/test_cli.py`
- Modify: `tests/unit/test_search.py`

**Interfaces:**
- Consumes: the existing df-descending `Iterable[tuple[str, int]]` returned by `Index.vocabulary()`.
- Produces: `representative_vocabulary(vocabulary: Iterable[tuple[str, int]], *, limit: int, min_df: int) -> list[tuple[str, int]]`, `Project(..., vocab_size: int = 1200, vocab_min_df: int = 2)`, and CLI query flags `--vocab-size` / `--vocab-min-df`.

- [ ] **Step 1: Write failing sampling and parameter-flow tests**

Assert the sampler filters singletons, respects limit, preserves df order, and stops without consuming an unbounded iterator past the required prefix. Add Project tests proving its cached vocabulary uses both constructor parameters. Add CLI parser/wiring tests proving query arguments reach `Project.open()`.

```python
assert representative_vocabulary(
    (("common", 9), ("useful", 2), ("singleton", 1)), limit=2, min_df=2
) == [("common", 9), ("useful", 2)]
```

- [ ] **Step 2: Run the focused tests and confirm failure**

Run:

```bash
conda run -n codesearch pytest tests/unit/test_index.py tests/unit/test_cli.py tests/unit/test_search.py -v
```

Expected: FAIL because `vocab_min_df` and the sampler do not exist.

- [ ] **Step 3: Thread vocabulary parameters through the public entry points**

Add `vocab_size` and `vocab_min_df` keyword-only parameters to `Project.__init__`, `Project.build`, and `Project.open`. The query CLI owns the corresponding integer flags and passes them to `Project.open`; construction APIs pass values into the returned Project instance. Reject negative size and `min_df < 1` with `ValueError`.

- [ ] **Step 4: Implement linear representative sampling**

Place the pure sampler beside `VOCAB_FOR_PROMPT` in `codesense/search.py`. `Project.vocabulary` fetches the df-descending index vocabulary, applies `representative_vocabulary(..., limit=self._vocab_size, min_df=self._vocab_min_df)`, and caches only that bounded result. `Project.search` continues passing the resulting sequence into `_planned` and `_codegen`; neither route gets a second copy of the thresholds. Do not change lexical behavior. Preserve the raw `df` values in the prompt.

- [ ] **Step 5: Run focused tests and lint**

Run:

```bash
conda run -n codesearch pytest tests/unit/test_index.py tests/unit/test_cli.py tests/unit/test_search.py -v
conda run -n codesearch ruff check codesense/project.py codesense/cli.py codesense/search.py tests/unit/test_index.py tests/unit/test_cli.py tests/unit/test_search.py
```

Expected: PASS.

- [ ] **Step 6: Commit representative vocabulary sampling**

```bash
git add codesense/project.py codesense/cli.py codesense/search.py tests/unit/test_index.py tests/unit/test_cli.py tests/unit/test_search.py
git commit -m "feat: bound query prompt vocabulary by frequency"
```

---

### Task 4: Centralize exact and grounded term resolution

**Files:**
- Create: `codesense/ql/term_resolution.py`
- Create: `tests/unit/ql/test_term_resolution.py`
- Modify: `codesense/ql/satisfiers/base.py`
- Modify: `tests/unit/ql/test_unit_eval.py`

**Interfaces:**
- Consumes: `Term`, `ExpansionTable`, `PostingIndex`, and `EvalContext`.
- Produces: `TermResolver(ctx)` with `surfaces(term)`, `postings(term)`, and `symbol_ids(term)` methods plus convenience functions `resolved_surfaces(term, ctx, *, resolver=None)`, `resolved_postings(term, ctx, *, resolver=None)`, and `resolved_symbol_ids(term, ctx, *, resolver=None)`.

- [ ] **Step 1: Write failing resolution tests**

Build an in-memory context where `buffer` has no exact posting but expands to `buf`. Assert:

```python
assert [surface.target for surface in resolved_surfaces("buffer", ctx)] == ["buf"]
assert resolved_symbol_ids("buffer", ctx) == frozenset({1})
```

Add exact-first, duplicate-target, missing-target, posting `(symbol_id, field)` deduplication, and stable-order cases. Extend unit evaluation to prove `Term("buffer", source="derived")` hits `buf` and preserves the `buf←buffer(...)` evidence detail.

- [ ] **Step 2: Run focused tests and confirm failure**

Run:

```bash
conda run -n codesearch pytest tests/unit/ql/test_term_resolution.py tests/unit/ql/test_unit_eval.py -v
```

Expected: FAIL because the shared module does not exist.

- [ ] **Step 3: Implement the standard-library resolver**

Return the exact `Expansion(term, 1.0, "exact")` only when it has `TermInfo`, followed by expansion entries whose targets have postings. Deduplicate surfaces by target and postings by `(symbol_id, field)` with dict insertion order. Keep caching query-local: `TermResolver` owns per-instance dictionaries. Each convenience function uses the supplied resolver or constructs a short-lived one, so no process-global context or unbounded cache is introduced.

- [ ] **Step 4: Refactor lexical evaluation to use the resolver**

Replace the private `_surfaces()` in `satisfiers/base.py`. Preserve `_too_generic`: exact input bypasses the expansion ICF floor, expanded surfaces do not. Ensure expansion scores and reasons remain part of evidence.

- [ ] **Step 5: Run focused tests and lint**

Run:

```bash
conda run -n codesearch pytest tests/unit/ql/test_term_resolution.py tests/unit/ql/test_unit_eval.py tests/unit/ql/test_store.py -v
conda run -n codesearch ruff check codesense/ql/term_resolution.py codesense/ql/satisfiers/base.py tests/unit/ql/test_term_resolution.py tests/unit/ql/test_unit_eval.py
```

Expected: PASS.

- [ ] **Step 6: Commit shared grounding resolution**

```bash
git add codesense/ql/term_resolution.py codesense/ql/satisfiers/base.py tests/unit/ql/test_term_resolution.py tests/unit/ql/test_unit_eval.py
git commit -m "refactor: centralize grounded term resolution"
```

---

### Task 5: Make compilation statistics grounding-aware

**Files:**
- Modify: `codesense/ql/compile/build.py`
- Modify: `codesense/ql/compile/cost.py`
- Modify: `codesense/ql/compile/partition.py`
- Modify: `codesense/ql/compile/validate.py`
- Modify: `tests/unit/ql/compile/test_build.py`
- Modify: `tests/unit/ql/compile/test_cost.py`
- Modify: `tests/unit/ql/compile/test_partition.py`
- Modify: `tests/unit/ql/compile/test_relations.py`

**Interfaces:**
- Consumes: Task 4's `TermResolver`, `resolved_postings`, and `resolved_symbol_ids`.
- Produces: `build_spec(..., resolver: TermResolver | None = None)` support for `Sequence[Term]` and `plan(spec, ctx, *, resolver: TermResolver | None = None)`, with grounded terms participating consistently in kind/field inference, clustering, group validation, relation validation, and cost estimation.

- [ ] **Step 1: Write failing build and inference tests**

Construct a context where only `buf` has postings and `buffer -> buf` is the expansion. Assert `build_spec("buffer", [Term("buffer", source="derived", weight=0.5)], ctx)` succeeds, keeps the canonical `Term`, infers the hit kind/field from `buf`, and reports an individually ungrounded sibling term in notes without failing.

- [ ] **Step 2: Write failing statistical-consistency tests**

Assert grounded canonical terms produce nonzero `estimate_unit`, participate in partition overlap, validate cohesive groups, and produce the same `relation_lift` as supplying the resolved project spelling directly.

- [ ] **Step 3: Run the compile tests and confirm failure**

Run:

```bash
conda run -n codesearch pytest tests/unit/ql/compile/test_build.py tests/unit/ql/compile/test_cost.py tests/unit/ql/compile/test_partition.py tests/unit/ql/compile/test_relations.py -v
```

Expected: FAIL because build/validation still use exact postings and `build_spec` flattens metadata.

- [ ] **Step 4: Preserve QL Term metadata through build_spec**

Extend `_normalise()` to accept `Sequence[Term]` without replacing source, weight, or reason. Replace the exact `known` filter with resolved-surface availability. Build `LexicalSatisfier` from the retained `Term` instances, applying the model weight exactly once.

When validated model groups are present, use their stable names as `QueryUnit.name` instead of renaming them by position to `u0/u1`; inferred partitions keep the existing `q/uN` names. Track which final validated group contains each proposal group's grounded terms so Task 7 can map relation endpoints through any statistical fold-back. Add tests for preserved names and one folded group.

- [ ] **Step 5: Replace exact statistical lookups with resolved postings**

Add optional keyword-only `resolver` parameters to `build_spec`, `infer_kinds`, `infer_fields`, `partition`, `_cohesion`, `validate_groups`, `relation_lift`, `validate_relations`, `estimate_unit`, and `plan`. When omitted, each public entry point constructs a short-lived resolver; internal calls pass the same instance onward. `partition`, `_cohesion`, `validate_groups`, and `relation_lift` calculate symbol sets from resolved postings; `estimate_unit` counts resolved surfaces and deduplicated postings. Task 8 will create one resolver per planned query and share it across `build_spec` and `plan`, so compilation validation and cost estimation reuse the same query-local cache.

- [ ] **Step 6: Record partial and total grounding failures**

Append deterministic notes such as `ignored ungrounded term 'latency'`. If no term has any resolved surface, raise `ValueError("not one term could be grounded in the index")`. Do not turn an empty result relation into a compilation error.

- [ ] **Step 7: Run compile and QL regression tests**

Run:

```bash
conda run -n codesearch pytest tests/unit/ql/compile tests/unit/ql/test_unit_eval.py tests/unit/ql/test_store.py -v
conda run -n codesearch ruff check codesense/ql/compile codesense/ql/term_resolution.py tests/unit/ql/compile
```

Expected: PASS.

- [ ] **Step 8: Commit grounding-aware compilation**

```bash
git add codesense/ql/compile/build.py codesense/ql/compile/cost.py codesense/ql/compile/partition.py codesense/ql/compile/validate.py tests/unit/ql/compile
git commit -m "feat: compile grounded semantic terms consistently"
```

---

### Task 6: Generalize hard result targets to multiple element kinds

**Files:**
- Modify: `codesense/ql/compile/spec.py`
- Modify: `codesense/ql/compile/plan.py`
- Modify: `codesense/ql/compile/planner.py`
- Modify: `codesense/ql/compile/emit.py`
- Modify: `codesense/search.py`
- Modify: `tests/unit/ql/compile/test_planner.py`
- Modify: `tests/unit/ql/compile/test_emit.py`
- Modify: `tests/unit/test_search.py`

**Interfaces:**
- Consumes: Task 1's target values and existing `ProjectTarget`/`project()` behavior.
- Produces: `normalise_target(raw: object) -> tuple[str, ...]` for all approved aliases and union target enforcement.

- [ ] **Step 1: Write failing normalization tests**

Replace the file-only contract test with cases:

```python
assert normalise_target("class") == ("class",)
assert normalise_target("type") == (
    "class", "interface", "enum", "record", "annotation_type"
)
assert normalise_target(["function", "file"]) == (
    "method", "constructor", "file"
)
assert normalise_target(["class", "class", "unknown"]) == ("class",)
```

- [ ] **Step 2: Write failing execution and emission tests**

Build a Frag with class, method, field, and file nodes. Assert class/interface targets directly filter; file/method targets retain matching methods plus projected files; target projection precedes public limit; `to_script()` matches `Plan.run()`.

- [ ] **Step 3: Run target tests and confirm failure**

Run:

```bash
conda run -n codesearch pytest tests/unit/ql/compile/test_planner.py tests/unit/ql/compile/test_emit.py tests/unit/test_search.py -v
```

Expected: FAIL because `normalise_target` still accepts only file.

- [ ] **Step 4: Implement target alias normalization**

Add a dedicated `TARGET_ALIASES` rather than reusing soft `KIND_ALIASES`. `class` remains strict, `type` expands across declaration types, and `function` expands to method/constructor. Ignore unknown external target strings for backward compatibility and preserve concrete-kind insertion order.

- [ ] **Step 5: Optimize target enforcement by target family**

When no target is requested, preserve the current non-file default. When targets exclude file, use `Frag.induced()` directly. When targets include file, union direct matching nodes with `project(frag, edge="in_file", kind="file", include_self=True)`. Project before final `Narrow` and preserve boosted IDs across projection.

- [ ] **Step 6: Run target tests and lint**

Run:

```bash
conda run -n codesearch pytest tests/unit/ql/compile/test_planner.py tests/unit/ql/compile/test_emit.py tests/unit/test_search.py -v
conda run -n codesearch ruff check codesense/ql/compile/spec.py codesense/ql/compile/plan.py codesense/ql/compile/planner.py codesense/ql/compile/emit.py codesense/search.py
```

Expected: PASS.

- [ ] **Step 7: Commit generalized targets**

```bash
git add codesense/ql/compile/spec.py codesense/ql/compile/plan.py codesense/ql/compile/planner.py codesense/ql/compile/emit.py codesense/search.py tests/unit/ql/compile/test_planner.py tests/unit/ql/compile/test_emit.py tests/unit/test_search.py
git commit -m "feat: support multiple code result targets"
```

---

### Task 7: Compile result-bound relations into graph projection

**Files:**
- Modify: `codesense/ql/compile/spec.py`
- Modify: `codesense/ql/compile/build.py`
- Modify: `codesense/ql/compile/plan.py`
- Modify: `codesense/ql/compile/planner.py`
- Modify: `codesense/ql/compile/emit.py`
- Modify: `codesense/ql/compile/__init__.py`
- Modify: `tests/unit/ql/compile/test_relations.py`
- Modify: `tests/unit/ql/compile/test_planner.py`
- Modify: `tests/unit/ql/compile/test_emit.py`

**Interfaces:**
- Consumes: Task 1's `RelationEndpoint` shape and existing evidence-preserving `project()`.
- Produces: `ResultRelation(unit: str, result_side: Literal["source", "target"], edge: tuple[str, ...])`, `QuerySpec.result_relation: ResultRelation | None`, and `ResolveResultRelation` plan step.

- [ ] **Step 1: Write failing QuerySpec result-relation tests**

Assert `build_spec` converts `$result --references--> page_request` to:

```python
ResultRelation(
    unit="page_request",
    result_side="source",
    edge=("references",),
)
```

Keep unit-unit relations in `QuerySpec.graph`. Assert two result-bound relations and result-result are rejected at the Pydantic boundary, not guessed in QL.

- [ ] **Step 2: Write failing plan execution tests**

Build PageRequest, Controller.run, and file nodes with `Controller.run --references--> PageRequest` and `Controller.run --in_file--> Controller.java`. Assert reverse result resolution produces Controller.run, then `target=file` produces only Controller.java. Assert PageRequest.java is absent and no edge produces an empty Frag.

- [ ] **Step 3: Write failing emitted-script equivalence tests**

Construct forward and backward `ResolveResultRelation` plans. Assert emitted source calls `project()` with the correct direction and edge tuple, and running emitted source returns the same nodes as `Plan.run()`.

- [ ] **Step 4: Run relation tests and confirm failure**

Run:

```bash
conda run -n codesearch pytest tests/unit/ql/compile/test_relations.py tests/unit/ql/compile/test_planner.py tests/unit/ql/compile/test_emit.py -v
```

Expected: FAIL because result endpoints and `ResolveResultRelation` do not exist.

- [ ] **Step 5: Add the result-relation IR and build adapter**

Define frozen `ResultRelation` in `spec.py`. Extend `QuerySpec` with `result_relation: ResultRelation | None`. `build_spec` receives the typed relation proposal, converts unit-unit relations through current statistical validation, maps its anchor proposal name to the final validated `QueryUnit.name`, and stores the single result-bound relation without attempting unknown-side lift. If the referenced proposal unit has no grounded terms, raise `ValueError` so planned search degrades instead of changing the requested relation semantics.

- [ ] **Step 6: Implement ResolveResultRelation**

The step reads `anchor = state.units[unit]`, uses `project(anchor, ctx, edge=edge, direction="backward")` for `result_side="source"` and `direction="forward"` for `result_side="target"`, and replaces `state.current`. Preserve ranking intent by projecting `anchor.induced(state.boosted)` through the same edge and direction into the new `state.boosted`; do not intersect anchor IDs with projected result IDs. Its estimate uses one-hop `estimate_hop(anchor_rows, hops=(1, 1))`.

- [ ] **Step 7: Insert the step at the correct planner boundary**

Place it after unit evaluation and unit-unit boosts, before `Cohere`, intent, target projection, and public limit. Add reasoning text that names the returned relation side. A valid empty projection remains an empty result and stops later work normally.

- [ ] **Step 8: Emit equivalent QL source and export public types**

Render:

```python
frag = project(
    page_request_matches,
    ctx,
    edge=("references",),
    direction="backward",
)
boosted_frag = project(
    page_request_matches.induced(boosted),
    ctx,
    edge=("references",),
    direction="backward",
)
boosted = set(boosted_frag.nodes)
```

Export `ResultRelation` and `ResolveResultRelation` from `codesense.ql.compile`.

- [ ] **Step 9: Run relation and plan regression tests**

Run:

```bash
conda run -n codesearch pytest tests/unit/ql/compile tests/unit/ql/test_project_operator.py tests/unit/ql/test_script.py -v
conda run -n codesearch ruff check codesense/ql/compile tests/unit/ql/compile
```

Expected: PASS.

- [ ] **Step 10: Commit result-bound relation execution**

```bash
git add codesense/ql/compile tests/unit/ql/compile
git commit -m "feat: resolve relational result endpoints"
```

---

### Task 8: Adapt planned search to the typed semantic IR

**Files:**
- Modify: `codesense/search.py`
- Modify: `tests/unit/test_search.py`
- Modify: `tests/integration/test_file_target_queries.py`

**Interfaces:**
- Consumes: Task 1's typed model, Task 4's `TermResolver`, Task 5's `build_spec(terms: Sequence[Term])`, Task 6's general targets, and Task 7's result relation.
- Produces: complete typed `QueryUnderstandingResult -> QuerySpec` adaptation inside `_planned` without loose dict access, sharing one resolver across compilation and planning.

- [ ] **Step 1: Add a typed fixture builder for search tests**

Replace dict-returning `planned_understanding()` and missing-target response fixtures with a helper that returns `QueryUnderstandingResult.model_validate(...)`. Empty `targets` now explicitly suppress query-text target inference in planned mode.

- [ ] **Step 2: Write failing typed-adapter tests**

Assert `_planned` flattens SemanticTerm objects into QL Terms with source/weight/reason, converts unit names to groups, sends unit-unit and result-bound relations to their respective build inputs, and uses `understood.targets` without checking key presence.

- [ ] **Step 3: Write the motivating integration regression**

In the existing Java fixture, inject a typed understanding for:

```text
Find Java files containing references to the PageRequest class.
```

Assert the planned route returns Controller.java, excludes PageRequest.java and declaration Hits, reports target file, and emits a script containing backward `references` projection followed by `in_file` target projection.

- [ ] **Step 4: Run search tests and confirm failure**

Run:

```bash
conda run -n codesearch pytest tests/unit/test_search.py tests/integration/test_file_target_queries.py -v
```

Expected: FAIL because `_planned` expects a dict and cannot bind result endpoints.

- [ ] **Step 5: Implement the typed adapter**

Use attributes only:

```python
terms = tuple(
    Term(
        value=item.value.casefold(),
        source=item.source.value,
        weight=item.weight,
        reason=item.reason,
    )
    for unit in understood.units
    for item in unit.terms
)
groups = {
    unit.name: [item.value.casefold() for item in unit.terms]
    for unit in understood.units
}
route_target = normalise_target(understood.targets)
```

Keep caller-explicit target precedence. Split relations into unit-unit proposals and the optional result-bound proposal before calling `build_spec`. Construct `resolver = TermResolver(ctx)` once, pass it to both `build_spec(..., resolver=resolver)` and `plan(..., resolver=resolver)`, and let the resolver die when `_planned` returns. Use `understood.criterion` only when judge is enabled.

- [ ] **Step 6: Preserve target across planned failure and lexical degradation**

Set `route_target` immediately after typed understanding and before build/plan. Raise `_PlannedFailure(route_target, cause, judge_ran)` for downstream errors. The lexical fallback preserves target but adds the existing note that relation semantics were approximated.

- [ ] **Step 7: Run search and integration tests**

Run:

```bash
conda run -n codesearch pytest tests/unit/test_search.py tests/integration/test_file_target_queries.py tests/unit/llm -v
conda run -n codesearch ruff check codesense/search.py tests/unit/test_search.py tests/integration/test_file_target_queries.py
```

Expected: PASS.

- [ ] **Step 8: Commit planned search integration**

```bash
git add codesense/search.py tests/unit/test_search.py tests/integration/test_file_target_queries.py
git commit -m "feat: compile typed query understanding in planned search"
```

---

### Task 9: Document, verify, and close the feature

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `docs/design/04-query-unit.md`
- Modify: `docs/design/07-mapping-to-current.md`
- Modify: `docs/design/09-grounding.md`

**Interfaces:**
- Consumes: all completed implementation tasks.
- Produces: user-facing design alignment, changelog entry, and full verification evidence.

- [ ] **Step 1: Update current design documentation**

Document that the first LLM hop is now a strict Pydantic IR, representative vocabulary is advisory, semantic terms may be out-of-vocabulary, term resolution is shared by statistics and execution, targets accept unions, and `$result` binds a relation's returned endpoint. Remove text claiming model terms must always be chosen from the prompt vocabulary.

- [ ] **Step 2: Add one completed changelog entry**

Under the current unreleased section, record:

- strict JSON Schema/Pydantic query understanding;
- literal/synonym/derived semantic terms with project grounding;
- representative df-bounded prompt vocabulary;
- general multi-kind result targets;
- result-bound relation projection for queries such as files referencing a class.

- [ ] **Step 3: Run all focused tests together**

Run:

```bash
conda run -n codesearch pytest tests/unit/llm tests/unit/ql tests/unit/test_search.py tests/unit/test_index.py tests/unit/test_cli.py tests/integration/test_file_target_queries.py -v
```

Expected: PASS.

- [ ] **Step 4: Run repository gates**

Run:

```bash
conda run -n codesearch ruff check .
conda run -n codesearch ruff format --check .
conda run -n codesearch pytest
git diff --check
```

Expected: all commands pass. If a pre-existing unrelated dirty file blocks a repository-wide gate, record the exact file and also run the equivalent check restricted to every file changed by this plan.

- [ ] **Step 5: Run one real planned query when credentials are available**

Run the PageRequest query against a built Java fixture or configured project with `trace=True`. Confirm logs show understand, grounded spec, result-relation projection, target enforcement, and search completion. Repeat with `trace=False` and assert no search-stage stdout. If `CODESENSE_API_KEY` is unavailable, report this external integration as not run rather than substituting a fake success.

- [ ] **Step 6: Review the final diff for scope and generated artifacts**

Run:

```bash
git status --short
git diff --stat HEAD~9..HEAD
git diff --check HEAD~9..HEAD
```

Confirm no key, `output/`, evaluation viewer artifact, unrelated dirty file, or model weight was staged by these tasks.

- [ ] **Step 7: Commit documentation and changelog**

```bash
git add CHANGELOG.md docs/design/04-query-unit.md docs/design/07-mapping-to-current.md docs/design/09-grounding.md
git commit -m "docs: describe typed query understanding"
```
