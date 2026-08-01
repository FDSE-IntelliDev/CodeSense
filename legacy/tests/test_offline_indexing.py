"""离线索引的端到端冒烟测试。

标了 slow：需要 tree-sitter / javalang 这些重依赖，装了才跑得起来。

    pytest -m slow tests/integration        # 只跑这些
    pytest -m "not slow"                    # 日常跑，跳过这些

这不是完整的正确性测试，只保证「拿一个最小项目走一遍离线链路不会炸、
产物结构对得上」。真正的检索质量应该由 evaluation/ 那套指标来衡量。
"""

from __future__ import annotations

import json

import pytest

MINI_PROJECT = "tests/fixtures/mini_project"


@pytest.mark.slow
def test_解析最小项目能产出符号表(repo_root, tmp_path):
    from codesense.indexing.code_parser import run as run_code_parser

    run_code_parser(str(repo_root / MINI_PROJECT), str(tmp_path))

    symbols_path = tmp_path / "symbols_index.json"
    assert symbols_path.is_file(), "离线解析没有产出 symbols_index.json"

    symbols = json.loads(symbols_path.read_text(encoding="utf-8"))
    names = {s["name"] for s in symbols} if isinstance(symbols, list) else set()
    assert "LoginController" in names
    assert "AuthService" in names


@pytest.mark.slow
def test_符号_id_不是绝对路径(repo_root, tmp_path):
    """符号标识里带绝对路径的话，标注和产物换台机器就对不上了。"""
    from codesense.indexing.code_parser import run as run_code_parser

    run_code_parser(str(repo_root / MINI_PROJECT), str(tmp_path))
    symbols = json.loads((tmp_path / "symbols_index.json").read_text(encoding="utf-8"))

    for sym in symbols:
        assert not str(sym.get("name", "")).startswith("/")


@pytest.mark.slow
def test_ngram_与倒排索引能在符号表之上建起来(repo_root, tmp_path):
    from codesense.indexing.code_parser import run as run_code_parser
    from codesense.indexing.invert_index import InvertedIndexBuilder
    from codesense.indexing.ngram_split import SymbolNgramer

    run_code_parser(str(repo_root / MINI_PROJECT), str(tmp_path))

    ngramed = tmp_path / "ngramed_symbol.json"
    SymbolNgramer(
        symbols_index_path=str(tmp_path / "symbols_index.json"),
        output_path=str(ngramed),
    ).build_ngramed_symbol()
    assert ngramed.is_file()

    invert = tmp_path / "invert_index.json"
    InvertedIndexBuilder(
        symbols_index_path=str(ngramed),
        output_path=str(invert),
    ).build_invert_index()
    assert invert.is_file()
