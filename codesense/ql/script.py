"""执行模型生成的查询脚本。

编译产物是代码，所以「跑它」等于执行模型的输出。这个模块是那道闸门：
先用 AST 白名单静态检查，再在受限命名空间里执行。

**控制流是放行的。** QL 建在 Python 上，图的就是它的表达力——
中间变量、条件分支、迭代收敛，算子因此能编排成计算图而不只是一条直线。
禁掉 `for` / `while` 换来的那点确定性不值得；真正要防的是**不终止**，
那用执行预算解决。

**不是沙箱。** 它挡的是「模型写歪了」——import、属性穿透、
文件与网络访问、跑不完的循环——不是有意的攻击。
真要防攻击得靠进程隔离。
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

#: 脚本能用的内置函数。全是纯函数，没有 IO 与反射。
#:
#: **白名单和实际提供的必须同源。** 踩过一次：`set` 列进了白名单
#: 却没放进执行环境，结果生成的脚本完全正确却全军覆没。
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

#: 脚本里允许调用的算子与构造器。
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

#: 允许访问的属性。片段的结构是公开的，但仅限这些——
#: 不放行任意属性，否则 `ctx.judge._config.api_key` 这类穿透就有了。
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

#: 明确禁止的节点类型。
#:
#: **控制流是放行的**——`for` / `while` / `if` / 函数定义都允许。
#: QL 建在 Python 上，图的就是它的表达力：中间变量、条件分支、
#: 迭代收敛（06 章那个「试跑→太少放宽」的回路可以直接写进脚本里），
#: 算子之间因此能编排成任意计算图，而不只是一条直线。
#:
#: 禁掉循环换来的那点确定性不值得——真正要防的是**不终止**，
#: 而那该用执行预算（`max_steps`）解决，不是用语法禁令。
#:
#: 这里留下的都是「脚本形态里没有正当用途、且会打穿隔离」的：
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

#: 放进执行环境的内置函数。与 `BUILTIN_NAMES` 同源，不会漂。
SAFE_BUILTINS = {name: getattr(__import__("builtins"), name) for name in BUILTIN_NAMES}

#: 脚本必须产出的变量名。
RESULT_NAME = "answer"

#: 默认策略。做成模块级单例而不是参数默认值里现构造——
#: 后者每次调用都建一个新对象，也让默认值本身变得可变。
DEFAULT_POLICY: ScriptPolicy


#: 生成脚本的虚拟文件名。追踪时靠它认出「哪些帧算脚本自己的」。
_FILENAME = "<compiled-query>"


class ScriptError(Exception):
    """脚本没通过检查，或者跑挂了。"""


class _BudgetExceeded(Exception):
    """脚本执行超出步数预算——多半是没写终止条件的循环。"""


@contextmanager
def _step_budget(limit: int) -> Iterator[None]:
    """给脚本自身的执行记步。

    只追踪文件名匹配的帧：算子内部是普通 Python，逐行追踪它们会让
    一次 `eval_unit` 慢上几个数量级，而且那不是要防的东西。
    """
    counter = itertools.count()

    def trace_lines(frame: Any, event: str, arg: Any) -> Any:
        if next(counter) > limit:
            raise _BudgetExceeded(f"脚本执行超过 {limit:,} 步，可能有不终止的循环")
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
    """允许什么。默认值就是编译产物该有的形状。"""

    calls: frozenset[str] = field(default=ALLOWED_CALLS)
    attributes: frozenset[str] = field(default=ALLOWED_ATTRS)
    max_lines: int = 200

    #: 脚本自身最多执行多少行。这是循环放行之后的安全阀——
    #: 挡的是不终止，不是挡循环本身。只统计脚本自己的帧，
    #: 算子内部的执行不计入，否则一次 `eval_unit` 就能耗光预算。
    max_steps: int = 200_000


DEFAULT_POLICY = ScriptPolicy()


def check(
    source: str,
    policy: ScriptPolicy | None = None,
    provided: Iterable[str] = (),
) -> ast.Module:
    """静态检查。不通过就抛，**绝不「尽力而为地跑一下」**。

    ``provided`` 是调用方注入命名空间的名字。它们一律可调用——
    命名空间里有什么本来就是调用方决定的，白名单管的是
    「能不能碰到注入之外的东西」。
    """
    policy = policy or DEFAULT_POLICY
    if source.count("\n") > policy.max_lines:
        raise ScriptError(f"脚本超过 {policy.max_lines} 行，不像是编译产物")
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise ScriptError(f"语法错误：{exc}") from exc

    # 脚本自己定义的名字可以调用——白名单管的是「能碰到什么外部能力」，
    # 不是脚本内部。放行了函数定义却不让调用它，等于没放行。
    allowed = policy.calls | _bound_names(tree) | frozenset(provided)

    for node in ast.walk(tree):
        if isinstance(node, FORBIDDEN_NODES):
            raise ScriptError(f"不允许 {type(node).__name__}")
        if isinstance(node, ast.Attribute) and node.attr not in policy.attributes:
            raise ScriptError(f"不允许访问属性 .{node.attr}")
        if isinstance(node, ast.Name) and node.id.startswith("__"):
            raise ScriptError(f"不允许 {node.id}")
        if isinstance(node, ast.Call):
            target = node.func
            if isinstance(target, ast.Name) and target.id not in allowed:
                raise ScriptError(f"不允许调用 {target.id}()")
            if isinstance(target, ast.Attribute) and target.attr not in policy.attributes:
                raise ScriptError(f"不允许调用 .{target.attr}()")
    return tree


def _bound_names(tree: ast.Module) -> frozenset[str]:
    """脚本自己绑定的名字：函数定义、赋值、for 的循环变量、推导式变量。"""
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
    """检查并执行，返回脚本里的 ``answer``。

    ``namespace`` 由调用方给全（`ctx` 与各算子），脚本自己不能 import——
    它拿到什么完全由这里决定。
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
        except Exception as exc:  # noqa: BLE001 —— 脚本跑挂要变成可处理的错误
            raise ScriptError(f"执行失败：{type(exc).__name__}: {exc}") from exc
    if RESULT_NAME not in scope:
        raise ScriptError(f"脚本没有产出 `{RESULT_NAME}`")
    return scope[RESULT_NAME]
