"""Global pytest fixtures.

The convention: ``unit/`` touches neither IO nor the network; only
``integration/`` may write to disk or call external services. Anything that
costs money or needs a heavy dependency is marked ``slow`` and does not run by
default.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def repo_root() -> Path:
    return REPO_ROOT
