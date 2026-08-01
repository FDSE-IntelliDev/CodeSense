"""Run a model-generated query script.

The compiled artifact is code, so running it means executing model output.
This module is the gate: an AST whitelist first, then execution in a
restricted namespace.

**Control flow is permitted.** QL is built on Python precisely for that
expressiveness -- intermediate variables, branching and iterative widening
are what let operators compose into a graph rather than a straight line.
Banning `for` and `while` buys too little determinism to be worth it; the
real hazard is non-termination, and an execution budget handles that.

**This is not a sandbox.** It stops a model writing something wrong --
imports, attribute escapes, file and network access, runaway loops -- not a
deliberate attack. Defending against that needs process isolation.
"""

from __future__ import annotations

import ast
import itertools
import sys
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

__all__ = ["DEFAULT_POLICY", "SAFE_BUILTINS", "ScriptError", "ScriptPolicy", "check", "run_script"]

#: Builtins available to the script. All pure; no IO, no reflection.
#:
#: **The whitelist and what is actually provided must share one source.**
#: Learned the hard way: `set` was whitelisted but never put in the
#: execution namespace, so entirely correct scripts died on NameError.
BUILTIN_NAMES = (
    "set",
    "sorted",
    "len",
    "min",
    "max",
    "sum",
    "abs",
    "round",
    "tuple",
    "list",
    "dict",
    "frozenset",
    "range",
    "enumerate",
    "zip",
    "reversed",
    "any",
    "all",
    "str",
    "int",
    "float",
    "bool",
)

#: Operators and constructors the script may call.
OPERATOR_NAMES = (
    "eval_unit",
    "hop",
    "reach",
    "degree",
    "only",
    "top",
    "intent",
    "score_of",
    "QueryUnit",
    "Term",
    "LexicalSatisfier",
    "AnnotationSatisfier",
    "ModifierSatisfier",
)

ALLOWED_CALLS = frozenset(OPERATOR_NAMES) | frozenset(BUILTIN_NAMES)

#: Attributes the script may reach. A fragment's structure is public, but
#: only these -- allowing arbitrary attributes would open escapes such as
#: `ctx.judge._config.api_key`.
ALLOWED_ATTRS = frozenset(
    {
        "nodes",
        "edges",
        "evidence",
        "witnesses",
        "induced",
        "roots",
        "leaves",
        "only_nodes",
        "evidence_for",
        "items",
        "keys",
        "values",
        "name",
        "kind",
        "file",
        "symbol_id",
        "container",
        "signature",
        "doc",
        "modifiers",
        "span",
    }
)

#: Node types that are rejected outright.
#:
#: **Control flow is allowed** -- `for`, `while`, `if` and function
#: definitions all pass. QL is built on Python for that expressiveness:
#: intermediate variables, branching, and iterative widening (chapter 06's
#: trial-and-widen loop can live inside the script) let operators compose
#: into an arbitrary computation graph rather than a straight line.
#:
#: Banning loops buys too little determinism. The real hazard is
#: non-termination, and that belongs to the execution budget (`max_steps`),
#: not to a syntax ban.
#:
#: What remains has no legitimate use in a script and would breach isolation:
FORBIDDEN_NODES = (
    ast.Import,
    ast.ImportFrom,
    ast.ClassDef,
    ast.Global,
    ast.Nonlocal,
    ast.With,
    ast.AsyncWith,
    ast.AsyncFunctionDef,
)

#: Builtins placed in the execution namespace. Shares its source with
#: `BUILTIN_NAMES` so the two cannot drift apart.
SAFE_BUILTINS = {name: getattr(__import__("builtins"), name) for name in BUILTIN_NAMES}

#: The variable a script must produce.
RESULT_NAME = "answer"

#: The default policy, as a module-level singleton rather than constructed in
#: an argument default -- the latter builds a new object on every call and
#: makes the default itself mutable.
DEFAULT_POLICY: ScriptPolicy


#: Virtual filename for generated scripts. Tracing uses it to tell the
#: script's own frames from everything else.
_FILENAME = "<compiled-query>"


class ScriptError(Exception):
    """A script failed the checks, or failed while running."""


class _BudgetExceeded(Exception):
    """A script exceeded its step budget, usually a loop with no exit."""


@contextmanager
def _step_budget(limit: int) -> Iterator[None]:
    """Count executed lines in the script's own frames.

    Only frames whose filename matches are traced. Operator internals are
    ordinary Python, and tracing them line by line would make a single
    `eval_unit` orders of magnitude slower -- and they are not the hazard.
    """
    counter = itertools.count()

    def trace_lines(frame: Any, event: str, arg: Any) -> Any:
        if next(counter) > limit:
            raise _BudgetExceeded(f"script ran past {limit:,} steps; a loop may not terminate")
        return trace_lines

    def trace_calls(frame: Any, event: str, arg: Any) -> Any:
        return trace_lines if frame.f_code.co_filename == _FILENAME else None

    previous = sys.gettrace()
    sys.settrace(trace_calls)
    try:
        yield
    finally:
        sys.settrace(previous)


@dataclass(frozen=True, slots=True)
class ScriptPolicy:
    """What is permitted. The defaults describe a well-formed compiled script."""

    calls: frozenset[str] = field(default=ALLOWED_CALLS)
    attributes: frozenset[str] = field(default=ALLOWED_ATTRS)
    max_lines: int = 200

    #: How many lines of the script itself may execute. This is the safety
    #: valve that makes allowing loops reasonable -- it stops
    #: non-termination, not loops. Only the script's own frames count;
    #: otherwise one `eval_unit` would exhaust the budget.
    max_steps: int = 200_000


DEFAULT_POLICY = ScriptPolicy()


def check(
    source: str,
    policy: ScriptPolicy | None = None,
    provided: Iterable[str] = (),
) -> ast.Module:
    """Static checks. Raises on failure; never runs something "as best it can".

    ``provided`` names come from the caller's namespace and are all callable
    -- what is in the namespace is the caller's decision, and the whitelist
    governs whether the script can reach anything beyond it.
    """
    policy = policy or DEFAULT_POLICY
    if source.count("\n") > policy.max_lines:
        raise ScriptError(f"script exceeds {policy.max_lines} lines; not a compiled query")
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise ScriptError(f"syntax error: {exc}") from exc

    # Names the script defines are callable. The whitelist governs which
    # external capabilities are reachable, not the script's own internals --
    # allowing function definitions but not calls would allow nothing.
    allowed = policy.calls | _bound_names(tree) | frozenset(provided)

    for node in ast.walk(tree):
        if isinstance(node, FORBIDDEN_NODES):
            raise ScriptError(f"{type(node).__name__} is not allowed")
        if isinstance(node, ast.Attribute) and node.attr not in policy.attributes:
            raise ScriptError(f"attribute .{node.attr} is not allowed")
        if isinstance(node, ast.Name) and node.id.startswith("__"):
            raise ScriptError(f"{node.id} is not allowed")
        if isinstance(node, ast.Call):
            target = node.func
            if isinstance(target, ast.Name) and target.id not in allowed:
                raise ScriptError(f"calling {target.id}() is not allowed")
            if isinstance(target, ast.Attribute) and target.attr not in policy.attributes:
                raise ScriptError(f"calling .{target.attr}() is not allowed")
    return tree


def _bound_names(tree: ast.Module) -> frozenset[str]:
    """Names the script binds: functions, assignments, loop and comprehension
    variables."""
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            found.add(node.name)
            found.update(arg.arg for arg in node.args.args)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Param)):
            found.add(node.id)
        elif isinstance(node, ast.Lambda):
            found.update(arg.arg for arg in node.args.args)
    return frozenset(found)


def run_script(
    source: str, namespace: Mapping[str, Any], *, policy: ScriptPolicy | None = None
) -> Any:
    """Check, execute, and return the script's ``answer``.

    The caller supplies the whole ``namespace`` (`ctx` and the operators);
    a script cannot import, so what it can reach is decided entirely here.
    """
    active = policy or DEFAULT_POLICY
    tree = check(source, active, provided=namespace.keys())
    scope: dict[str, Any] = {
        "__builtins__": dict(SAFE_BUILTINS),
        **SAFE_BUILTINS,
        **dict(namespace),
    }
    with _step_budget(active.max_steps):
        try:
            exec(compile(tree, _FILENAME, "exec"), scope)  # noqa: S102
        except _BudgetExceeded as exc:
            raise ScriptError(str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 -- turn crashes into a handled error
            raise ScriptError(f"execution failed: {type(exc).__name__}: {exc}") from exc
    if RESULT_NAME not in scope:
        raise ScriptError(f"script did not produce `{RESULT_NAME}`")
    return scope[RESULT_NAME]
