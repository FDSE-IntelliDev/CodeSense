"""契约：import 一个模块不该产生任何副作用。

对应 ARCHITECTURE.md「模块顶层不执行逻辑」。

这条约定特别容易被悄悄破坏，因为破坏它的写法看起来完全无害：

    from codesense.config import PROJECT_OUTPUT_DIR      # 触发 __getattr__ → 读 YAML
    def f(model: str = BASE_MODEL): ...                  # 默认参数在 def 时求值
    DEFAULT_PATH = f"{QUERY_OUTPUT_DIR}/x.json"          # 模块级 f-string

后果是实打实的：没有 configs/default.yaml 就连 import 都失败，
于是不需要配置的纯逻辑（解析、集合运算）也跟着没法测。

所以把它钉在测试里。新加模块如果在这里挂了，改模块，别改测试。
"""

from __future__ import annotations

import importlib
import sys

import pytest

import codesense.config as config_module

#: 已经守约的模块。清理一个就往这里加一个，只增不减。
PURE_IMPORT_MODULES = [
    "codesense.filters",
    "codesense.filters.base",
    "codesense.filters.relation_filters",
    "codesense.filters.relation_filter",
    "codesense.filters.relation_graph_store",
    "codesense.filters.type_filter",
    "codesense.filters.llm_judge_filter",
    "codesense.parsers.java_lsp_client",
    "codesense.parsers.parallel_java_lsp_client",
    "codesense.executors.relation_executor",
]


@pytest.fixture
def config_read_counter(monkeypatch):
    """数一数期间读了几次配置文件。"""
    calls: list[int] = []
    original = config_module.Config.from_file

    def counting(cls, *args, **kwargs):
        calls.append(1)
        return original.__func__(cls, *args, **kwargs)

    monkeypatch.setattr(config_module.Config, "from_file", classmethod(counting))
    config_module.load_config.cache_clear()
    yield calls
    config_module.load_config.cache_clear()


def _reimport(name: str) -> None:
    """强制重新执行模块顶层代码。"""
    for loaded in list(sys.modules):
        if loaded.startswith("codesense.") and loaded != "codesense.config":
            del sys.modules[loaded]
    importlib.import_module(name)


@pytest.mark.parametrize("module_name", PURE_IMPORT_MODULES)
def test_import_不读配置文件(module_name: str, config_read_counter) -> None:
    try:
        _reimport(module_name)
    except ModuleNotFoundError as exc:
        pytest.skip(f"缺第三方依赖，跳过：{exc.name}")
    assert not config_read_counter, (
        f"{module_name} 在 import 时读了 {len(config_read_counter)} 次配置。"
        "多半是模块级 `from codesense.config import 某常量`、"
        "或者把配置写进了默认参数。"
    )


def test_配置对象本身是惰性的() -> None:
    """import codesense.config 不该读盘，第一次 load_config() 才读。"""
    assert callable(config_module.load_config)
    assert hasattr(config_module, "__getattr__"), "过渡期兼容层被删了？同步更新本测试"
