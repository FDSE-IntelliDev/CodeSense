"""契约：`codesense.ql` 是从设计文档从头实现的，不得被旧实现污染。

用户的要求是「一定要注意不被原本的实现干扰」。靠自觉守不住，
所以在这里机械强制：本包不允许 import ``codesense`` 下的任何其它子包。

需要旧实现的某个能力时，正确做法是**照着设计文档重新实现**，
或把它明确提升为共享基础设施（那就要先从这里的白名单放行）。
重写前的完整实现见 git tag ``pre-ql-rewrite``。
"""

from __future__ import annotations

import ast
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
