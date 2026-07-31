"""Golden：三个 RelationFilter 在真实候选集上的输出不能变。

需要 output/ 下的离线产物（codegraph.sqlite、filtered_by_type.json），
那些不进版本库，所以缺了就跳过——不是失败。

    python -m codesense --init        # 先建索引
    pytest -m slow tests/integration/test_golden_relation_filter.py

只比对 symbol_id 序列：那就是过滤器全部的可观察行为，
候选记录里其它字段是上游带下来的，不该由本层负责。
"""

from __future__ import annotations

import json

import pytest

from tests.golden_cases import GOLDEN_DIR, PROJECT_OUTPUT, QUERY_OUTPUT, relation_filter_cases

GOLDEN = GOLDEN_DIR / "relation_filter.json"
CODEGRAPH = PROJECT_OUTPUT / "codegraph.sqlite"
CANDIDATES = QUERY_OUTPUT / "filtered_by_type.json"

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def expected() -> dict:
    if not GOLDEN.is_file():
        pytest.skip(f"缺期望值文件 {GOLDEN.name}")
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def actual() -> dict:
    if not CODEGRAPH.is_file() or not CANDIDATES.is_file():
        pytest.skip("缺离线产物（codegraph.sqlite / filtered_by_type.json），先跑 --init")
    from codesense.filters.relation_graph_store import RelationGraphStore

    store = RelationGraphStore.open_if_ready(str(CODEGRAPH))
    if store is None:
        pytest.skip("codegraph.sqlite 里没有 code_edges，图库还没建好")
    try:
        yield relation_filter_cases(store)
    finally:
        store.close()


def test_所有用例与_golden_一致(expected: dict, actual: dict) -> None:
    assert set(expected) == set(actual), "用例集合变了"
    diffs = [k for k in expected if expected[k] != actual[k]]
    assert not diffs, (
        f"{len(diffs)} 个用例的结果变了：{diffs[:5]}。"
        "有意变更的话用 python -m scripts.record_golden --only relation_filter 重录。"
    )


def test_历史产物仍然可复现(actual: dict) -> None:
    """真实那次查询用的是 role=entry_point，产出 filtered_by_relation.json。

    这条比 golden 更强：它锚定的是**当初真实跑出来的结果**，
    而不只是「和上次一样」。
    """
    historical = QUERY_OUTPUT / "filtered_by_relation.json"
    if not historical.is_file():
        pytest.skip("没有历史产物可对照")
    want = [r.get("symbol_id") for r in json.loads(historical.read_text(encoding="utf-8"))]
    assert actual["roles/entry_point/preserve=True"] == want
