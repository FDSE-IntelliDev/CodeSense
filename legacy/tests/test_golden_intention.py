"""Golden：cluster 与 embedding 两个阶段的输出不能变。

最重的一组：要 SentenceTransformer 模型、embedding 训练产物，还要
output/ 下的候选集。缺任何一样都跳过。

    pytest -m slow tests/integration/test_golden_intention.py

期望值只存各桶的 symbol_id 序列和整数型 stats。中间数据（tiers 之类）
体积几百 KB 且随实现细节变化，拿它当回归基准只会天天误报。
"""

from __future__ import annotations

import json

import pytest

from tests.golden_cases import GOLDEN_DIR, QUERY_OUTPUT, intention_cases

GOLDEN = GOLDEN_DIR / "intention_stages.json"
NEEDED = ("intention_semql.json", "filtered_by_type.json", "filtered_by_cluster.json")

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def expected() -> dict:
    if not GOLDEN.is_file():
        pytest.skip(f"缺期望值文件 {GOLDEN.name}")
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def actual() -> dict:
    missing = [n for n in NEEDED if not (QUERY_OUTPUT / n).is_file()]
    if missing:
        pytest.skip(f"缺产物：{missing}")
    try:
        return intention_cases()
    except (ImportError, ModuleNotFoundError) as e:
        pytest.skip(f"缺依赖 {e.name}")
    except OSError as e:
        pytest.skip(f"缺模型或数据：{str(e)[:80]}")


@pytest.mark.parametrize(
    "case",
    [
        "cluster/real_policy",
        "cluster/empty",
        "embedding/real_policy",
        "embedding/empty",
        "embedding/on_type_candidates",
    ],
)
def test_阶段输出与_golden_一致(case: str, expected: dict, actual: dict) -> None:
    want, got = expected.get(case), actual.get(case)
    assert want == got, (
        f"{case} 的输出变了。各桶大小 "
        f"{ {k: len(v) for k, v in (want or {}).items() if isinstance(v, list)} } -> "
        f"{ {k: len(v) for k, v in (got or {}).items() if isinstance(v, list)} }。"
        "有意变更的话用 python -m scripts.record_golden --only intention 重录。"
    )


def test_空输入不产生候选(actual: dict) -> None:
    """这条不依赖 golden——是个恒真的契约。"""
    for case in ("cluster/empty", "embedding/empty"):
        for key, value in actual[case].items():
            if isinstance(value, list):
                assert value == [], f"{case} 的 {key} 应该为空"
