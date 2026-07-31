"""Tests for the gate on executing model-generated scripts.

This layer stops the model writing something wrong, not a deliberate attack --
real attack resistance needs process isolation. But two things must hold:
**what should pass passes, and what should be blocked is blocked**.
"""

from __future__ import annotations

import pytest

from codesense.ql import ScriptError, ScriptPolicy, run_script
from codesense.ql.script import ALLOWED_CALLS, BUILTIN_NAMES, SAFE_BUILTINS


class TestControlFlow:
    """QL is built on Python precisely for its expressiveness. Control flow
    has to be allowed."""

    def test_conditionals(self) -> None:
        source = "x = 5\nif x > 3:\n    answer = 'big'\nelse:\n    answer = 'small'"
        assert run_script(source, {}) == "big"

    def test_loops(self) -> None:
        source = "total = 0\nfor n in range(5):\n    total = total + n\nanswer = total"
        assert run_script(source, {}) == 10

    def test_iterating_to_convergence_widening_when_too_few(self) -> None:
        """Chapter 06's try-then-widen loop can be written straight into the
        script."""
        source = (
            "answer = None\n"
            "for n in (2, 8, 40):\n"
            "    found = widen(n)\n"
            "    if len(found) >= 8:\n"
            "        answer = found\n"
            "        break\n"
        )
        assert len(run_script(source, {"widen": lambda n: list(range(n))})) == 8

    def test_user_defined_functions(self) -> None:
        """Allowing a definition but not the call is not allowing it."""
        source = "def double(n):\n    return n * 2\nanswer = double(21)"
        assert run_script(source, {}) == 42

    def test_comprehensions(self) -> None:
        source = "answer = {n for n in range(10) if n % 2}"
        assert run_script(source, {}) == {1, 3, 5, 7, 9}

    def test_lambda(self) -> None:
        source = "f = lambda n: n + 1\nanswer = sorted([3, 1, 2], key=f)"
        assert run_script(source, {}) == [1, 2, 3]


class TestBudget:
    """With loops allowed, the hazard is non-termination -- handled by a
    budget, not a syntax ban."""

    def test_an_infinite_loop_is_stopped_by_the_budget(self) -> None:
        with pytest.raises(ScriptError, match="may not terminate"):
            run_script("while True:\n    pass\nanswer = 1", {})

    def test_a_huge_iteration_count_is_stopped(self) -> None:
        with pytest.raises(ScriptError, match="may not terminate"):
            run_script("x = 0\nfor i in range(10**9):\n    x = x + 1\nanswer = x", {})

    def test_the_budget_is_adjustable(self) -> None:
        tight = ScriptPolicy(max_steps=20)
        with pytest.raises(ScriptError, match="may not terminate"):
            run_script("x = 0\nfor i in range(500):\n    x = x + 1\nanswer = x", {}, policy=tight)

    def test_execution_inside_an_operator_does_not_count_against_the_budget(self) -> None:
        """Otherwise one `eval_unit` would exhaust it."""

        def heavy(_: object) -> int:
            return sum(range(200_000))

        source = "answer = heavy(None)"
        assert run_script(source, {"heavy": heavy}, policy=ScriptPolicy(max_steps=50)) > 0


class TestIsolation:
    def test_imports_are_not_allowed(self) -> None:
        with pytest.raises(ScriptError, match="Import"):
            run_script("import os\nanswer = 1", {})

    def test_attribute_traversal_is_not_allowed(self) -> None:
        """Otherwise `ctx.judge._config.api_key` becomes reachable."""
        with pytest.raises(ScriptError, match="api_key"):
            run_script("answer = ctx.judge._config.api_key", {"ctx": None})

    def test_arbitrary_builtins_are_not_allowed(self) -> None:
        with pytest.raises(ScriptError, match="eval"):
            run_script("answer = eval('1')", {})

    def test_opening_files_is_not_allowed(self) -> None:
        with pytest.raises(ScriptError, match="open"):
            run_script("answer = open('/etc/passwd')", {})

    def test_dunders_are_not_allowed(self) -> None:
        with pytest.raises(ScriptError, match="__import__"):
            run_script("answer = __import__('os')", {})

    def test_defining_classes_is_not_allowed(self) -> None:
        with pytest.raises(ScriptError, match="ClassDef"):
            run_script("class X:\n    pass\nanswer = X", {})

    def test_an_overlong_script_is_rejected(self) -> None:
        with pytest.raises(ScriptError, match="not a compiled query"):
            run_script("answer = 1\n" * 300, {}, policy=ScriptPolicy(max_lines=10))


class TestContract:
    def test_answer_must_be_produced(self) -> None:
        with pytest.raises(ScriptError, match="answer"):
            run_script("x = 1", {})

    def test_a_syntax_error_gives_a_readable_message(self) -> None:
        with pytest.raises(ScriptError, match="syntax error"):
            run_script("answer = (", {})

    def test_a_runtime_exception_is_wrapped_as_ScriptError(self) -> None:
        with pytest.raises(ScriptError, match="ZeroDivisionError"):
            run_script("answer = 1 / 0", {})

    def test_the_whitelist_and_the_execution_environment_share_a_source(self) -> None:
        """Happened once: `set` was whitelisted but absent from the execution
        environment, and every correct script died."""
        assert set(BUILTIN_NAMES) == set(SAFE_BUILTINS)
        assert set(BUILTIN_NAMES) <= ALLOWED_CALLS
