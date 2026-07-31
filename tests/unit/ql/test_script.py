"""执行模型生成脚本的闸门测试。

这一层挡的是「模型写歪了」，不是有意的攻击——真要防攻击得靠进程隔离。
但两件事必须成立：**该放行的放行，该拦的拦住**。
"""

from __future__ import annotations

import pytest

from codesense.ql import ScriptError, ScriptPolicy, run_script
from codesense.ql.script import ALLOWED_CALLS, BUILTIN_NAMES, SAFE_BUILTINS


class TestControlFlow:
    """QL 建在 Python 上，图的就是它的表达力。控制流必须放行。"""

    def test_条件分支(self) -> None:
        source = "x = 5\nif x > 3:\n    answer = 'big'\nelse:\n    answer = 'small'"
        assert run_script(source, {}) == "big"

    def test_循环(self) -> None:
        source = "total = 0\nfor n in range(5):\n    total = total + n\nanswer = total"
        assert run_script(source, {}) == 10

    def test_迭代收敛_太少就放宽(self) -> None:
        """06 章那个「试跑 → 太少放宽」的回路，可以直接写进脚本里。"""
        source = (
            "answer = None\n"
            "for n in (2, 8, 40):\n"
            "    found = widen(n)\n"
            "    if len(found) >= 8:\n"
            "        answer = found\n"
            "        break\n"
        )
        assert len(run_script(source, {"widen": lambda n: list(range(n))})) == 8

    def test_自定义函数(self) -> None:
        """放行了函数定义却不让调用，等于没放行。"""
        source = "def double(n):\n    return n * 2\nanswer = double(21)"
        assert run_script(source, {}) == 42

    def test_推导式(self) -> None:
        source = "answer = {n for n in range(10) if n % 2}"
        assert run_script(source, {}) == {1, 3, 5, 7, 9}

    def test_lambda(self) -> None:
        source = "f = lambda n: n + 1\nanswer = sorted([3, 1, 2], key=f)"
        assert run_script(source, {}) == [1, 2, 3]


class TestBudget:
    """循环放行之后，要防的是不终止——用预算，不用语法禁令。"""

    def test_死循环被预算拦下(self) -> None:
        with pytest.raises(ScriptError, match="不终止"):
            run_script("while True:\n    pass\nanswer = 1", {})

    def test_巨量迭代被拦下(self) -> None:
        with pytest.raises(ScriptError, match="不终止"):
            run_script("x = 0\nfor i in range(10**9):\n    x = x + 1\nanswer = x", {})

    def test_预算可调(self) -> None:
        tight = ScriptPolicy(max_steps=20)
        with pytest.raises(ScriptError, match="不终止"):
            run_script("x = 0\nfor i in range(500):\n    x = x + 1\nanswer = x", {}, policy=tight)

    def test_算子内部的执行不计入预算(self) -> None:
        """否则一次 `eval_unit` 就能耗光预算。"""

        def heavy(_: object) -> int:
            return sum(range(200_000))

        source = "answer = heavy(None)"
        assert run_script(source, {"heavy": heavy}, policy=ScriptPolicy(max_steps=50)) > 0


class TestIsolation:
    def test_不许_import(self) -> None:
        with pytest.raises(ScriptError, match="Import"):
            run_script("import os\nanswer = 1", {})

    def test_不许属性穿透(self) -> None:
        """否则 `ctx.judge._config.api_key` 这种就有了。"""
        with pytest.raises(ScriptError, match="api_key"):
            run_script("answer = ctx.judge._config.api_key", {"ctx": None})

    def test_不许任意内置函数(self) -> None:
        with pytest.raises(ScriptError, match="eval"):
            run_script("answer = eval('1')", {})

    def test_不许开文件(self) -> None:
        with pytest.raises(ScriptError, match="open"):
            run_script("answer = open('/etc/passwd')", {})

    def test_不许_dunder(self) -> None:
        with pytest.raises(ScriptError, match="__import__"):
            run_script("answer = __import__('os')", {})

    def test_不许定义类(self) -> None:
        with pytest.raises(ScriptError, match="ClassDef"):
            run_script("class X:\n    pass\nanswer = X", {})

    def test_超长脚本被拒(self) -> None:
        with pytest.raises(ScriptError, match="不像是编译产物"):
            run_script("answer = 1\n" * 300, {}, policy=ScriptPolicy(max_lines=10))


class TestContract:
    def test_必须产出_answer(self) -> None:
        with pytest.raises(ScriptError, match="answer"):
            run_script("x = 1", {})

    def test_语法错误给出可读信息(self) -> None:
        with pytest.raises(ScriptError, match="语法错误"):
            run_script("answer = (", {})

    def test_运行时异常被包成_ScriptError(self) -> None:
        with pytest.raises(ScriptError, match="ZeroDivisionError"):
            run_script("answer = 1 / 0", {})

    def test_白名单与执行环境同源(self) -> None:
        """踩过一次：`set` 列进了白名单却没放进执行环境，正确的脚本全军覆没。"""
        assert set(BUILTIN_NAMES) == set(SAFE_BUILTINS)
        assert set(BUILTIN_NAMES) <= ALLOWED_CALLS
