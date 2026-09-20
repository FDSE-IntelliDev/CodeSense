# CodeSense QL 算子参考

本文以现役实现 `codesense/ql/`、`codesense/search.py` 和对应测试为准，说明一条自然语言查询如何进入 QL、各个算子处理什么数据，以及编译器如何组织这些算子。

> `legacy/` 中的 SemCon → SemQL → 三执行器属于归档实现，不在本文范围内。

## 1. 总体流程

CodeSense 不直接把自然语言当作正则表达式执行，而是先把查询转换为可组合的检索单元和图关系，再运行 QL：

```text
自然语言查询
    │
    ├─ codegen：LLM 直接生成 Python QL 脚本
    ├─ planned：LLM 只生成结构化查询理解，系统构建 QuerySpec 和 Plan
    └─ lexical：不调用 LLM，直接使用查询词和项目词汇接地
    │
    ▼
QueryUnit / Satisfier 基础召回
    ▼
Frag 集合代数、图遍历、投影、筛选与排序
    ▼
可选 intent 语义判断
    ▼
统一执行硬 target 结果约束
    ▼
SearchResult：排序结果、证据、实际 route、生成脚本和说明
```

三条 route 最终共享相同的索引、`Frag` 数据模型和 QL 算子。若 `codegen` 或 `planned` 无法运行，搜索会退化到 `lexical`；结果中的 `route` 记录实际执行的路线。

## 2. 核心心智模型

QL 中只有一种对象在算子之间流动：`Frag`。

```text
QueryUnit + EvalContext
        │
        ▼
    eval_unit
        │
        ▼
Frag(nodes, edges, evidence, witnesses)
        │
        ├─ | / & / -             集合代数
        ├─ only / degree / top    过滤与排序
        ├─ hop / reach            多跳图关系
        ├─ project                精确单跳端点转换
        └─ intent                 LLM 语义判断
```

各层职责：

| 层 | 作用 |
|---|---|
| `Term` | 描述一个查询词及其来源、权重和原因 |
| `Satisfier` | 把词、注解或修饰符转换为带分数的命中 |
| `QueryUnit` | 表示一个语义槽，并组合多个召回信号 |
| `EvalContext` | 注入符号表、倒排索引、词汇扩展、图和 Judge |
| `Frag` | 保存候选节点、子图、路径见证和证据 |
| QL 算子 | 对 `Frag` 做召回、组合、图计算、过滤和判断 |

## 3. 数据模型

### 3.1 `Term`

```python
Term(
    value: str,
    source: str = "literal",
    weight: float = 1.0,
    reason: str = "",
)
```

`source` 常见取值：

| 值 | 含义 |
|---|---|
| `literal` | 查询中直接出现，通常最可信 |
| `synonym` | 语义上近似或可替换 |
| `derived` | 从意图派生的项目相关词，召回强但需要其他证据约束 |

`Term` 不是正则表达式。它会经过项目词汇扩展表，再查询倒排索引。

### 3.2 `QueryUnit`

```python
QueryUnit(
    name: str,
    concept: str = "",
    satisfiers: tuple[object, ...] = (),
    combine: str = "noisy_or",
)
```

- `name` 是证据和图关系引用的稳定标识。
- `concept` 保存完整自然语言语义，但不直接参与 `eval_unit` 匹配。
- `satisfiers` 是当前单元的召回信号。
- `combine` 控制多个 Satisfier 分数如何合并。

多个概念若可能落在不同代码元素上，应拆成多个 Unit，再使用图关系连接：

```python
caller = eval_unit(caller_unit, ctx)
cache = eval_unit(cache_unit, ctx)
linked = hop(caller, cache, ctx, edge="calls")
```

### 3.3 现役 Satisfier

#### `LexicalSatisfier`

```python
LexicalSatisfier(
    terms: tuple[Term, ...],
    weight: float = 0.5,
    fields: tuple[IndexField, ...] | None = None,
)
```

默认查询名称、签名、容器和文档等词法字段。`fields` 可限制范围。

#### `AnnotationSatisfier`

```python
AnnotationSatisfier(
    units: tuple[Term, ...] = (),
    names: tuple[str, ...] = (),
    weight: float = 0.9,
)
```

查询注解名称和参数。`names` 会经过 meta-annotation 扩展，例如从框架声明的注解关系扩展到派生注解。

#### `ModifierSatisfier`

```python
ModifierSatisfier(
    modifiers: tuple[str, ...] = (),
    weight: float = 0.6,
)
```

查询 `static`、`abstract`、`synchronized`、`native` 等语言级修饰符。

当前没有 `SemanticSatisfier`、`RegexSatisfier`、`GraphSatisfier` 或 `CodeQLSatisfier`。语义接地在索引构建时写入 expansion table；图关系由图算子处理。

### 3.4 `EvalContext`

```python
EvalContext(
    symbols,
    postings,
    expansion,
    edges,
    declaration_count: int | None = None,
    field_weights=DEFAULT_FIELD_WEIGHTS,
    judge=NullJudge(),
    icf_floor: float = 0.34,
    min_hit_score: float = 1e-6,
)
```

`EvalContext` 不自行读配置或文件，所有依赖由构造函数注入。

`population` 用于 ICF 和规划器选择率估计。索引包含文件节点时，它使用 `declaration_count`，避免文件节点扭曲声明级统计；旧调用方未提供该值时则回退到全部元素数量。

### 3.5 `Element`、`Edge` 和 `Path`

```python
Element(
    symbol_id, name, kind, file, span,
    signature="", container="", language="",
    doc="", modifiers=frozenset(),
)

Edge(
    source_id, target_id, kind,
    site=None, confidence=1.0, provenance="",
)

Path(nodes: tuple[int, ...], edges: tuple[Edge, ...])
```

`confidence` 和 `provenance` 是图证据的一部分，不只是附加信息。轻量图无法完整处理动态分派、反射和外部依赖，因此图算子可以通过 `min_confidence` 排除弱边。

### 3.6 当前图节点和边

索引中的节点包括代码声明和 `kind="file"` 的文件节点。文件节点没有倒排 postings，主要通过图关系投影得到；`declaration_count` 因而单独保存。

现役索引构建以下边：

| 边 | 方向 | 含义 |
|---|---|---|
| `contains` | 容器声明 → 成员声明 | 从 `container` 字段派生，置信度 1.0 |
| `calls` | 调用者声明 → 被调用声明 | 轻量类型/名称解析得到，可能不完整 |
| `references` | 最小引用宿主声明或文件 → 被引用声明 | 广义引用；调用和 import 也会同时形成该边 |
| `imports` | 文件 → 被 import 的声明 | import 的精确关系 |
| `in_file` | 声明 → 所属文件 | 源路径事实，置信度 1.0 |
| `extends` | 子类型 → 父类型 | Java `supertypes` 的低成本近似关系 |
| `implements` | 实现类型/方法 → 接口/接口方法 | 类型和方法共享 edge kind，由端点 kind 区分 |
| `overrides` | 覆盖方法 → 被覆盖方法 | Java 签名启发式关系 |

图是项目内、轻量、无需完整构建的近似图，不应当成完整 CodeQL/LSP 语义图。

### 3.7 `Frag`

```python
Frag(
    nodes: Mapping[int, Element] = {},
    edges: Mapping[tuple[int, int, str], Edge] = {},
    evidence: Mapping[int, Evidence] = {},
    witnesses: tuple[Path, ...] = (),
)
```

- `nodes`：当前候选或路径中的元素。
- `edges`：两端都在当前 fragment 内的边。
- `evidence`：节点为何命中的追加式证据。
- `witnesses`：`hop` 返回的实际路径。

`Frag` 的相等性只比较节点和边，不比较证据与路径，保证集合代数满足幂等性。

#### 集合代数

```python
union = a | b          # 合并节点、边、路径和证据
common = a & b         # 保留共同节点，并合并两侧证据
remaining = a - b      # 删除 b 中的节点及相关边
```

#### 辅助方法

```python
frag.induced(symbol_ids)   # 取指定节点的诱导子图
frag.roots()               # 当前 fragment 内入度为 0 的节点
frag.leaves()              # 当前 fragment 内出度为 0 的节点
frag.only_nodes()          # 丢弃边和路径，只保留节点与证据
frag.evidence_for(sid)     # 获取节点证据；不存在时返回空 Evidence
```

`roots()`/`leaves()` 只检查 fragment 内的边；它们与基于完整索引图计算的 `degree()` 不同。

### 3.8 证据

```python
UnitHit(unit, signal, detail, field="", score=1.0, span=None)
Verdict(source, label, reason="", score=1.0)
Evidence(unit_hits=(), verdicts=())
```

证据遵循追加式合并。`eval_unit` 会保留原始命中，并为每个 Unit 追加一个 `signal="combined"` 的权威汇总分数。`Evidence.scores` 优先读取该汇总，避免重复计分。

## 4. 算子总览

```python
from codesense.ql.operators import (
    degree,
    eval_unit,
    hop,
    intent,
    only,
    project,
    reach,
    score_of,
    top,
)
```

| 算子 | 输入 | 输出 | 核心用途 |
|---|---|---|---|
| `eval_unit` | `QueryUnit` | 节点型 `Frag` | 从倒排索引召回并打分 |
| `only` | `Frag` | `Frag` | 按元素属性做硬过滤 |
| `top` | `Frag` | `Frag` | 按证据分数取前 N 个 |
| `score_of` | `Frag` + 节点 ID | `float` | 读取一个节点的分数 |
| `degree` | `Frag` + 图 | `Frag` | 按全图入度/出度过滤 |
| `hop` | 两个 `Frag` + 图 | 带路径的 `Frag` | 验证两组候选间的多跳路径 |
| `reach` | 一个 `Frag` + 图 | 节点型 `Frag` | 探索多跳可达节点 |
| `project` | 一个 `Frag` + 图 | 节点型 `Frag` | 沿一条边精确转换到另一端 |
| `intent` | `Frag` + 自然语言 | `Frag` | 使用 LLM 判断完整意图 |

## 5. `eval_unit`

```python
eval_unit(unit: QueryUnit, ctx: EvalContext) -> Frag
```

执行过程：

1. 依次执行 Unit 中的每个 Satisfier。
2. 同一 Satisfier 内的多条命中使用 `noisy_or` 合并，避免派生词无限累加。
3. 不同 Satisfier 的分数使用 `unit.combine` 合并。
4. 返回只含节点和证据的 `Frag`，不自动添加图边。

多个 Term 和多个 Satisfier 都是带分数的软组合，不是布尔 AND。只要任意 Term 经词汇扩展后命中任意字段，或者任意 Satisfier 产生证据，该元素就会进入召回结果；更多信号只会提高分数。

同一 Satisfier 内固定使用 `noisy_or`：

```text
score = 1 - ∏(1 - score_i)
```

例如两个分数均为 `0.5` 的命中组合为 `0.75`。不同 Satisfier 之间则使用 `QueryUnit.combine`，当前可选 `noisy_or`、`max` 和 `sum`。这些策略只决定得分，不改变“任意信号命中即可召回”的成员关系。

```python
cache_unit = QueryUnit(
    "cache",
    concept="code that caches computed results",
    satisfiers=(
        LexicalSatisfier(
            terms=(Term("cache"), Term("memoize", source="synonym", weight=0.8)),
        ),
        AnnotationSatisfier(names=("@Cacheable",)),
    ),
)

cache = eval_unit(cache_unit, ctx)
```

### 5.1 严格的逻辑 AND、OR、NOT

严格布尔关系不在 `eval_unit` 内部执行，而是在多个 Unit 分别召回后，通过 `Frag` 集合代数组合：

| 语义 | QL 表达式 | 集合含义 |
|---|---|---|
| OR | `left \| right` | 两侧候选并集，并合并证据 |
| AND | `left & right` | 只保留两侧同时命中的节点，并合并证据 |
| NOT | `left - right` | 从左侧删除右侧节点及相关边 |

如果要求一个元素同时满足两个概念，应建立两个 Unit，而不是把两个 Term 塞进同一个 Satisfier：

```python
cache = eval_unit(cache_unit, ctx)
buffer = eval_unit(buffer_unit, ctx)
tests = eval_unit(test_unit, ctx)

cache_or_buffer = cache | buffer
cache_and_buffer = cache & buffer
cache_not_test = cache - tests
```

因此，`eval_unit` 负责尽量召回并保留证据，`Frag` 代数负责精确表达逻辑关系。这种分层避免召回阶段过早删除候选，也让 AND、OR、NOT 可以继续与图算子组合。

## 6. `only`

```python
only(
    frag: Frag,
    *,
    kind: str | Sequence[str] | None = None,
    file: str | Sequence[str] | None = None,
    language: str | None = None,
    where: Callable[[Element], bool] | None = None,
) -> Frag
```

所有非 `None` 条件以 AND 组合。空序列表示显式匹配空集合，而不是禁用条件。

```python
methods = only(found, kind=("method", "constructor"), language="java")
controllers = only(found, where=lambda e: e.name.endswith("Controller"))
```

`only` 是硬过滤；编译计划里的 `Narrow(kind=...)` 则只是软偏好，两者不要混淆。

## 7. `top` 与 `score_of`

```python
top(frag: Frag, n: int, *, by: str | None = None) -> Frag
score_of(frag: Frag, symbol_id: int, by: str | None = None) -> float
```

- `by=None`：使用所有 Unit 分数之和。
- `by="unit_name"`：只使用指定 Unit 的分数。
- 同分时按 `symbol_id` 升序，结果可复现。
- `top(..., n<0)` 会报错。

```python
best_overall = top(found, 20)
best_cache = top(found, 20, by="cache")
score = score_of(found, symbol_id, by="cache")
```

## 8. `degree`

```python
degree(
    frag: Frag,
    ctx: EvalContext,
    *,
    edge: str | Sequence[str] | None = "calls",
    min_in: int | None = None,
    max_in: int | None = None,
    min_out: int | None = None,
    max_out: int | None = None,
) -> Frag
```

度数在完整索引图上计算，而不是只看当前 fragment：

```python
entry_like = degree(methods, ctx, edge="calls", max_in=0)
popular = degree(methods, ctx, edge="calls", min_in=20)
```

`edge=None` 统计所有边。若不传任何度数边界，返回内容不变。

## 9. `hop`

```python
hop(
    src: Frag,
    dst: Frag,
    ctx: EvalContext,
    *,
    edge: str | Sequence[str] = "calls",
    direction: str = "forward",
    hops: int | tuple[int, int] = (1, 3),
    via: Frag | None = None,
    avoid: Frag | None = None,
    min_confidence: float = 0.0,
    max_paths: int | None = 10_000,
    max_degree: int | None = 64,
) -> Frag
```

`hop` 验证 `src` 与 `dst` 之间是否存在满足约束的路径，并返回：

- 路径上的全部节点；
- 路径边；
- `Path` witnesses；
- 两端原有证据。

关键语义：

- `hops` 是闭区间；`2` 等价于 `(2, 2)`。
- `direction` 支持 `forward`、`backward`、`any`。
- `via` 要求路径经过给定节点集合。
- `avoid` 禁止路径经过给定节点集合。
- `max_degree` 避免通过无区分度的高连接 hub 扩散。
- 达到 `max_paths` 会截断并记录 warning，结果可能不完整。

```python
paths = hop(
    callers,
    cache,
    ctx,
    edge=("calls", "references"),
    direction="forward",
    hops=(1, 3),
    min_confidence=0.5,
)
```

### 9.1 `AND_HOP` 如何由原子能力组合得到

现役 QL 没有名为 `and_hop` 的单一原子算子。归档实现中的 `AND_HOP(left, right, n)` 表示：两组候选命中同一元素，或者在指定跳数内通过无向调用关系连接；最终只返回参与关系的两端。

它可以由现役原子能力组合得到：

```python
def and_hop(left: Frag, right: Frag, ctx: EvalContext, max_hops: int) -> Frag:
    # 距离 0：同一元素同时命中两侧，等价于普通 AND。
    same_element = left & right
    if max_hops == 0:
        return same_element

    # 距离 1..n：验证两组候选之间的无向调用路径。
    connected_paths = hop(
        left,
        right,
        ctx,
        edge="calls",
        direction="any",
        hops=(1, max_hops),
    )

    # hop 包含中间路径节点；AND_HOP 的结果只保留关系端点。
    endpoints = set(left.nodes) | set(right.nodes)
    connected_endpoints = connected_paths.induced(endpoints)
    return same_element | connected_endpoints
```

对应关系是：

```text
AND_HOP(left, right, 0)
    = left & right

AND_HOP(left, right, n)
    = (left & right)
      | endpoints(hop(left, right, direction="any", hops=(1, n)))
```

如果存在多条 pair rule，可以分别得到每一对的 `and_hop` 结果，再通过 `&` 组合；不参与 pair rule 的普通条件也可作为额外 `Frag` 继续求交。

### 9.2 与 planned route 的区别

上面的组合是在手写 QL 中还原旧版 `AND_HOP` 的硬约束语义。planned route 有意采用更保守的召回策略：

```text
EvalUnit（各语义槽分别召回）
    → Unit 结果并集
    → Boost（内部基于 reach 标记满足图关系的候选）
    → Narrow（排序并截取候选）
```

也就是说，planned route 中的图关系默认用于加权而不是硬过滤。原因是当前轻量代码图可能漏掉动态分派、反射或外部依赖；如果直接执行硬 `AND_HOP`，缺失一条边就可能删除正确答案。需要关系必须成立时，应手写 `hop`，或使用绑定 `$result` 端点的 `result_relation`。

### 9.3 cache/buffer：由原子算子编排复杂语义查询

下面的查询表达：

```text
查找同时涉及 cache 与 buffer，或两者在 1..3 跳调用关系内相连的代码，
排除 test 相关结果，只返回方法或构造器，并取分数最高的 20 个。
```

先定义三个独立语义槽：

```python
cache_unit = QueryUnit(
    "cache",
    concept="code that caches or reuses computed results",
    satisfiers=(
        LexicalSatisfier(
            terms=(
                Term("cache"),
                Term("memoize", source="synonym", weight=0.8),
            ),
        ),
        AnnotationSatisfier(names=("@Cacheable",)),
    ),
)

buffer_unit = QueryUnit(
    "buffer",
    concept="code that manages in-memory data buffers",
    satisfiers=(
        LexicalSatisfier(
            terms=(
                Term("buffer"),
                Term("bytebuf", source="derived", weight=0.8),
            ),
        ),
    ),
)

test_unit = QueryUnit(
    "test",
    concept="test-only code",
    satisfiers=(
        LexicalSatisfier(terms=(Term("test"), Term("mock"))),
    ),
)
```

再组合原子算子：

```python
# 原子召回：每个 Unit 内部是带分数的软 OR。
cache = eval_unit(cache_unit, ctx)
buffer = eval_unit(buffer_unit, ctx)
tests = eval_unit(test_unit, ctx)

# 基础布尔组合可以独立使用。
cache_or_buffer = cache | buffer
cache_and_buffer = cache & buffer
cache_not_test = cache - tests

# 图感知 AND：接受同一元素共现，或 1..3 跳内存在调用路径。
cache_and_hop_buffer = and_hop(cache, buffer, ctx, max_hops=3)

# NOT + 属性过滤 + 排序。
answer = top(
    only(
        cache_and_hop_buffer - tests,
        kind=("method", "constructor"),
    ),
    20,
)
```

这段编排展示了 QL 的核心设计：原子算子保持职责单一，复杂语义通过数据流组合表达。

```text
QueryUnit / eval_unit       语义槽召回和证据评分
Frag |、&、-                OR、AND、NOT
hop + induced              图关系验证和端点投影
only                       硬属性约束
top                        证据驱动排序
```

因此，扩展查询能力不一定需要不断增加新的复合算子。只要现有原子算子能够保留节点、关系和证据，就可以通过 Python QL 的变量、控制流和集合代数组装出更复杂、仍然可检查的语义搜索语句。

## 10. `reach`

```python
reach(
    src: Frag,
    ctx: EvalContext,
    *,
    edge: str | Sequence[str] = "calls",
    direction: str = "forward",
    hops: int | tuple[int, int] = (1, 3),
    min_confidence: float = 0.0,
) -> Frag
```

`reach` 从一组起点探索可达节点，不要求目标，也不保留路径和源节点证据。

```python
neighbours = reach(seeds, ctx, edge="calls", direction="any", hops=(1, 2))
```

适合候选扩展和结构邻近计算；若需要证明两组候选确实由一条路径连接，应使用 `hop`。

## 11. `project`

```python
project(
    src: Frag,
    ctx: EvalContext,
    *,
    edge: str | Sequence[str] = "in_file",
    direction: str = "forward",
    kind: str | Sequence[str] | None = None,
    include_self: bool = False,
    min_confidence: float = 0.0,
) -> Frag
```

`project` 沿指定边精确走一步，把当前候选转换成另一端节点。它与 `reach(..., hops=1)` 的主要差别是：

- 只支持 `forward`/`backward`，语义明确；
- 可以直接过滤目标 `kind`；
- 将源节点证据传递给目标节点；
- 追加 `signal="graph"`、零分的投影证据，记录边类型、方向、来源和位置；
- 返回节点型 `Frag`，不保留边和路径。

声明投影到所属文件：

```python
files = project(methods, ctx, edge="in_file", kind="file")
```

从被引用声明反向找到引用者，再投影到文件：

```python
referencers = project(
    page_request,
    ctx,
    edge="references",
    direction="backward",
)
files = project(referencers, ctx, edge="in_file", kind="file")
```

从接口反向查找实现类或实现方法：

```python
implementations = project(
    interface,
    ctx,
    edge="implements",
    direction="backward",
    min_confidence=0.8,
)
```

继承关系边统一从具体声明指向抽象声明；从抽象端查找实现或覆盖时使用
`direction="backward"`。Java 关系来自轻量 AST 启发式，`min_confidence` 可排除
较弱的名称/参数数量匹配。

`include_self=True` 用于输入可能已经包含目标类型的场景，例如统一执行文件 target 时保留已有文件节点。

## 12. `intent`

```python
intent(
    frag: Frag,
    concept: str,
    ctx: EvalContext,
    *,
    threshold: float = 0.5,
    batch_size: int = 5,
    fallback: str = "keep",
    max_items: int | None = 200,
) -> Frag
```

这是唯一在查询时调用 LLM 的算子，应放在便宜的召回、图约束和 Top-K 之后。

- 只保留 `label="yes"` 且置信度达到 `threshold` 的明确判断。
- 无法判断时按 `fallback` 处理：`keep`、`drop` 或 `error`。
- 超过 `max_items` 会报错，强制调用者先缩小候选。
- Judge 异常按批次降级，不让整条查询直接失败。
- Judge 的标签、理由和分数会追加到 `Evidence.verdicts`。

```python
shortlist = top(paths.roots(), 20, by="caller")
answer = intent(shortlist, "methods that invalidate stale cache entries", ctx)
```

## 13. `QuerySpec`：planned route 的中间表示

```python
QuerySpec(
    query: str,
    units: tuple[QueryUnit, ...],
    graph: tuple[GraphConstraint, ...] = (),
    concept: str = "",
    kinds: tuple[str, ...] = (),
    target: tuple[str, ...] = (),
    result_relation: ResultRelation | None = None,
    limit: int | None = None,
)
```

### 13.1 普通图约束

```python
GraphConstraint(
    src: str,
    dst: str,
    edge: tuple[str, ...] = ("calls", "contains"),
    hops: tuple[int, int] = (1, 2),
)
```

普通图约束用于给满足关系的候选加权，而不是硬过滤。这样可以避免轻量图漏边时把本来正确的词法候选完全删除。

`calls`/`contains` 保留传统的声明到声明关系，并允许规划器为降低成本从更小的一侧开始。`imports`、`in_file`、`references` 等 typed relation 保留 `src → dst` 端点角色；编译器会在声明节点和文件节点之间做所需的物理投影。

### 13.2 `result_relation`

```python
ResultRelation(
    unit: str,
    result_side: Literal["source", "target"],
    edge: tuple[str, ...],
)
```

`result_relation` 表示查询明确要求返回某条关系的一端。例如“哪些文件引用了 PageRequest”不是简单地把所有候选按 `references` 加权，而是要求：

```text
$result --references--> page_request
```

规划器会用 `ResolveResultRelation` 从锚定 Unit 精确投影到 `$result` 所在端点。若真实边不存在，结果为空，不会用词法相似结果代替关系事实。

### 13.3 `kinds` 与 `target`

二者作用不同：

| 字段 | 语义 | 执行方式 |
|---|---|---|
| `kinds` | 模型推测的候选类型 | 软偏好，只影响 Top-K 排名 |
| `target` | 对外承诺的结果类型 | 硬约束，最终结果必须满足 |

支持的 target 别名包括：

```text
file
type
class / interface / enum / record
annotation / annotation_type
function / method / constructor
field / variable
```

其中 `type` 扩展为所有类型声明；`method`/`function` 包含普通方法和构造器。未知 target 会被忽略，避免错误模型输出成为意外的空结果过滤器。

## 14. planned route 的执行计划

规划器先根据倒排索引 df 估计每个 Unit 的选择率和成本，再生成有序步骤。当前步骤如下：

| Step | 作用 |
|---|---|
| `EvalUnit` | 执行一个 Unit，并与当前候选做并集 |
| `Boost` | 根据经过统计验证的 Unit 间图关系标记加权候选 |
| `ResolveResultRelation` | 沿指定边解析 `$result` 所绑定的端点 |
| `Cohere` | 给最强命中附近的结构邻居加权 |
| `Narrow` | 使用图 boost、kind 偏好和分数取 Top-K |
| `Intent` | 对缩小后的候选执行语义 Judge |
| `ProjectTarget` | 硬过滤声明类型，或沿 `in_file` 投影到文件 |

典型顺序：

```text
EvalUnit（按预估结果数从小到大）
    → Boost（每条普通图约束）
    → ResolveResultRelation（若存在）
    → Cohere
    → Narrow(max 60) → Intent（若启用 Judge）
    → ProjectTarget（若声明了硬 target）
    → Narrow(public limit)
```

重要细节：

- 多个 Unit 使用并集，因为不同语义槽经常落在不同元素上。
- 覆盖近乎整个声明表的 Unit 可能被丢弃，但硬 `result_relation` 的锚定 Unit 不会被丢弃。
- 普通图关系和 `kinds` 只加权，不负责成员资格。
- `Intent` 前最多保留 60 个候选，以控制查询成本。
- `ProjectTarget` 在公共结果 limit 前运行，防止 Top-K 先被非目标类型占满。
- `Plan.run()` 记录每一步的预估数量、实际数量、耗时和跳过原因。

`to_script(plan, spec)` 会生成与 Plan 等价、可检查的 Python QL 脚本。

## 15. 三条搜索 route

### 15.1 `codegen`

LLM 根据查询、项目描述、带 df 的代表性词汇和图规模，直接生成 QL 脚本。脚本通过 AST 门禁后，在受限命名空间中执行。

适合表达分支、循环、逐步放宽条件等自定义控制流；结果中 `script` 保存实际执行脚本。

### 15.2 `planned`

LLM 只负责结构化理解：

- Unit 及其词项；
- Unit 间关系；
- `$result` 绑定的关系端点；
- 软类型偏好；
- 硬 target；
- 完整语义 criterion。

系统负责词项接地、关系统计验证、成本排序、Plan 执行和等价脚本生成。该 route 更确定，也能看到系统为何保留或丢弃某个约束。

### 15.3 `lexical`

不调用查询理解模型。它只保留能被当前倒排索引或 expansion table 接地的查询词，构造一个 `QueryUnit` 执行 `eval_unit`，再做结构邻近加权。

`lexical` 能执行统一 target 后置约束，但无法验证自然语言中的复杂关系语义，因此指定 target 时结果说明中会标记它只是 lexical approximation。

### 15.4 route 后的统一处理

所有 route 和 fallback 完成后，`search()` 还会统一执行：

1. 解析最终 target：显式调用参数优先，其次 route 的结构化结果，最后才是有限的查询文本文件目标启发式。
2. 必要时执行全局 `intent` Judge。
3. 强制执行 target 后置条件。
4. 按证据分数排序并截取公共 `limit`。

默认 target 为空时，会从公共结果中排除文件节点；显式 `target="file"` 时则通过 `in_file` 投影并只返回文件节点。

## 16. `Project.search()`

```python
result = project.search(
    "Find Java files containing references to PageRequest",
    route="planned",
    target="file",
    limit=30,
    judge=False,
    trace=True,
)
```

常用结果字段：

```python
result.route     # 实际执行路线，fallback 后可能与请求不同
result.target    # 最终硬结果类型
result.hits      # 排序后的 Hit
result.script    # 生成脚本或 planned route 的等价脚本
result.notes     # 验证、规划和 fallback 说明
result.elapsed
result.explain() # 汇总以上信息
```

手写算子适合验证算子语义、图方向和证据；`Project.search()` 适合验证完整用户流程。

## 17. 生成脚本的执行边界

生成脚本经过 `codesense/ql/script.py` 的 AST 检查和执行步数限制。它用于阻止错误脚本访问非查询能力或无限运行，但不是面对恶意代码的安全沙箱；不可信攻击者输入仍需进程级隔离。

### 17.1 有效执行命名空间

`codesense/search.py` 当前提供：

```text
ctx
eval_unit, hop, reach, project, degree, only, top, score_of, intent
QueryUnit, Term
LexicalSatisfier, AnnotationSatisfier, ModifierSatisfier
```

`project` 来自调用方提供的受限 namespace，因此虽然静态 `OPERATOR_NAMES` 元组尚未单列它，实际 codegen 脚本可以调用它；`check(..., provided=namespace.keys())` 会将这些提供名称纳入允许集合。

脚本还可使用一组纯 builtin，例如 `set`、`sorted`、`len`、`min`、`max`、`sum`、`tuple`、`list`、`dict`、`range`、`enumerate`、`zip`、`any` 和 `all`。

### 17.2 允许与禁止

允许：

- 变量和中间 fragment；
- `if`、`for`、`while`；
- 普通函数和 lambda；
- 白名单属性和纯 builtin；
- 最多 200 行、默认最多 200,000 个脚本自身执行步骤。

禁止：

- `import`；
- class、async、with、global、nonlocal；
- 未允许的函数和属性；
- 通过 `ctx` 访问 Judge 配置、密钥或其他内部状态；
- 文件、网络、反射和任意代码执行能力。

脚本必须赋值最终变量：

```python
answer = top(found, 20)
```

### 17.3 `intent` 保护

codegen namespace 中的 `intent` 始终是恒等函数，不会因为模型在脚本里生成了该调用就产生隐藏的 LLM 成本。调用 `Project.search(..., judge=True)` 时，系统会在 codegen 脚本完成后统一对缩小后的候选运行真实 Judge；planned route 则把 `Intent` 作为显式 Plan Step 执行。

## 18. 完整示例：返回引用目标类型的文件

```python
from codesense.ql.operators import eval_unit, only, project, top
from codesense.ql.satisfiers import LexicalSatisfier
from codesense.ql.unit import QueryUnit, Term

page_request_unit = QueryUnit(
    "page_request",
    concept="the PageRequest pagination type",
    satisfiers=(
        LexicalSatisfier(
            terms=(Term("page"), Term("request")),
        ),
    ),
)

# 1. 从倒排索引找到目标类型。
page_request = only(
    eval_unit(page_request_unit, ctx),
    kind=("class", "interface", "record"),
)

# 2. 沿 references 反向找到引用者。
referencers = project(
    page_request,
    ctx,
    edge="references",
    direction="backward",
)

# 3. 将声明级引用者投影到所属文件。
files = project(
    referencers,
    ctx,
    edge="in_file",
    kind="file",
    include_self=True,
)

answer = top(files, 20, by="page_request")
```

这里不能直接把 `page_request` 投影到 `in_file`，否则得到的是声明 `PageRequest` 自己所在的文件，而不是引用它的文件。关系方向和投影顺序属于查询语义的一部分。

## 19. 调试顺序

遇到结果不符合预期时，建议逐层检查：

1. **词汇是否存在**：查询词或 expansion 目标是否进入项目词表。
2. **Unit 是否命中**：分别执行每个 `eval_unit`，不要一开始就混合所有概念。
3. **证据是否合理**：查看 `evidence_for(sid).unit_hits` 和 `scores`。
4. **图边是否存在**：确认边类型、方向、置信度和端点层级是声明还是文件。
5. **投影是否正确**：文件结果通常需要 `... → references/imports → in_file` 的组合。
6. **过滤是否过强**：区分硬 `only`/`target` 与软 `kinds`/graph boost。
7. **最后再开 Judge**：先用 `top` 把候选缩小，再运行 `intent`。

```python
for sid, element in found.nodes.items():
    evidence = found.evidence_for(sid)
    print(element.kind, element.name, dict(evidence.scores))
    for hit in evidence.unit_hits:
        print(hit.signal, hit.detail, hit.field, hit.score, hit.span)
```

## 20. 当前能力边界

- 当前内置语言 adapter 是 Java；QL 数据模型本身不绑定语言。
- 调用和引用图是轻量静态近似，不覆盖完整动态分派、反射和外部库。
- 查询阶段没有通用在线 embedding reranker；语义词汇接地主要在构建索引时完成。
- 没有第一等的 regex、代码片段或 CodeQL Satisfier。
- `intent` 能判断候选是否符合完整语义，但它是昂贵的后置过滤器，不替代基础召回和图关系。

## 21. 源码与测试入口

推荐阅读顺序：

1. [`codesense/project.py`](../codesense/project.py)：项目构建、打开和搜索入口。
2. [`codesense/search.py`](../codesense/search.py)：三条 route、fallback、Judge 和 target 后置条件。
3. [`codesense/ql/unit.py`](../codesense/ql/unit.py)：`Term` 与 `QueryUnit`。
4. [`codesense/ql/satisfiers/lexical.py`](../codesense/ql/satisfiers/lexical.py)：三个现役 Satisfier。
5. [`codesense/ql/frag.py`](../codesense/ql/frag.py)：`Frag`、集合代数和证据。
6. [`codesense/ql/operators/`](../codesense/ql/operators/)：公开算子实现。
7. [`codesense/ql/compile/spec.py`](../codesense/ql/compile/spec.py)：`QuerySpec`、关系和 target。
8. [`codesense/ql/compile/planner.py`](../codesense/ql/compile/planner.py)：成本驱动的计划顺序。
9. [`codesense/ql/compile/plan.py`](../codesense/ql/compile/plan.py)：各 Plan Step 的实际语义。
10. [`codesense/ql/script.py`](../codesense/ql/script.py)：生成脚本门禁和执行预算。
11. [`tests/unit/ql/test_project_operator.py`](../tests/unit/ql/test_project_operator.py)：单跳投影语义。
12. [`tests/unit/ql/compile/test_planner.py`](../tests/unit/ql/compile/test_planner.py)：规划步骤、关系端点和 target。
13. [`tests/integration/test_file_target_queries.py`](../tests/integration/test_file_target_queries.py)：文件目标与引用关系的端到端用例。

## 22. API 速查

```python
from codesense import Project
from codesense.ql import Edge, Element, Evidence, Frag, IndexField, Path, UnitHit, Verdict
from codesense.ql.context import EvalContext
from codesense.ql.operators import (
    degree,
    eval_unit,
    hop,
    intent,
    only,
    project,
    reach,
    score_of,
    top,
)
from codesense.ql.satisfiers import (
    AnnotationSatisfier,
    LexicalSatisfier,
    ModifierSatisfier,
)
from codesense.ql.unit import QueryUnit, Term
```

最简记忆方式：

```text
eval_unit 负责“找候选”
Frag 代数负责“组合候选”
hop 负责“证明存在一条路径”
reach 负责“探索多跳邻域”
project 负责“沿一条确定关系换端点”
only / degree / top 负责“缩小和排序”
intent 负责“最后判断完整语义”
target 负责“保证公开结果类型”
```
