"""直接生成查询脚本。

和 `codesense.ql.compile` 那条路的区别只有一处，但很根本：
**把统计信息也给模型**，让它自己排顺序，而不是先抽成结构化中间表示、
再由规划器排。

那条路建立在「模型不知道 `buffer` 在 netty 里命中 2365 个符号」之上。
但那是提示词设计问题，不是架构必然——词表带上 `df` 一起给它，
它就拥有和规划器一样的依据。而且脚本能表达中间表示表达不了的东西：
临时变量、条件、任意组合，这正是 06 章选择脚本而非 JSON 计划的理由。

产出要过 `codesense.ql.script` 的白名单检查才执行。
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence

from codesense.llm.config import LlmConfig

__all__ = ["OPERATOR_SPEC", "PROMPT", "ScriptGenerator"]

OPERATOR_SPEC = """\
可用的算子（全部返回 Frag —— 一个带证据的代码子图）：

  eval_unit(unit, ctx) -> Frag
      求值一个查询单元。单元由若干 satisfier 组成，命中越多分越高。

  reach(frag, ctx, *, edge=["calls"], direction="forward"|"backward"|"any",
        hops=(lo, hi)) -> Frag
      从 frag 出发能到达的符号。只要节点，不要路径。便宜。

  hop(src, dst, ctx, *, edge, direction, hops, avoid=None,
      min_confidence=0.0, max_paths=10000) -> Frag
      src 与 dst 之间满足图约束的**路径**。比 reach 贵得多，
      只有确实需要路径本身时才用。

  only(frag, *, kind=None, file=None, where=None) -> Frag      按属性筛
  top(frag, n, *, by=None) -> Frag                             按分数取前 n
  score_of(frag, symbol_id, by=None) -> float                  取分数
  degree(frag, ctx, *, min_in=None, max_in=None, edge=...) -> Frag

  intent(frag, "判定标准", ctx, *, threshold=0.5, max_items=60) -> Frag
      LLM 逐个判定。**比一次查表贵约 5000 倍**，必须放最后、
      且输入先压到 60 个以内。

Frag 支持 `|`（并）`&`（交）`-`（差），以及 .nodes / .induced(ids) /
.roots() / .leaves()。

**分数从哪来，这一条最容易写错：**
只有 `eval_unit` 产出的 Frag 带词法分数。`reach` / `hop` 产出的 Frag
**没有分数**——对它们用 `top` 等于随便取。所以图信息要当**加权**用，
不能当替换用：留住 `eval_unit` 的结果，只拿图去调整它的排序。

构造单元：

  QueryUnit("名字", concept="给 intent 看的说明", satisfiers=(...))
  LexicalSatisfier(terms=(Term("词", weight=0.8), ...), weight=0.5)
  AnnotationSatisfier(units=(Term("cache"),), names=("@Cacheable",), weight=0.9)
  ModifierSatisfier(modifiers=("static", "native"), weight=0.6)

标准写法（照着改，别另起炉灶）：

```python
# 一个单元，词按相关度加权；宁可多要词，ICF 会自动降权
q = QueryUnit("q", satisfiers=(
    LexicalSatisfier(terms=(
        Term("pool", weight=0.9), Term("arena", weight=0.9), Term("chunk", weight=0.8),
        Term("recycler", weight=0.8), Term("alloc", weight=0.6), ...   # 15~30 个
    ), weight=0.5),
))
frag = eval_unit(q, ctx)

# 图邻近性：取最强的当种子，落在邻域里的**加权**——注意 frag 没有被替换
near = reach(top(frag, 20), ctx, edge=["calls", "contains"], direction="any", hops=(1, 2))
boosted = set(near.nodes) & set(frag.nodes)
frag = frag.induced(sorted(
    frag.nodes,
    key=lambda s: (-score_of(frag, s) * (1 + 0.6 * (s in boosted)), s),
)[:60])

answer = intent(frag, "判定标准", ctx, max_items=60)
```

**脚本就是普通 Python，控制流随便用。** 这是它相对固定管线的全部意义——
中间变量、条件分支、循环收敛都可以，算子之间因此能编排成计算图而不只是直线。
比如「先窄后宽」的自适应写法：

```python
# 先试窄的；候选太少再放宽，不必一开始就猜对
core = QueryUnit("core", satisfiers=(LexicalSatisfier(terms=NARROW, weight=0.5),))
wide = QueryUnit("wide", satisfiers=(LexicalSatisfier(terms=BROAD, weight=0.4),))

frag = eval_unit(core, ctx)
if len(frag) < 30:                    # 太窄，把外围词并进来
    frag = frag | eval_unit(wide, ctx)
```

也可以按不同边类型分别探，再合起来：

```python
by_call = reach(seeds, ctx, edge=["calls"], direction="any", hops=(1, 2))
by_type = reach(seeds, ctx, edge=["contains"], direction="any", hops=(1, 1))
strong = set(by_call.nodes) & set(by_type.nodes)   # 两种边都连着的，信号更强
```
"""

PROMPT = """\
你在为代码检索系统生成一段查询脚本。

代码库：{project}（{symbols} 个符号，{edges} 条边）
查询：{query}

{spec}

这个代码库里的词，格式是 `词:命中多少个符号`（**terms 只能从中挑**）：
{vocab}

写脚本的要点：

- **先看 df 再决定编排。** 命中几万个的词和命中几十个的词，
  在同一条 OR 里是完全不同的东西。
- **单元之间用并集不用交集。** 「同时含有 A 和 B 关键词的元素」几乎不存在；
  两个概念通常落在**不同元素**上，要靠图连起来。
- **词要给够：15~30 个**，只给三五个会严重伤召回。
  不确定某个词该不该要时宁可要——它会被 ICF 自动降权。
- **图邻近性总是有用的**，但它是**加权**不是替换：
  别把 `reach` 的结果直接当候选集，那样词法分数就全丢了。
- **不要过早截断。** `top` 只在最后、交给 `intent` 之前用一次。
- **`intent` 放最后**，输入先压到 60 以内。

只输出脚本，不要解释。脚本必须：
- 把结果赋给变量 `answer`
- 直接使用 `ctx` 和上面列出的算子（**不要写 import**）
- 用注释说明**为什么这么排**，不要解释算子是什么

可以用 `for` / `while` / `if` / `def`，但循环必须能终止（有步数上限）。
不能 import，不能碰算子与 Frag 之外的东西。
"""

_FENCE = re.compile(r"```(?:python)?\s*(.*?)```", re.S)

_log = logging.getLogger(__name__)


class ScriptGenerator:
    """让模型直接写查询脚本。"""

    def __init__(self, config: LlmConfig, session: object | None = None) -> None:
        self._config = config
        self._session = session

    def generate(
        self,
        query: str,
        project: str,
        vocabulary: Sequence[tuple[str, int]],
        *,
        symbols: int,
        edges: int,
    ) -> str | None:
        """``vocabulary`` 是 (词, df) 对——**df 是关键**，没有它模型排不出顺序。"""
        prompt = PROMPT.format(
            project=project,
            query=query,
            spec=OPERATOR_SPEC,
            symbols=f"{symbols:,}",
            edges=f"{edges:,}",
            vocab=" ".join(f"{term}:{df}" for term, df in vocabulary),
        )
        content = self._ask(prompt)
        if content is None:
            return None
        fenced = _FENCE.search(content)
        return (fenced.group(1) if fenced else content).strip()

    def _ask(self, prompt: str) -> str | None:
        session = self._session
        if session is None:
            import requests

            session = requests.Session()
        try:
            response = session.post(  # type: ignore[attr-defined]
                f"{self._config.base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {self._config.api_key}"},
                json={
                    "model": self._config.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0,
                },
                timeout=self._config.timeout,
            )
            response.raise_for_status()
            return str(response.json()["choices"][0]["message"]["content"])
        except Exception:  # noqa: BLE001 —— 生成失败要能降级
            _log.exception("调用代码生成接口失败")
            return None
