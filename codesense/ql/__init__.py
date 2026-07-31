"""CodeSense QL —— 把自然语言查询编译成一段代码查询脚本。

设计见 ``docs/design/``。这是一次**从设计文档从头实现**，
不复用 ``codesense`` 下其它子包的执行层代码；重写前的实现见 git tag
``pre-ql-rewrite`` 与分支 ``archive/legacy-implementation``。

隔离由 ``tests/contract/test_ql_isolation.py`` 强制：本包不得 import
``codesense`` 下的任何其它子包。
"""

from codesense.ql.fields import DEFAULT_FIELD_WEIGHTS, FieldWeights, IndexField
from codesense.ql.frag import (
    Edge,
    EdgeKey,
    Element,
    Evidence,
    Frag,
    Path,
    UnitHit,
    Verdict,
)
from codesense.ql.judge import Judge, JudgeItem, NullJudge
from codesense.ql.script import ScriptError, ScriptPolicy, run_script

__all__ = [
    "DEFAULT_FIELD_WEIGHTS",
    "Edge",
    "EdgeKey",
    "Element",
    "Evidence",
    "FieldWeights",
    "Frag",
    "IndexField",
    "Judge",
    "JudgeItem",
    "NullJudge",
    "ScriptError",
    "ScriptPolicy",
    "Path",
    "UnitHit",
    "Verdict",
    "run_script",
]
