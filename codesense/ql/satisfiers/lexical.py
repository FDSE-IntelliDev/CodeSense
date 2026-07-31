"""词法与注解 satisfier。

两者共用同一条「词 → 扩展 → 倒排」的通路，区别只在查哪些域、
以及注解名要先过元注解展开。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import ClassVar

from codesense.ql.context import EvalContext
from codesense.ql.fields import IndexField
from codesense.ql.satisfiers.base import SATISFIERS, HitsBySymbol, Satisfier, collect_term_hits
from codesense.ql.unit import Term

__all__ = ["AnnotationSatisfier", "LexicalSatisfier", "ModifierSatisfier"]

#: 注解相关的域。注解名和注解参数都要查——`@PreAuthorize("@ss.hasPerm('sys:user:query')")`
#: 的权限串、`@Schema(description=…)` 的自然语言描述都在参数里，
#: 只索引名字就把它们全丢了。
_ANNOTATION_FIELDS = (IndexField.ANNOTATION, IndexField.ANNOTATION_ARG)


@SATISFIERS.decorator("lexical")
@dataclass(frozen=True, slots=True)
class LexicalSatisfier(Satisfier):
    """标识符 / 签名 / 文档里的词法匹配。

    默认权重低（0.5）：词法是**最弱的一种**证据。一个叫 `cacheKey` 的字段
    命中了 `cache` 却和性能无关，只有和别的信号合成后才压得下去。
    """

    signal: ClassVar[str] = "lexical"

    terms: tuple[Term, ...]
    weight: float = 0.5
    fields: tuple[IndexField, ...] | None = None

    def hits(self, unit: str, ctx: EvalContext) -> HitsBySymbol:
        return collect_term_hits(
            unit=unit,
            signal=self.signal,
            terms=self.terms,
            ctx=ctx,
            weight=self.weight,
            fields=self.fields,
        )


@SATISFIERS.decorator("annotation")
@dataclass(frozen=True, slots=True)
class AnnotationSatisfier(Satisfier):
    """注解匹配。

    匹配的是**切分后的注解名单元**，不是字面正则——所以项目自定义的
    `@AppCache`、`@CacheAside` 会和 `@Cacheable` 一起命中 `cache` 单元，
    不需要 LLM 现场生成正则。

    ``names`` 里可以直接点名注解（如 ``"@Transactional"``），它们会先过
    扩展表做**元注解展开**：`@RequestMapping` → `@GetMapping` / `@PostMapping`。
    这层关系是框架在源码里声明的事实，不是估计，所以分数是 1.0。

    默认权重高（0.9）：在 Java/Spring 项目里注解几乎是最强的语义信号——
    `@RestController` 直接说明这是 HTTP 入口，比任何关键词都准。
    """

    signal: ClassVar[str] = "annotation"

    units: tuple[Term, ...] = ()
    names: tuple[str, ...] = ()
    weight: float = 0.9

    def hits(self, unit: str, ctx: EvalContext) -> HitsBySymbol:
        return collect_term_hits(
            unit=unit,
            signal=self.signal,
            terms=(*self.units, *_as_terms(self.names)),
            ctx=ctx,
            weight=self.weight,
            fields=_ANNOTATION_FIELDS,
        )


def _as_terms(names: Sequence[str]) -> tuple[Term, ...]:
    """点名的注解按 literal 词处理；元注解展开由扩展表负责。"""
    return tuple(Term(value=name, source="literal") for name in names)


@SATISFIERS.decorator("modifier")
@dataclass(frozen=True, slots=True)
class ModifierSatisfier(Satisfier):
    """语言级修饰符匹配：`static` / `abstract` / `synchronized` / `native`……

    修饰符是**事实**不是猜测——查「异步的写盘函数」时 `synchronized`、
    `volatile` 是确定的，而名字里有没有 "async" 是猜的。所以默认权重（0.6）
    高于词法（0.5），但低于注解（0.9）：注解携带的语义比修饰符更具体。

    不需要扩展——`static` 就是 `static`，没有 `buf`/`buffer` 那种表层差异。
    扩展表里本来也不会有这些键，所以复用同一条通路是安全的。

    强弱由 ICF 自动区分：`public` 几乎所有符号都有，会被 ICF 下限挡掉；
    `native` / `volatile` 罕见，正是有信息的那些。
    """

    signal: ClassVar[str] = "modifier"

    modifiers: tuple[str, ...] = ()
    weight: float = 0.6

    def hits(self, unit: str, ctx: EvalContext) -> HitsBySymbol:
        return collect_term_hits(
            unit=unit,
            signal=self.signal,
            terms=tuple(Term(value=m, source="literal") for m in self.modifiers),
            ctx=ctx,
            weight=self.weight,
            fields=(IndexField.MODIFIER,),
        )
