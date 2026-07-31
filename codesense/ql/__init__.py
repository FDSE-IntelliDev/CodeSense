"""CodeSense QL: compiling a natural-language query into a query script.

Design lives in ``docs/design/``. This is a **reimplementation from the
design documents** that reuses no execution-layer code from the other
``codesense`` subpackages; the pre-rewrite tree is at git tag
``pre-ql-rewrite`` and branch ``archive/legacy-implementation``.

Isolation is enforced by ``tests/contract/test_ql_isolation.py``: this
package must not import any other ``codesense`` subpackage.
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
