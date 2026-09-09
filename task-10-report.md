# Task 10 report: public search target state machine

## Scope and target-state table

Implemented the requested target priority without changing reference-graph construction:

| Priority | State | Result |
| --- | --- | --- |
| 1 | Explicit caller target, including `()` and unsupported values | Always wins; normalized empty stays empty. |
| 2 | Planned structured target, including structured `()` | Preserved on success and through planned-route failure. |
| 3 | Codegen returns only file elements | Infers `("file",)`. |
| 4 | No higher-priority decision | Conservative output-request recognizer; object-position `file/files` is not enough. |

`None` is now the internal “no route decision yet” state; `()` is a deliberate
empty target. The final common target enforcement remains in `search()` for all
routes and fallbacks.

## Root causes addressed

- `search()` previously inferred a broad lexical target before planned
  understanding and passed it into the route.
- `_query_target()` matched every `file/files/文件` occurrence, including an
  object such as “methods that write a file”. It now recognizes narrow forms
  such as `find/list/show/return ... files`, `which files`, and clear Chinese
  output phrasing.
- Planned understanding was stack-local. `_PlannedFailure` carries the
  normalized target and original cause through build/plan/execution failures.
- Planned judging is now controlled by public `judge`: the planned route uses
  `Narrow(cap) -> Intent -> ProjectTarget -> Narrow(public limit)` when enabled;
  public post-route judging is skipped for a successfully judged planned route.
  `judge=False` removes planned `Intent`.
- Public `limit` is passed to `build_spec()` and stored in `QuerySpec.limit`, so
  the public limit is applied after file projection and values above 100 are
  not replaced by the planner default.

## Inherited-test audit and RED/GREEN

The inherited `tests/unit/test_search.py` changes were audited before
production edits. The only test defect was unsorted imports; it was fixed.
The remaining ten failing scenarios were intentional RED tests: structured
empty target, conservative lexical inference, planned-target preservation on
`build_spec`/`plan` failure, explicit target precedence, judge call count and
ordering, and public limit behavior. The judge expectation of declaration IDs
`[1, 2]` is correct: the pre-fix extra file ID `3` demonstrated judging after
projection.

After implementation, the same file is GREEN.

## Verification

- `conda run -n codesearch pytest -q tests/unit/test_search.py` — 51 passed.
- `conda run -n codesearch pytest -q tests/unit/ql/compile tests/unit/test_search.py tests/unit/test_index.py` — 135 passed.
- `conda run -n codesearch pytest -q tests/unit/ql/compile tests/unit/ql/test_script.py tests/unit/llm/test_compiler.py tests/integration/test_file_target_queries.py -m slow` — passed (3 tests; one existing tree-sitter deprecation warning).
- `conda run -n codesearch pytest` — 613 passed, 33 deselected.
- `conda run -n codesearch ruff check .` — passed.
- `conda run -n codesearch ruff format --check .` — 128 files already formatted.

Base commit: `4a7d20d5d15898777b7c696f6e84f0c8d483f10f`.

## Concerns

- The lexical recognizer intentionally prefers false negatives to broad
  semantic inference; future output forms should add focused tests.
- Planned-route failures before structured understanding still use lexical
  fallback inference because no structured target exists.
