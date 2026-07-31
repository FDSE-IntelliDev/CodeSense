"""查询算子。所有算子都是 ``... -> Frag``。"""

from codesense.ql.operators.hop import DEFAULT_MAX_DEGREE, DEFAULT_MAX_PATHS, hop, reach
from codesense.ql.operators.unit import eval_unit

__all__ = ["DEFAULT_MAX_DEGREE", "DEFAULT_MAX_PATHS", "eval_unit", "hop", "reach"]
