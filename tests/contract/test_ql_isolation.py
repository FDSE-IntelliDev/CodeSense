"""契约：`codesense.ql` 是从设计文档从头实现的，不得被旧实现污染。

用户的要求是「一定要注意不被原本的实现干扰」。靠自觉守不住，
所以在这里机械强制：本包不允许 import ``codesense`` 下的任何其它子包。

需要旧实现的某个能力时，正确做法是**照着设计文档重新实现**，
或把它明确提升为共享基础设施（那就要先从这里的白名单放行）。
重写前的完整实现见 git tag ``pre-ql-rewrite``。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

QL_ROOT = Path(__file__).resolve().parents[2] / "codesense" / "ql"

#: 允许从 codesense 命名空间导入的模块前缀。
#: 刻意只放行包自身——config 都不放行，QL 的配置走构造函数注入。
ALLOWED = ("codesense.ql",)


def _imported_modules(source: str) -> set[str]:
    tree = ast.parse(source)
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module)
    return found


def test_ql_不导入旧实现的任何子包() -> None:
    offenders: list[str] = []
    for path in sorted(QL_ROOT.rglob("*.py")):
        for module in _imported_modules(path.read_text(encoding="utf-8")):
            if module.startswith("codesense") and not module.startswith(ALLOWED):
                rel = path.relative_to(QL_ROOT.parents[1])
                offenders.append(f"{rel}: import {module}")
    assert not offenders, (
        "codesense/ql/ 被旧实现污染了。照设计文档重新实现，"
        "或先把该模块加进本测试的 ALLOWED 白名单并说明理由：\n  " + "\n  ".join(offenders)
    )


def test_ql_目录存在且非空() -> None:
    """防止上面那个测试因为目录被删/改名而空转通过。"""
    assert QL_ROOT.is_dir()
    assert list(QL_ROOT.rglob("*.py"))


def test_import_没有副作用() -> None:
    """契约：import 一个模块不该读盘、读配置、连网络。

    破坏它的写法看起来完全无害——模块级常量里塞一个 `load_config()`、
    默认参数写成 `def f(m=BASE_MODEL)`——但后果是没有配置文件就连
    import 都失败，于是不需要配置的纯逻辑也跟着没法测。

    归档实现里这条契约救过场（见 legacy/tests/test_import_purity.py），
    新包从第一天就守住。
    """
    import importlib

    for path in sorted(QL_ROOT.rglob("*.py")):
        if path.name == "__init__.py":
            continue
        module = "codesense.ql." + str(path.relative_to(QL_ROOT).with_suffix("")).replace("/", ".")
        importlib.import_module(module)  # 抛异常即失败


def test_ql_不依赖任何第三方包() -> None:
    """现役 QL 层应当只用标准库。

    第三方依赖会把「能不能跑测试」和「装没装齐环境」绑在一起，
    而这一层是纯逻辑，不需要。真要引入（比如 sqlite 之外的存储），
    先在这里放行并说明。
    """
    stdlib = set(sys.stdlib_module_names)
    outside: list[str] = []
    for path in sorted(QL_ROOT.rglob("*.py")):
        for module in _imported_modules(path.read_text(encoding="utf-8")):
            root = module.split(".")[0]
            if root not in stdlib and not module.startswith("codesense"):
                outside.append(f"{path.name}: {module}")
    assert not outside, "QL 层引入了第三方依赖:\n  " + "\n  ".join(outside)
