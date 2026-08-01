"""Golden：分词与缩写生成的行为不能悄悄变。

这一组是**可移植**的——输入的 120 个符号名固化在
``tests/fixtures/golden/tokenizer_input.json`` 里，不依赖 output/ 下的产物。
装齐依赖（srctoolkit、wordfreq、spacy + en_core_web_sm、nltk 的 wordnet）
就能跑。

标 slow 是因为那几个依赖不轻，不该让刚 clone 的人跑 pytest 就见红：

    pytest -m slow tests/integration/test_golden_tokenizer.py

期望值有意存的是**全量输出**而不是抽样：分词和缩写对输入形态极其敏感，
少数几个样本测不出回归。
"""

from __future__ import annotations

import json

import pytest

from tests.golden_cases import GOLDEN_DIR, tokenizer_cases

GOLDEN = GOLDEN_DIR / "tokenizer.json"

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def expected() -> dict:
    if not GOLDEN.is_file():
        pytest.skip(f"缺期望值文件 {GOLDEN.name}，用 python -m scripts.record_golden 录一份")
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def actual() -> dict:
    try:
        return tokenizer_cases()
    except (ImportError, ModuleNotFoundError) as e:
        pytest.skip(f"缺依赖 {e.name}")
    except OSError as e:
        # spacy 的 en_core_web_sm 没装时抛的是 OSError
        pytest.skip(f"缺模型或数据：{str(e)[:80]}")


@pytest.mark.parametrize(
    "group", ["tokenizer/bpe", "tokenizer/unigram", "abbreviate", "normalize_entity"]
)
def test_输出与_golden_一致(group: str, expected: dict, actual: dict) -> None:
    want, got = expected.get(group, {}), actual.get(group, {})
    assert set(want) == set(got), "输入集合变了——是不是改了 tokenizer_input.json？"

    diffs = {k: (want[k], got[k]) for k in want if want[k] != got[k]}
    assert not diffs, (
        f"{group} 有 {len(diffs)} 个输入的输出变了，例如："
        + "; ".join(f"{k!r}: {v[0]!r} -> {v[1]!r}" for k, v in list(diffs.items())[:3])
        + "。如果这是有意的行为变更，用 python -m scripts.record_golden --only tokenizer 重录。"
    )


def test_覆盖面没有缩水(expected: dict) -> None:
    """防止有人为了让测试变绿而删样本。"""
    assert len(expected["tokenizer/bpe"]) >= 100
    assert len(expected["abbreviate"]) >= 10
