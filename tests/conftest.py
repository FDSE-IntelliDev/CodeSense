"""pytest 全局夹具。

约定：``unit/`` 不碰 IO 和网络，``integration/`` 才允许落盘或调外部服务；
要花钱或要重型依赖的标 ``slow``，默认不跑。
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def repo_root() -> Path:
    return REPO_ROOT
