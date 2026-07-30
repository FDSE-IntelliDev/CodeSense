"""pytest 全局夹具。

约定见 docs/testing.md：unit/ 不碰 IO 和网络，integration/ 才允许落盘。
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture
def config_yaml(tmp_path: Path):
    """造一份临时配置文件，返回 (写入函数, 默认内容)。

    单元测试不要读仓库里那份 configs/default.yaml——那个文件会被改，
    测试就会跟着飘。
    """

    def write(body: str) -> Path:
        path = tmp_path / "config.yaml"
        path.write_text(textwrap.dedent(body), encoding="utf-8")
        return path

    return write


VALID_CONFIG = """\
target:
  project_path: "/tmp/some-project"
  project_name: "some-project"
output:
  dir: "output"
  corpus: "enhanced_call_chain_corpus.json"
  abbr_result_dir: "output/abbr_results"
llm:
  base_url: "https://example.invalid/v1"
  model: "test-model"
tools:
  jdtls_path: "jdtls"
query:
  default_id: 7
"""
