"""Query operators. Every one of them returns a ``Frag``."""

from codesense.ql.operators.hop import DEFAULT_MAX_DEGREE, DEFAULT_MAX_PATHS, hop, reach
from codesense.ql.operators.intent import DEFAULT_MAX_ITEMS, intent
from codesense.ql.operators.project import project
from codesense.ql.operators.select import degree, only, score_of, top
from codesense.ql.operators.unit import eval_unit

__all__ = [
    "DEFAULT_MAX_DEGREE",
    "DEFAULT_MAX_ITEMS",
    "DEFAULT_MAX_PATHS",
    "degree",
    "eval_unit",
    "hop",
    "intent",
    "only",
    "project",
    "reach",
    "score_of",
    "top",
]
