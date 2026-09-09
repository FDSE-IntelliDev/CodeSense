# Task 16 report — preserve relation boosts across file projection

Base: `d8748ed0109e9f69d8cc620a9c80ef6aced4ee54`

## Root cause

`Boost.apply` correctly stored typed-relation destination declaration IDs in
`State.boosted`. `ProjectTarget.apply` then projected only `State.current` to
file IDs, leaving the ranking set in the declaration-ID domain. The emitted
script had the same omission. Consequently, final `Narrow(limit=1)` could not
boost the related declaration's owner file.

## TDD evidence

The new state and Plan/script execution tests failed before the implementation:

- state expected projected boost `{11}` but retained declaration `{2}`;
- the end-to-end plan selected the lexically stronger unrelated file `{12}`
  instead of related file `{11}`.

After the minimal change, both execution paths project the boosted subset from
the pre-projection fragment through the same exact `in_file`, target-kind and
`include_self=True` contract used for public candidates. Empty boost state and
non-file target behavior are covered separately, and projected evidence scores
remain intact.

## Verification

- `pytest -q tests/unit/ql/compile tests/unit/test_search.py` — passed.
- `pytest -q -m slow tests/integration/test_file_target_queries.py tests/unit/lang/test_java_scanner.py tests/unit/indexing/test_reference_graph.py` — 27 passed; 7 upstream tree-sitter deprecation warnings.
- `ruff check .` — passed.
- `ruff format --check .` — 129 files formatted.
- `pytest -q` — passed.
- `git diff --check` — passed.
