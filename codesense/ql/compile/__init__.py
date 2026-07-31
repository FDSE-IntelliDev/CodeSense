"""Compilation: natural language to a query spec to an execution plan.

    QuerySpec   the intermediate form; hand-writable, or built from LLM JSON
    plan()      **orders steps by estimated selectivity** -- the only place
                the compiler optimises anything
    Plan        ordered steps; runnable, printable, and comparable against
                what actually happened

Turning language into a spec needs an LLM, so that step lives in
`codesense.llm` rather than here (the QL layer is standard-library only).
"""

from codesense.ql.compile.build import ScoredTerm, build_spec, infer_fields, infer_kinds
from codesense.ql.compile.cost import Estimate, estimate_hop, estimate_intent, estimate_unit
from codesense.ql.compile.emit import to_script
from codesense.ql.compile.partition import Cluster, partition
from codesense.ql.compile.plan import (
    Boost,
    Cohere,
    EvalUnit,
    Intent,
    Narrow,
    Plan,
    State,
    Step,
    Trace,
)
from codesense.ql.compile.planner import plan
from codesense.ql.compile.spec import GraphConstraint, QuerySpec
from codesense.ql.compile.validate import (
    Relation,
    relation_lift,
    validate_groups,
    validate_relations,
)

__all__ = [
    "Boost",
    "Cluster",
    "Cohere",
    "Estimate",
    "EvalUnit",
    "GraphConstraint",
    "Intent",
    "Narrow",
    "Plan",
    "QuerySpec",
    "Relation",
    "ScoredTerm",
    "State",
    "Step",
    "Trace",
    "build_spec",
    "estimate_hop",
    "estimate_intent",
    "estimate_unit",
    "infer_fields",
    "infer_kinds",
    "partition",
    "plan",
    "to_script",
    "relation_lift",
    "validate_groups",
    "validate_relations",
]
