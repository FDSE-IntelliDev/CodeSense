"""把执行计划渲染成一段可读、可改、可跑的 Python 脚本。

06 章的判断是**产物应当是脚本而不是 JSON 计划**：研究场景里「改一下再试」
发生得极其频繁，而脚本可以打断点、注释一行看差异、手工改完再跑。

所以 `Plan` 是内部表示，脚本才是交付物。两者必须等价——
`tests/unit/ql/compile/test_emit.py` 会实际跑一遍生成的脚本，
断言它和直接执行 `Plan` 得到同一个结果。

注释只解释**编排理由**，不解释算子语义——算子语义在 05 章里。
"""

from __future__ import annotations

import textwrap
from collections.abc import Sequence

from codesense.ql.compile.plan import Boost, Cohere, EvalUnit, Intent, Narrow, Plan, Step
from codesense.ql.compile.spec import QuerySpec
from codesense.ql.satisfiers.lexical import AnnotationSatisfier, LexicalSatisfier, ModifierSatisfier
from codesense.ql.unit import QueryUnit

__all__ = ["to_script"]

_HEADER = '''"""{title}

编译自：{query}
索引：{index}
"""
from codesense.ql.compile import Intent
from codesense.ql.operators import eval_unit, intent, reach, score_of, top
from codesense.ql.satisfiers import AnnotationSatisfier, LexicalSatisfier, ModifierSatisfier
from codesense.ql.unit import QueryUnit, Term
'''


def to_script(plan: Plan, spec: QuerySpec, *, index: str = "<未记录>") -> str:
    """渲染成脚本。

    ``index`` 要记进头部：同一段脚本在不同索引上结果不同，不记就没法复现。
    """
    lines = [
        _HEADER.format(
            title=spec.query or "查询",
            query=spec.query or "（未记录）",
            index=index,
        )
    ]
    lines.append("\n# ── 查询单元 " + "─" * 46)
    for step in plan.steps:
        if isinstance(step, EvalUnit):
            lines.append(_unit_source(step.unit))

    lines.append("\n# ── 编排 " + "─" * 50)
    for why in plan.reasoning:
        lines.append(_comment(why))
    lines.append("")

    body: list[str] = []
    for step in plan.steps:
        rendered = _step_source(step)
        if rendered:
            body.append(rendered)
    lines.append("\n".join(body))
    lines.append("\nanswer = frag")
    lines.append(
        "\n# 与 `Plan` 等价——tests/unit/ql/compile/test_emit.py 会跑这段脚本并断言两者结果相同"
    )
    return "\n".join(lines).rstrip() + "\n"


def _comment(text: str, width: int = 76) -> str:
    return "\n".join(f"# {line}" for line in textwrap.wrap(text, width) or [""])


def _unit_source(unit: QueryUnit) -> str:
    parts = [f'{_ident(unit.name)} = QueryUnit(\n    "{unit.name}",']
    if unit.concept:
        parts.append(f'    concept="{_escape(unit.concept)}",')
    parts.append("    satisfiers=(")
    for satisfier in unit.satisfiers:
        parts.append(_satisfier_source(satisfier))
    parts.append("    ),")
    parts.append(")")
    return "\n".join(parts)


def _satisfier_source(satisfier: object) -> str:
    if isinstance(satisfier, LexicalSatisfier):
        terms = ", ".join(
            f'Term("{t.value}", weight={t.weight:.2f})' if t.weight != 1.0 else f'Term("{t.value}")'
            for t in satisfier.terms
        )
        return _wrap(f"LexicalSatisfier(terms=({terms},), weight={satisfier.weight:.3f}),")
    if isinstance(satisfier, AnnotationSatisfier):
        names = ", ".join(f'"{n}"' for n in satisfier.names)
        units = ", ".join(f'Term("{t.value}")' for t in satisfier.units)
        args = []
        if units:
            args.append(f"units=({units},)")
        if names:
            args.append(f"names=({names},)")
        return _wrap(f"AnnotationSatisfier({', '.join(args)}, weight={satisfier.weight:g}),")
    if isinstance(satisfier, ModifierSatisfier):
        mods = ", ".join(f'"{m}"' for m in satisfier.modifiers)
        return _wrap(f"ModifierSatisfier(modifiers=({mods},), weight={satisfier.weight:g}),")
    return _wrap(f"{satisfier!r},")


def _wrap(text: str, indent: str = "        ") -> str:
    return "\n".join(
        textwrap.wrap(
            text,
            width=92,
            initial_indent=indent,
            subsequent_indent=indent + "    ",
            break_long_words=False,
            break_on_hyphens=False,
        )
    )


def _step_source(step: Step) -> str:
    if isinstance(step, EvalUnit):
        name = _ident(step.unit.name)
        if step.seed:
            return f"frag = eval_unit({name}, ctx)"
        # 并集不是交集——单元本来就落在不同元素上
        return f"frag = frag | eval_unit({name}, ctx)"
    if isinstance(step, Cohere):
        # 只标记邻域，不在这里重排——排序统一在 `Narrow` 里做，
        # 与 `Plan` 的语义保持一致（`Cohere` 填 state.boosted，`Narrow` 消费它）
        return "\n".join(
            [
                f"# 结构凝聚：最强的 {step.seeds} 个当种子，向外 {step.hops[0]}~{step.hops[1]} 跳",
                f"near = reach(top(frag, {step.seeds}), ctx,",
                f'             edge={list(step.edge)!r}, direction="any", hops={step.hops!r})',
                "boosted = set(near.nodes) & set(frag.nodes)",
            ]
        )

    if isinstance(step, Boost):
        return (
            f"# 图约束：{step.src_name} ↔ {step.dst_name}（统计校验过）\n"
            f"near = reach(eval_unit({_ident(step.src_name)}, ctx), ctx,\n"
            f'             edge={list(step.edge)!r}, direction="any", hops={step.hops!r})'
        )
    if isinstance(step, Narrow):
        if not step.limit:
            return ""
        lines = []
        multiplier = "(1 + 0.6 * (s in boosted))"
        if step.kind:
            lines.append(
                f"# 种类是**偏好不是过滤**：模型给的种类不可靠，硬过滤会把答案删光\n"
                f"preferred = {{s for s, e in frag.nodes.items() if e.kind in {list(step.kind)!r}}}"
            )
            multiplier += " * (1 + 0.3 * (s in preferred))"
        lines.append(
            f"frag = frag.induced(sorted(\n"
            f"    frag.nodes,\n"
            f"    key=lambda s: (-score_of(frag, s) * {multiplier}, s),\n"
            f")[:{step.limit}])"
        )
        return "\n".join(lines)
    if isinstance(step, Intent):
        return (
            f'frag = intent(frag, "{_escape(step.concept)}", ctx,\n'
            f"              threshold={step.threshold:g}, max_items={step.max_items})"
        )
    return f"# 未知步骤: {step.label}"


def _ident(name: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in name)
    return f"unit_{cleaned}" if not cleaned[:1].isalpha() else cleaned


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


def script_of(plan: Plan, spec: QuerySpec, index: str = "<未记录>") -> Sequence[str]:
    """按行返回，方便测试逐行断言。"""
    return to_script(plan, spec, index=index).splitlines()
