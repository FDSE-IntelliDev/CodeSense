# CodeSense QL 算子参考手册

本文档描述 CodeSense **当前现役实现**中的查询语言（QL）数据模型、查询单元、Satisfier、公开算子和 Python API 使用方式。

本文以 `codesense/` 下的实际源码为准。`docs/design/` 中出现但尚未实现的算子、边类型或 Satisfier，不会被当作现成功能；`legacy/` 中的旧 SemCon → SemQL → 三执行器实现也不属于当前 QL API。

## 1. 先回答三个核心问题

### 1.1 `eval_unit` 是执行搜索的吗？

**是。** `eval_unit` 是当前 QL 中执行“基础候选搜索”或“初始召回”的算子。

更准确地说：

> `eval_unit` 执行一个 `QueryUnit` 中配置的全部 `Satisfier`。Satisfier 根据查询词访问词汇接地表和倒排索引，找出匹配的代码符号；`eval_unit` 再合并命中分数与证据，返回一个 `Frag`。

它的边界也很重要：

- 它**不会**自行理解一整句自然语言；
- 它**不会**自动把自然语言拆成多个概念；
- 它**不会**遍历调用图；
- 它**不会**执行 LLM 意图判断；
- 它只负责执行已经构造好的 `QueryUnit`。

完整数据流是：

```text
自然语言 query
    ↓ LLM 编译器或手工编写
一个或多个 QueryUnit
    ↓ eval_unit(unit, ctx)
Satisfier → 词汇接地表 → 倒排索引 → 字段/ICF 评分
    ↓
Frag(nodes + evidence)
    ↓
集合代数 / hop / reach / only / top / degree / intent
    ↓
最终结果
```

因此可以把 `eval_unit` 理解为：

```text
QL 的基础检索算子
```

而不是：

```text
完整自然语言搜索系统
```

### 1.2 当前有哪些 Unit？

当前只有一个通用 Unit 类型：

```python
QueryUnit
```

系统没有预定义如下固定类型：

```text
BufferUnit
RelationUnit
IntentUnit
AnnotationUnit
```

`QueryUnit` 是一个通用的“语义槽位”。一条查询包含哪些概念，就动态构造哪些 Unit。

例如：

```text
查找使用 Redis 存储 token 的方法
```

可以拆成两个 Unit：

```text
token：token 生命周期或认证凭据
redis：Redis 或缓存存储
```

它们都是 `QueryUnit` 的实例，而不是两个不同的 Python 类。

### 1.3 当前有哪些 Satisfier？

当前正式实现了三个 Satisfier：

| Satisfier | 注册名 | 主要匹配内容 | 默认权重 |
|---|---|---|---:|
| `LexicalSatisfier` | `lexical` | 名称、签名、容器、文档等 posting 字段 | 0.5 |
| `AnnotationSatisfier` | `annotation` | 注解名称和注解参数 | 0.9 |
| `ModifierSatisfier` | `modifier` | `static`、`synchronized` 等修饰符 | 0.6 |

当前没有实现 `SemanticSatisfier`、`EmbeddingSatisfier`、`RegexSatisfier`、`CodeQLSatisfier` 或 `GraphSatisfier`。

## 2. QL 的核心对象及关系

QL 的主要对象可以分成五层：

```text
Term
  一个查询词及其来源、权重、推导理由

Satisfier
  一种“这个 Unit 如何被代码满足”的检索信号

QueryUnit
  一个语义概念，组合一个或多个 Satisfier

Frag
  算子的统一输入输出：节点、边、路径和证据

EvalContext
  算子访问索引、图、接地表和 Judge 的运行时上下文
```

它们之间的关系是：

```text
QueryUnit
├── name
├── concept
├── combine
└── satisfiers
    ├── LexicalSatisfier
    │   └── Term...
    ├── AnnotationSatisfier
    │   └── Term / annotation name...
    └── ModifierSatisfier
        └── modifier name...

eval_unit(QueryUnit, EvalContext)
    ↓
Frag
```

## 3. `Term`：一个查询词

源码：[`codesense/ql/unit.py`](../codesense/ql/unit.py)

定义：

```python
@dataclass(frozen=True, slots=True)
class Term:
    value: str
    source: str = "literal"
    weight: float = 1.0
    reason: str = ""
```

### 3.1 字段含义

| 字段 | 含义 |
|---|---|
| `value` | 实际查询词；不能为空 |
| `source` | 这个词来自哪里 |
| `weight` | 该词在本 Unit 中的相对权重 |
| `reason` | 该词为什么能代表当前概念 |

### 3.2 `source` 的语义

代码和设计约定了三种常见来源：

| `source` | 含义 | 可信程度 |
|---|---|---|
| `literal` | 用户查询中直接出现 | 最高 |
| `synonym` | 与原概念语义近似或可互换 | 中等 |
| `derived` | 关联推导词，不一定是严格同义词 | 较低 |

示例：

```python
terms = (
    Term("buffer", source="literal", weight=1.0),
    Term("buf", source="synonym", weight=0.9, reason="common abbreviation"),
    Term("watermark", source="derived", weight=0.6, reason="backpressure mechanism"),
)
```

`source` 本身不会直接触发不同的执行分支，但它会进入证据，并且编译器可以通过不同 `weight` 控制推导词的影响。

## 4. `QueryUnit`：动态语义单元

源码：[`codesense/ql/unit.py`](../codesense/ql/unit.py)

定义：

```python
@dataclass(frozen=True, slots=True)
class QueryUnit:
    name: str
    concept: str = ""
    satisfiers: tuple[object, ...] = ()
    combine: str = "noisy_or"
```

### 4.1 字段含义

#### `name`

Unit 的稳定标识符。

它用于：

- 在证据中标记一次命中属于哪个 Unit；
- `top(frag, n, by="unit_name")` 按指定 Unit 排序；
- `score_of(frag, symbol_id, by="unit_name")` 读取指定 Unit 分数；
- 生成脚本时引用不同语义单元。

建议使用简短、稳定的英文标识符：

```python
name="buffer"
name="disk_io"
name="token_management"
```

#### `concept`

完整的自然语言语义描述。

```python
concept="code that manages the complete token lifecycle"
```

`concept` 本身不参与倒排检索。当前主要用途是：

- 供人阅读和调试；
- 传给 `intent` 作为 LLM 判断目标；
- 保留 Unit 的完整语义，避免被关键词完全取代。

典型写法：

```python
answer = intent(shortlist, unit.concept, ctx)
```

#### `satisfiers`

一个或多个 Satisfier 实例，描述“什么信号可以满足这个 Unit”。

```python
satisfiers=(
    LexicalSatisfier(...),
    AnnotationSatisfier(...),
    ModifierSatisfier(...),
)
```

#### `combine`

描述多个 Satisfier 得分如何合并。

当前注册了三种策略：

| 策略 | 算法 | 特点 |
|---|---|---|
| `max` | 取最大值 | 一个强信号即可，偏召回 |
| `sum` | 直接相加 | 弱信号可以积累，但容易被噪声膨胀 |
| `noisy_or` | `1 - ∏(1 - score_i)` | 弱信号能加强结果且总分有界，默认策略 |

实现位置：[`codesense/ql/combine.py`](../codesense/ql/combine.py)

### 4.2 Unit 没有固定清单

Unit 是按查询动态生成的。常见 Unit 名称可能包括：

```text
authentication
token
redis
cache
disk_io
performance
backpressure
transaction
http_entry
test_code
```

这些只是实例名称，不是系统注册的 Unit 类型。

一条查询可以只有一个 Unit：

```python
buffer_unit = QueryUnit(...)
buffer = eval_unit(buffer_unit, ctx)
```

也可以拆成多个 Unit，再用集合或图算子组合：

```python
token = eval_unit(token_unit, ctx)
redis = eval_unit(redis_unit, ctx)

same_symbol = token & redis
connected = hop(token, redis, ctx, edge="calls")
```

### 4.3 一个 Unit 使用多个信号

示例：“带事务注解的同步写方法”。

```python
from codesense.ql.satisfiers import (
    AnnotationSatisfier,
    LexicalSatisfier,
    ModifierSatisfier,
)
from codesense.ql.unit import QueryUnit, Term

unit = QueryUnit(
    name="transactional_write",
    concept="a synchronized method that writes data transactionally",
    satisfiers=(
        LexicalSatisfier(
            terms=(Term("write"), Term("save")),
        ),
        AnnotationSatisfier(
            names=("@Transactional",),
        ),
        ModifierSatisfier(
            modifiers=("synchronized",),
        ),
    ),
    combine="noisy_or",
)

found = eval_unit(unit, ctx)
```

执行过程是：

```text
LexicalSatisfier ───────┐
AnnotationSatisfier ────┼─→ eval_unit 合并分数和证据 → Frag
ModifierSatisfier ──────┘
```

## 5. `EvalContext`：算子的运行时依赖

源码：[`codesense/ql/context.py`](../codesense/ql/context.py)

定义：

```python
@dataclass(frozen=True, slots=True)
class EvalContext:
    symbols: SymbolStore
    postings: PostingIndex
    expansion: ExpansionTable
    edges: EdgeStore
    field_weights: FieldWeights = DEFAULT_FIELD_WEIGHTS
    judge: Judge = NullJudge()
    icf_floor: float = 0.34
    min_hit_score: float = 1e-6
```

各字段职责：

| 字段 | 职责 |
|---|---|
| `symbols` | 根据 `symbol_id` 读取代码元素 |
| `postings` | 查询 `term → posting` 倒排索引 |
| `expansion` | 将通用概念映射到项目实际拼写 |
| `edges` | 查询 `calls`、`contains` 等图边 |
| `field_weights` | 控制不同字段的匹配强度 |
| `judge` | 为 `intent` 提供 Judge 实现 |
| `icf_floor` | 过滤过于通用的扩展词 |
| `min_hit_score` | 丢弃极弱的单条证据 |

通常不需要手动构造 `EvalContext`，直接从 `Project` 获取：

```python
from codesense import Project

project = Project.open("/path/to/project/.codesense")
ctx = project.context
```

`Project.context` 是惰性创建并缓存的，因为大型项目的上下文可能占用较多内存。

## 6. Satisfier 的通用执行原理

抽象基类：[`codesense/ql/satisfiers/base.py`](../codesense/ql/satisfiers/base.py)

```python
class Satisfier(ABC):
    signal: ClassVar[str]

    @abstractmethod
    def hits(self, unit: str, ctx: EvalContext) -> HitsBySymbol:
        ...
```

Satisfier 的输出是：

```text
symbol_id → 该 Satisfier 产生的全部 UnitHit
```

三个现有 Satisfier 共用 `collect_term_hits()`，执行过程如下：

```text
Term
  ↓ 保留原词，并查询 expansion table
一个或多个项目实际拼写
  ↓ postings.lookup(surface)
Posting(symbol_id, field, tf)
  ↓ 字段限制、ICF 门槛和最低分过滤
UnitHit(unit, signal, detail, field, score)
```

单条证据的核心评分公式是：

```text
Satisfier weight
× Term weight
× expansion score
× field weight
× ICF ratio
```

即：

```text
score =
    satisfier_weight
    * term.weight
    * expansion.score
    * field_weight
    * icf_ratio
```

这意味着：

- 同一个词命中名称通常比命中文档分数高；
- 推导词可以用较低 `Term.weight` 降权；
- `buffer → buf` 之类接地会乘以 expansion score；
- 出现在大量符号中的通用词会因 ICF 较低而降权；
- 用户直接输入的原词不会被 `icf_floor` 直接删除；
- 只有扩展词受 `icf_floor` 门槛约束。

### 6.1 字段权重

实现：[`codesense/ql/fields.py`](../codesense/ql/fields.py)

默认权重：

| 字段 | 权重 |
|---|---:|
| `name` | 1.0 |
| `annotation` | 0.9 |
| `annotation_arg` | 0.7 |
| `modifier` | 0.6 |
| `signature` | 0.6 |
| `container` | 0.5 |
| `doc` | 0.3 |

## 7. `LexicalSatisfier`

源码：[`codesense/ql/satisfiers/lexical.py`](../codesense/ql/satisfiers/lexical.py)

签名：

```python
LexicalSatisfier(
    terms: tuple[Term, ...],
    weight: float = 0.5,
    fields: tuple[IndexField, ...] | None = None,
)
```

### 7.1 功能

根据查询词匹配倒排索引中的代码字段，是当前最通用的基础召回方式。

`fields=None` 表示不额外限制 posting 字段。实际索引字段包括：

```text
name
signature
container
doc
annotation
annotation_arg
modifier
```

默认权重只有 0.5，因为“名称中出现某个词”是相对较弱的语义证据。

### 7.2 基本用法

```python
from codesense.ql.satisfiers import LexicalSatisfier
from codesense.ql.unit import QueryUnit, Term

unit = QueryUnit(
    name="buffer",
    concept="buffer allocation and lifecycle",
    satisfiers=(
        LexicalSatisfier(
            terms=(
                Term("buffer"),
                Term("allocator"),
                Term("alloc"),
            ),
        ),
    ),
)

found = eval_unit(unit, ctx)
```

### 7.3 限制字段

只搜索名称：

```python
from codesense.ql import IndexField

names_only = LexicalSatisfier(
    terms=(Term("buffer"),),
    fields=(IndexField.NAME,),
)
```

搜索名称和签名：

```python
name_or_signature = LexicalSatisfier(
    terms=(Term("buffer"),),
    fields=(IndexField.NAME, IndexField.SIGNATURE),
)
```

### 7.4 词汇接地示例

若查询使用：

```text
buffer
```

项目实际使用：

```text
buf
```

接地表可以提供：

```text
buffer → buf
```

随后 `LexicalSatisfier` 会查询 `buf` 的 posting，并产生类似证据：

```text
buf←buffer(prefix)@name
```

## 8. `AnnotationSatisfier`

源码：[`codesense/ql/satisfiers/lexical.py`](../codesense/ql/satisfiers/lexical.py)

签名：

```python
AnnotationSatisfier(
    units: tuple[Term, ...] = (),
    names: tuple[str, ...] = (),
    weight: float = 0.9,
)
```

### 8.1 功能

专门查询两个字段：

```text
annotation
annotation_arg
```

Java 和 Spring 项目中的注解往往比普通词法匹配更能直接表达语义，因此默认权重为 0.9。

### 8.2 按完整注解名称查询

```python
unit = QueryUnit(
    name="transactional",
    concept="transactional code",
    satisfiers=(
        AnnotationSatisfier(
            names=("@Transactional",),
        ),
    ),
)
```

### 8.3 按注解的分词单元查询

```python
unit = QueryUnit(
    name="cache",
    concept="cache-related declarations",
    satisfiers=(
        AnnotationSatisfier(
            units=(Term("cache"),),
        ),
    ),
)
```

这可以匹配：

```java
@Cacheable
@CacheEvict
@AppCache
```

因为索引阶段会对注解名称进行分词，不要求使用字面正则表达式。

### 8.4 元注解和框架扩展

注解名称会经过 expansion table，因此可以利用框架事实关系。

例如项目可以把：

```text
@RequestMapping
```

扩展到：

```text
@GetMapping
@PostMapping
@PutMapping
@DeleteMapping
@PatchMapping
```

这种关系来自框架定义，和普通相似度估计不同，可以具有 1.0 的接地分数。

### 8.5 注解参数

注解参数也进入索引，例如：

```java
@GetMapping("/users")
@PreAuthorize("@ss.hasPerm('sys:user:query')")
```

因此查询 `users` 或权限字符串的分词结果可以在 `annotation_arg` 字段命中。

## 9. `ModifierSatisfier`

源码：[`codesense/ql/satisfiers/lexical.py`](../codesense/ql/satisfiers/lexical.py)

签名：

```python
ModifierSatisfier(
    modifiers: tuple[str, ...] = (),
    weight: float = 0.6,
)
```

### 9.1 功能

专门匹配语言修饰符，例如：

```text
public
private
protected
static
abstract
final
synchronized
volatile
native
```

修饰符是结构事实，不需要同义词扩展。默认权重 0.6，高于普通 lexical 的 0.5，低于语义更具体的 annotation 0.9。

### 9.2 用法

```python
unit = QueryUnit(
    name="synchronized_write",
    concept="synchronized write methods",
    satisfiers=(
        LexicalSatisfier(
            terms=(Term("write"),),
        ),
        ModifierSatisfier(
            modifiers=("synchronized",),
        ),
    ),
)

found = eval_unit(unit, ctx)
```

`public` 等极常见修饰符通常因 ICF 很低而只产生很弱的分数；`native`、`volatile` 等较少见修饰符更具区分度。

## 10. 当前公开算子总览

统一导出位置：[`codesense/ql/operators/__init__.py`](../codesense/ql/operators/__init__.py)

```python
from codesense.ql.operators import (
    degree,
    eval_unit,
    hop,
    intent,
    only,
    reach,
    score_of,
    top,
)
```

| 算子 | 输入 | 输出 | 主要用途 |
|---|---|---|---|
| `eval_unit` | `QueryUnit + EvalContext` | `Frag` | 执行基础索引召回 |
| `hop` | 两个 `Frag + EvalContext` | `Frag` | 查找两组节点之间的受约束路径 |
| `reach` | 一个 `Frag + EvalContext` | `Frag` | 探索图邻域 |
| `only` | `Frag` | `Frag` | 按元素属性过滤 |
| `top` | `Frag` | `Frag` | 按证据分数取前 N 个 |
| `score_of` | `Frag + symbol_id` | `float` | 读取一个节点的分数 |
| `degree` | `Frag + EvalContext` | `Frag` | 按完整项目图的入度/出度过滤 |
| `intent` | `Frag + concept + EvalContext` | `Frag` | 使用 LLM 判断完整语义意图 |

严格来说，`score_of` 返回标量，是辅助查询函数；其他公开函数都返回 `Frag`。

## 11. `eval_unit`

源码：[`codesense/ql/operators/unit.py`](../codesense/ql/operators/unit.py)

签名：

```python
eval_unit(unit: QueryUnit, ctx: EvalContext) -> Frag
```

### 11.1 实现原理

`eval_unit` 的核心步骤：

1. 遍历 `unit.satisfiers`；
2. 校验每个对象确实是 `Satisfier`；
3. 调用 `satisfier.hits(unit.name, ctx)`；
4. 按 `symbol_id` 收集所有 `UnitHit`；
5. 在同一个 Satisfier 内，使用 `noisy_or` 合并多条 Term 命中；
6. 在多个 Satisfier 之间，使用 `unit.combine` 合并信号分数；
7. 从 `ctx.symbols` 批量加载对应 `Element`；
8. 为每个节点附加详细证据和一个 `combined` 汇总证据；
9. 返回只含节点和证据、暂时不含图边的 `Frag`。

两层合并需要区分：

```text
同一个 Satisfier 内的多个 Term
    固定使用 noisy_or

不同 Satisfier 之间
    使用 QueryUnit.combine
```

### 11.2 最小示例

```python
unit = QueryUnit(
    name="token",
    satisfiers=(
        LexicalSatisfier(
            terms=(Term("token"),),
        ),
    ),
)

token = eval_unit(unit, ctx)
```

### 11.3 查看结果

```python
for element in token:
    evidence = token.evidence_for(element.symbol_id)
    print(element.name, dict(evidence.scores))
```

### 11.4 返回值特点

`eval_unit` 返回的 `Frag`：

- `nodes`：命中的代码元素；
- `evidence`：每个元素为什么命中；
- `edges`：通常为空；
- `witnesses`：通常为空。

图结构随后由 `hop`、`reach` 等算子引入。

## 12. `hop`

源码：[`codesense/ql/operators/hop.py`](../codesense/ql/operators/hop.py)

签名：

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

### 12.1 功能

寻找 `src` 与 `dst` 两个 fragment 之间满足约束的图路径。

返回结果保留：

- 所有路径上的节点；
- 所有路径上的边；
- 起点和终点原有证据；
- `Path` 类型的路径见证 `witnesses`。

### 12.2 实现原理

`hop` 不是从每个起点盲目枚举全部路径，而是分两步：

1. 从目标集合反向执行 BFS，计算各节点到目标的最短距离；
2. 从起点执行带剪枝的 DFS，只扩展仍可能在最大跳数内到达目标的分支。

主要剪枝机制：

- 超过 `hops` 上界停止；
- 利用反向最短距离剪掉不可能到达目标的分支；
- 路径内不重复节点，避免环；
- `avoid` 中的节点不进入路径；
- 超过 `max_degree` 的中间 hub 不展开；
- 达到 `max_paths` 后停止并记录 warning；
- 过滤低于 `min_confidence` 的边。

### 12.3 基本用法

```python
linked = hop(
    token,
    redis,
    ctx,
    edge="calls",
    direction="forward",
    hops=(1, 3),
)
```

语义是：在 `calls` 图上寻找 token 相关代码到 redis 相关代码之间长度为 1—3 的调用路径。

### 12.4 `hops` 是闭区间

```python
hops=(1, 3)
```

包括 1、2、3 跳。

整数表示精确跳数：

```python
hops=2
```

等价于：

```python
hops=(2, 2)
```

### 12.5 方向

支持：

| 方向 | 含义 |
|---|---|
| `forward` | 沿边的 source → target |
| `backward` | 逆着边查询 |
| `any` | 两个方向都可走 |

例如查找调用 token 代码的上游路径：

```python
paths = hop(
    token,
    controllers,
    ctx,
    edge="calls",
    direction="backward",
    hops=(1, 4),
)
```

### 12.6 多种边

当前索引主要产生：

```text
calls
contains
```

可以同时查询：

```python
linked = hop(
    token,
    redis,
    ctx,
    edge=("calls", "contains"),
    hops=(1, 3),
)
```

设计文档出现的 `flows_to`、`co_change` 等边目前没有在现役索引中构建，不能因为设计示例存在就认为可用。

### 12.7 `via` 与 `avoid`

要求完整路径至少经过 `service_layer` 中的一个节点：

```python
linked = hop(
    source,
    target,
    ctx,
    via=service_layer,
)
```

排除测试代码：

```python
linked = hop(
    source,
    target,
    ctx,
    avoid=test_code,
)
```

### 12.8 边置信度

```python
linked = hop(
    source,
    target,
    ctx,
    edge="calls",
    min_confidence=0.8,
)
```

当前 `contains` 边通常是置信度 1.0；`calls` 边根据 typed receiver 或 name match 使用不同置信度。

### 12.9 查看路径见证

```python
for path in linked.witnesses:
    print("nodes:", path.nodes)
    for edge in path.edges:
        print(
            edge.source_id,
            edge.kind,
            edge.target_id,
            edge.confidence,
            edge.provenance,
        )
```

## 13. `reach`

源码：[`codesense/ql/operators/hop.py`](../codesense/ql/operators/hop.py)

签名：

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

### 13.1 功能

从一个 fragment 出发探索图邻域，不要求提供目标集合。

区别：

```text
hop(src, dst)
    验证 src 和 dst 之间有哪些满足条件的路径

reach(src)
    探索从 src 可以到达哪些节点
```

### 13.2 实现原理

`reach` 使用 BFS 计算从起点集合到其他节点的最短距离，再保留距离落在 `hops` 闭区间中的节点。

返回结果只包含节点，不保存完整路径和 `witnesses`。如果必须知道“为什么这两个节点连接”，应使用 `hop`。

### 13.3 查询类成员

```python
members = reach(
    class_frag,
    ctx,
    edge="contains",
    direction="forward",
    hops=1,
)
```

### 13.4 查询上游调用者

```python
callers = reach(
    target_methods,
    ctx,
    edge="calls",
    direction="backward",
    hops=(1, 3),
)
```

### 13.5 查询下游调用

```python
callees = reach(
    entry_methods,
    ctx,
    edge="calls",
    direction="forward",
    hops=(1, 3),
)
```

## 14. `only`

源码：[`codesense/ql/operators/select.py`](../codesense/ql/operators/select.py)

签名：

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

### 14.1 功能

根据代码元素属性筛选节点，并保留仍然有效的边、路径和证据。

多个条件使用 AND：

```python
result = only(
    frag,
    kind="method",
    file="src/main/java/demo/AuthService.java",
    language="java",
)
```

### 14.2 实现原理

`only` 遍历当前 fragment 的节点，判断每个 `Element` 是否满足条件，然后调用 `frag.induced()` 构造诱导子图。

诱导子图会自动：

- 保留选中的节点；
- 保留两端都还存在的边；
- 保留对应证据；
- 删除悬空边和不再完整的路径见证。

### 14.3 按类型筛选

```python
methods = only(frag, kind="method")
```

多个类型：

```python
callables = only(
    frag,
    kind=("method", "constructor"),
)
```

### 14.4 按文件筛选

`file` 当前是精确值匹配，不是 glob 或正则：

```python
result = only(
    frag,
    file="src/main/java/demo/AuthService.java",
)
```

多个文件：

```python
result = only(
    frag,
    file=("A.java", "B.java"),
)
```

### 14.5 自定义 Python 谓词

普通 Python API 可以使用 `where`：

```python
getters = only(
    frag,
    where=lambda element: element.name.startswith("get"),
)
```

需要注意，生成脚本受 AST 白名单限制，复杂 `where` 表达式未必适合作为模型生成目标。优先使用 `kind`、`file`、`language` 等具名参数。

### 14.6 `None` 与空序列不同

```python
only(frag, kind=None)
```

表示不启用 kind 条件。

```python
only(frag, kind=())
```

表示明确允许零种 kind，因此结果为空。

## 15. `top`

源码：[`codesense/ql/operators/select.py`](../codesense/ql/operators/select.py)

签名：

```python
top(frag: Frag, n: int, *, by: str | None = None) -> Frag
```

### 15.1 功能

按证据分数选择前 N 个节点。

### 15.2 实现原理

`top` 调用 `score_of` 取得每个节点的分数，然后按如下排序键排序：

```python
(-score, symbol_id)
```

因此：

- 分数高的优先；
- 分数相同时，`symbol_id` 小的优先；
- 排序结果可复现。

随后使用 `frag.induced()` 返回前 N 个节点对应的诱导子图，证据不会丢失。

### 15.3 按一个 Unit 排序

```python
best_token = top(token, 20, by="token")
```

`by` 必须与 `QueryUnit.name` 对应。

### 15.4 按所有 Unit 总分排序

```python
best = top(frag, 20)
```

`by=None` 时使用该节点全部 Unit 分数之和。

### 15.5 边界行为

```python
top(frag, 0)   # 返回空 Frag
top(frag, 999) # N 超过节点数时返回全部节点
top(frag, -1)  # 抛出 ValueError
```

## 16. `score_of`

源码：[`codesense/ql/operators/select.py`](../codesense/ql/operators/select.py)

签名：

```python
score_of(
    frag: Frag,
    symbol_id: int,
    by: str | None = None,
) -> float
```

### 16.1 功能

读取一个节点的证据分数。

指定 Unit：

```python
token_score = score_of(frag, symbol_id, by="token")
```

读取全部 Unit 分数之和：

```python
total_score = score_of(frag, symbol_id)
```

### 16.2 实现原理

它读取：

```python
frag.evidence_for(symbol_id).scores
```

若指定 `by`，返回对应 Unit 分数；否则求全部 Unit 分数之和。不存在的 Unit 或没有证据的节点返回 0.0。

### 16.3 调试用法

```python
for symbol_id, element in frag.nodes.items():
    print(
        element.name,
        score_of(frag, symbol_id),
        score_of(frag, symbol_id, by="token"),
    )
```

## 17. `degree`

源码：[`codesense/ql/operators/select.py`](../codesense/ql/operators/select.py)

签名：

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

### 17.1 功能

按照节点在代码图中的入度和出度筛选候选。

### 17.2 关键语义

度数基于**完整项目图**计算，而不是只计算当前 fragment 内部的边。

这是因为：

```text
“这个函数被多少位置调用”
```

描述的是它在整个代码库中的地位，而不是它在当前候选集合中的地位。

### 17.3 实现原理

对 fragment 中的每个 `symbol_id`：

1. 从 `ctx.edges` 查询指定种类的入边；
2. 从 `ctx.edges` 查询指定种类的出边；
3. 检查是否落在 `min_in/max_in/min_out/max_out` 的闭区间；
4. 使用 `frag.induced()` 返回满足条件的节点。

### 17.4 近似入口节点

```python
entries = degree(
    methods,
    ctx,
    edge="calls",
    max_in=0,
)
```

表示当前索引图中没有其他节点调用它。

这只是基于当前轻量调用图的近似，不等同于语言语义上的正式入口点分析。

### 17.5 被广泛调用的方法

```python
popular = degree(
    methods,
    ctx,
    edge="calls",
    min_in=20,
)
```

### 17.6 调用其他方法的入口候选

```python
entries = degree(
    methods,
    ctx,
    edge="calls",
    max_in=0,
    min_out=1,
)
```

### 17.7 按多种边或全部边统计

```python
mixed = degree(
    frag,
    ctx,
    edge=("calls", "contains"),
    min_in=1,
)
```

`edge=None` 表示不限制边类型：

```python
all_edges = degree(frag, ctx, edge=None, min_in=1)
```

### 17.8 不传度数条件

```python
same = degree(frag, ctx)
```

没有任何 `min/max` 条件时，返回所有节点。

## 18. `intent`

源码：[`codesense/ql/operators/intent.py`](../codesense/ql/operators/intent.py)

签名：

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

### 18.1 功能

使用 Judge 判断候选代码是否真正符合完整自然语言语义。

它是当前公开算子中唯一会在查询阶段调用 LLM 的算子。

### 18.2 正确执行位置

`intent` 是最昂贵的算子，应放在廉价约束之后：

```python
candidates = eval_unit(unit, ctx)
candidates = only(candidates, kind="method")
candidates = top(candidates, 20, by=unit.name)

answer = intent(
    candidates,
    unit.concept,
    ctx,
    max_items=20,
)
```

不应把全库或上千候选直接交给 `intent`。

### 18.3 实现原理

1. 检查 `fallback` 是否有效；
2. 检查候选数是否超过 `max_items`；
3. 将每个 `Element` 转成 `JudgeItem`；
4. 按 `batch_size` 分批调用 `ctx.judge.judge(concept, batch)`；
5. 丢弃模型返回但不属于当前候选集的 `symbol_id`；
6. 只保留 `label == "yes"` 且 `score >= threshold` 的节点；
7. 对无法判断的候选执行 fallback；
8. 将 Verdict 和 reason 追加到节点证据；
9. 删除因为节点被过滤而产生的悬空边和不完整路径。

### 18.4 阈值

```python
answer = intent(
    shortlist,
    "manages the complete token lifecycle",
    ctx,
    threshold=0.7,
)
```

只有：

```text
label == yes
且 confidence >= 0.7
```

才通过。

`unsure` 不会被当作 `yes`。

### 18.5 批处理

```python
answer = intent(
    shortlist,
    concept,
    ctx,
    batch_size=5,
)
```

`batch_size` 必须大于 0。

### 18.6 `max_items`

默认最多接收 200 个候选：

```python
max_items=200
```

超过时会抛出 `ValueError`，提示先用 `hop`、`only` 或 `top` 缩小候选。

如果明确接受成本，可以关闭上限：

```python
max_items=None
```

通常不建议这么做。

### 18.7 fallback

| 值 | 无法判断时的行为 | 取向 |
|---|---|---|
| `keep` | 保留候选，并写入 fallback 证据 | 偏召回 |
| `drop` | 删除无法判断的候选 | 偏精度 |
| `error` | 直接抛出异常 | 严格模式 |

示例：

```python
answer = intent(
    shortlist,
    concept,
    ctx,
    fallback="drop",
)
```

### 18.8 没有 LLM 时

若 `Project` 没有注入 LLM：

```python
project = Project.open(index_dir)
```

则 `ctx.judge` 是 `NullJudge`。它不会真正判断候选。默认 `fallback="keep"` 时，候选会保留，同时证据中写入 judging unavailable。

要真正执行 LLM Judge：

```python
from codesense import Project
from codesense.llm import LlmConfig

llm = LlmConfig.load(
    base_url="https://api.openai.com/v1",
    model="gpt-4o-mini",
)

project = Project.open(index_dir, llm=llm)
ctx = project.context
```

## 19. `Frag`：所有算子共同处理的数据

源码：[`codesense/ql/frag.py`](../codesense/ql/frag.py)

核心结构：

```python
@dataclass(frozen=True, slots=True, eq=False)
class Frag:
    nodes: Mapping[int, Element]
    edges: Mapping[EdgeKey, Edge]
    evidence: Mapping[int, Evidence]
    witnesses: tuple[Path, ...]
```

### 19.1 字段

| 字段 | 内容 |
|---|---|
| `nodes` | `symbol_id → Element` |
| `edges` | `(source, target, kind) → Edge` |
| `evidence` | `symbol_id → Evidence` |
| `witnesses` | `hop` 返回的路径见证 |

`Frag` 创建后内部 mapping 会冻结，算子不会原地修改 fragment，而是返回新 fragment。

## 20. `Frag` 集合代数

### 20.1 并集 `|`

```python
combined = token | redis
```

功能：

- 合并两边节点；
- 合并边；
- 同一节点的证据合并；
- 路径见证去重后合并。

适合表达 OR。

### 20.2 交集 `&`

```python
both = token & redis
```

功能：

- 只保留两边共同存在的 `symbol_id`；
- 保留两端都在结果中的边；
- 合并两个 Unit 的证据。

适合表达“同一个代码元素同时满足两个概念”。

交集和 `hop` 不等价：

```text
token & redis
    同一个 symbol 同时满足 token 和 redis

hop(token, redis)
    token symbol 与 redis symbol 之间存在图路径
```

### 20.3 差集 `-`

```python
production = all_matches - test_matches
```

功能：

- 删除右侧 fragment 中出现的 `symbol_id`；
- 删除相关悬空边；
- 保留左侧剩余节点的证据。

适合表达 NOT 或 exclude。

## 21. `Frag` 投影和辅助方法

### 21.1 `induced`

```python
subset = frag.induced(symbol_ids)
```

构造给定节点集合对应的诱导子图。只保留两端都存在的边和完整路径。

### 21.2 `roots`

```python
starts = linked.roots()
```

返回当前 fragment 内入度为 0 的节点。它根据 `frag.edges` 判断，而不是根据整个项目图判断。

因此：

```text
linked.roots()
```

和：

```text
degree(linked, ctx, max_in=0)
```

语义不同：前者看 fragment 内部，后者看完整项目图。

### 21.3 `leaves`

```python
ends = linked.leaves()
```

返回当前 fragment 内出度为 0 的节点。

### 21.4 `only_nodes`

```python
nodes_only = linked.only_nodes()
```

保留节点和证据，删除边和路径见证，退化回普通节点集合语义。

### 21.5 `evidence_for`

```python
evidence = frag.evidence_for(symbol_id)
```

不存在证据时返回空 `Evidence`，不会抛出 `KeyError`。

## 22. 证据结构

### 22.1 `UnitHit`

```python
UnitHit(
    unit="token",
    signal="lexical",
    detail="token",
    field="name",
    score=0.62,
)
```

记录：

- 哪个 Unit；
- 哪种信号；
- 匹配了什么；
- 命中哪个字段；
- 本条证据分数。

### 22.2 `Verdict`

```python
Verdict(
    source="llm",
    label="yes",
    reason="the method issues and validates tokens",
    score=0.91,
)
```

主要由 `intent` 产生。

### 22.3 查看完整证据

```python
for symbol_id, element in frag.nodes.items():
    evidence = frag.evidence_for(symbol_id)

    print(element.name)
    print("scores:", dict(evidence.scores))

    for hit in evidence.unit_hits:
        print(
            "hit",
            hit.unit,
            hit.signal,
            hit.detail,
            hit.field,
            hit.score,
        )

    for verdict in evidence.verdicts:
        print(
            "verdict",
            verdict.source,
            verdict.label,
            verdict.reason,
            verdict.score,
        )
```

证据遵循追加而不是覆盖的原则，因此 fragment 经过多个算子后仍然能够说明原始召回原因和最终 Judge 理由。

## 23. 完整示例一：执行一个 Unit 的基础搜索

前提：目标 Java 项目已经通过 `codesense init` 建立 `.codesense` 索引。

```python
from codesense import Project
from codesense.ql.operators import eval_unit, only, top
from codesense.ql.satisfiers import LexicalSatisfier
from codesense.ql.unit import QueryUnit, Term


project = Project.open("/path/to/java-project/.codesense")
ctx = project.context

buffer_unit = QueryUnit(
    name="buffer",
    concept="code responsible for buffer allocation and recycling",
    satisfiers=(
        LexicalSatisfier(
            terms=(
                Term("buffer", source="literal", weight=1.0),
                Term("allocator", source="derived", weight=0.8),
                Term("recycle", source="derived", weight=0.7),
            ),
            weight=1.0,
        ),
    ),
)

# eval_unit 在这里执行倒排索引搜索。
found = eval_unit(buffer_unit, ctx)

# 确定性过滤和排序。
methods = only(found, kind=("method", "constructor"))
answer = top(methods, 20, by="buffer")

for element in answer:
    evidence = answer.evidence_for(element.symbol_id)
    print(
        element.name,
        element.file,
        element.span,
        dict(evidence.scores),
    )
```

## 24. 完整示例二：多 Unit、图路径和 Intent Judge

查询目标：

```text
查找使用 Redis 存储 token，并真正负责 token 生命周期的方法
```

```python
from codesense import Project
from codesense.llm import LlmConfig
from codesense.ql.operators import eval_unit, hop, intent, only, top
from codesense.ql.satisfiers import LexicalSatisfier
from codesense.ql.unit import QueryUnit, Term


def lexical_unit(name: str, concept: str, *terms: str) -> QueryUnit:
    return QueryUnit(
        name=name,
        concept=concept,
        satisfiers=(
            LexicalSatisfier(
                terms=tuple(Term(term) for term in terms),
                weight=1.0,
            ),
        ),
    )


llm = LlmConfig.load(
    base_url="https://api.openai.com/v1",
    model="gpt-4o-mini",
)

project = Project.open(
    "/path/to/java-project/.codesense",
    llm=llm,
)
ctx = project.context

token_unit = lexical_unit(
    "token",
    "code responsible for token lifecycle management",
    "token",
    "credential",
)

redis_unit = lexical_unit(
    "redis",
    "code interacting with Redis-backed storage",
    "redis",
    "cache",
)

# 两个 Unit 分别执行基础搜索。
token = eval_unit(token_unit, ctx)
redis = eval_unit(redis_unit, ctx)

# 只让方法或构造器作为 token 侧候选。
token_methods = only(
    token,
    kind=("method", "constructor"),
)

# 寻找 token 相关方法到 redis 相关代码之间的路径。
linked = hop(
    token_methods,
    redis,
    ctx,
    edge=("calls", "contains"),
    direction="forward",
    hops=(1, 3),
    min_confidence=0.3,
)

# 取路径起点，再按 token Unit 的证据分数缩小候选。
starts = linked.roots()
shortlist = top(starts, 20, by="token")

# 最后才执行昂贵的完整意图判断。
answer = intent(
    shortlist,
    "This method manages the token lifecycle and uses Redis as storage",
    ctx,
    threshold=0.6,
    fallback="keep",
    max_items=20,
)

for element in answer:
    evidence = answer.evidence_for(element.symbol_id)

    print(f"{element.kind} {element.name}")
    print(f"  {element.file}:{element.span[0]}")
    print(f"  scores: {dict(evidence.scores)}")

    for hit in evidence.unit_hits:
        print(
            f"  hit: unit={hit.unit}, signal={hit.signal}, "
            f"detail={hit.detail}, field={hit.field}, score={hit.score:.3f}"
        )

    for verdict in evidence.verdicts:
        print(
            f"  judge: {verdict.label}, confidence={verdict.score:.3f}, "
            f"reason={verdict.reason}"
        )
```

## 25. `Project.search()` 与手写算子的关系

高级入口：

```python
result = project.search(
    "buffer allocation",
    route="codegen",
)
```

内部仍然会落到相同 QL 算子上，但 QueryUnit 或脚本由系统生成。

当前三条 route：

| route | 查询理解方式 | QL 执行方式 |
|---|---|---|
| `lexical` | 从查询中提取项目可识别的词 | 构造一个 QueryUnit，执行 `eval_unit`，再做图邻近加权 |
| `planned` | LLM 提取 Unit、词和关系 | 构建 QuerySpec，统计规划并执行算子计划 |
| `codegen` | LLM 直接生成 Python QL | 白名单检查后执行生成脚本 |

调试建议：

```text
想验证最终用户体验
    使用 Project.search()

想验证 LLM 生成了什么
    使用 route="codegen" 并查看 result.script / result.explain()

想验证算子语义或排名原因
    使用 project.context 手写 QueryUnit 和算子链
```

## 26. 普通 Python API 与生成脚本白名单

普通 Python 程序可以直接导入所有公开类和函数。

模型生成脚本则经过：

[`codesense/ql/script.py`](../codesense/ql/script.py)

中的 AST 白名单检查。

### 26.1 生成脚本可调用的 QL 名称

当前白名单包括：

```text
eval_unit
hop
reach
degree
only
top
intent
score_of
QueryUnit
Term
LexicalSatisfier
AnnotationSatisfier
ModifierSatisfier
```

执行命名空间由 [`codesense/search.py`](../codesense/search.py) 提供，包括 `ctx` 和上述构造器/算子。

### 26.2 脚本必须产生 `answer`

```python
answer = top(found, 20)
```

未产生 `answer` 会触发 `ScriptError`。

### 26.3 控制流

生成脚本允许：

- `if`；
- `for`；
- `while`；
- 普通函数定义；
- 白名单内纯 builtin。

但存在最大脚本行数和最大执行步数限制。

### 26.4 被禁止的能力

生成脚本不能：

- `import`；
- 访问文件；
- 访问网络；
- 调用 `eval`、`open`、`__import__`；
- 定义 class；
- 访问未列入白名单的属性；
- 通过 `ctx` 访问 Judge 的 API key 等内部状态。

### 26.5 `intent` 的额外保护

普通 `project.search(..., judge=False)` 中，生成脚本即使写了 `intent`，搜索命名空间也会将它替换成不执行真实 Judge 的恒等函数；只有显式启用最终 Judge 时，搜索流程才会在缩小后的候选上运行意图判断。

手写 Python API 直接调用 `intent` 时，则由 `ctx.judge` 决定是否真正请求 LLM。

## 27. 当前未实现或不应误认为已实现的能力

### 27.1 未实现的 Satisfier

现役代码没有：

```text
SemanticSatisfier
EmbeddingSatisfier
StructuralSatisfier
RegexSatisfier
CodeSnippetSatisfier
CodeQLSatisfier
GraphSatisfier
```

### 27.2 图关系不是 Satisfier

当前职责是：

```text
eval_unit + Satisfier
    从倒排索引进行基础召回

Frag 的 | / & / -
    表达 OR / AND / NOT

hop / reach / degree
    表达代码图约束

only / top
    执行确定性过滤和排序

intent
    执行昂贵的完整语义判断
```

### 27.3 当前图边

现役索引主要构建：

```text
contains
calls
```

设计文档中的数据流、继承、依赖或协同变更边不代表当前已经存在。

### 27.4 当前语言支持

QL 本身是语言无关的，但当前内置索引 adapter 只有 Java。其他语言需要先实现并注册对应 `Language` adapter，才能为真实项目建立索引。

## 28. 常见问题

### 28.1 为什么不直接用一个很大的 QueryUnit？

如果多个概念需要落在不同代码元素上，应拆成多个 Unit，然后使用 `hop`。

错误建模：

```python
one = QueryUnit(
    "token_redis",
    satisfiers=(
        LexicalSatisfier(
            terms=(Term("token"), Term("redis")),
        ),
    ),
)
```

这倾向于寻找同一个元素中的词法共现。

更合适：

```python
token = eval_unit(token_unit, ctx)
redis = eval_unit(redis_unit, ctx)
linked = hop(token, redis, ctx)
```

### 28.2 多个 Term 是 AND 还是 OR？

一个 Satisfier 内的多个 Term 都会独立产生命中，然后使用 `noisy_or` 加强同一节点的得分。

因此它更接近“多个召回信号的软 OR”，不是强制所有词同时出现的布尔 AND。

若需要同一个节点必须满足两个概念，应构造两个 Unit 后取交集：

```python
both = eval_unit(unit_a, ctx) & eval_unit(unit_b, ctx)
```

### 28.3 多个 Satisfier 是 AND 还是 OR？

它们会分别产生证据，再由 `QueryUnit.combine` 合并分数，不是硬 AND。

如果必须强制满足两个独立条件，应分别执行 Unit，再用 `&` 取交集。

### 28.4 `concept` 会参与 `eval_unit` 搜索吗？

不会。`eval_unit` 只执行 `satisfiers`。

`concept` 主要用于：

- 保留完整语义；
- 提供给 `intent`；
- 供编译器和人理解。

### 28.5 `LexicalSatisfier` 会直接使用向量模型吗？

不会。查询阶段只使用已保存的 expansion table。

向量接地策略如果启用，模型只在建索引阶段用于产生接地表；查询阶段不会加载大型向量模型。

### 28.6 `hop` 为什么可能没有路径？

可能原因包括：

- 调用边没有被轻量 AST 解析器解析出来；
- 方向选反；
- `hops` 上限过小；
- `min_confidence` 过高；
- 中间节点超过 `max_degree`，被 hub throttling 阻止展开；
- `avoid` 排除了必要节点；
- 起点或终点 fragment 本身为空。

### 28.7 `roots()` 和入口点一样吗？

不完全一样。

```python
frag.roots()
```

计算当前 fragment 内部没有入边的节点。

```python
degree(frag, ctx, edge="calls", max_in=0)
```

计算完整索引图中没有调用入边的节点。

两者都只是基于当前轻量代码图的结构判断，不等于框架或运行时语义上的正式入口点检测。

## 29. 推荐调试顺序

调试一条 query 时，建议按以下顺序逐层检查：

### 第一步：检查项目词表

```bash
codesense info --index /path/to/project/.codesense --terms 100
```

确认查询词或接地词确实存在。

### 第二步：分别执行每个 Unit

```python
token = eval_unit(token_unit, ctx)
redis = eval_unit(redis_unit, ctx)

print(len(token), len(redis))
```

### 第三步：查看证据和分数

```python
for sid, element in token.nodes.items():
    print(element.name, token.evidence_for(sid).scores)
```

### 第四步：单独检查图关系

```python
linked = hop(token, redis, ctx, hops=(1, 3))
print(len(linked), len(linked.witnesses))
```

### 第五步：增加确定性过滤

```python
shortlist = top(only(linked.roots(), kind="method"), 20, by="token")
```

### 第六步：最后启用 `intent`

```python
answer = intent(shortlist, token_unit.concept, ctx, max_items=20)
```

这样可以准确判断问题发生在：

- QueryUnit 构造；
- Satisfier 召回；
- 词汇接地；
- 图边；
- 筛选排序；
- LLM Judge。

## 30. 源码阅读入口

推荐阅读顺序：

1. [`codesense/project.py`](../codesense/project.py)：Python 总入口；
2. [`codesense/search.py`](../codesense/search.py)：三条查询 route；
3. [`codesense/ql/unit.py`](../codesense/ql/unit.py)：`Term` 和 `QueryUnit`；
4. [`codesense/ql/satisfiers/base.py`](../codesense/ql/satisfiers/base.py)：Satisfier 抽象及评分路径；
5. [`codesense/ql/satisfiers/lexical.py`](../codesense/ql/satisfiers/lexical.py)：三个现役 Satisfier；
6. [`codesense/ql/operators/unit.py`](../codesense/ql/operators/unit.py)：`eval_unit`；
7. [`codesense/ql/frag.py`](../codesense/ql/frag.py)：`Frag` 和证据代数；
8. [`codesense/ql/operators/hop.py`](../codesense/ql/operators/hop.py)：`hop` 与 `reach`；
9. [`codesense/ql/operators/select.py`](../codesense/ql/operators/select.py)：`only`、`top`、`score_of`、`degree`；
10. [`codesense/ql/operators/intent.py`](../codesense/ql/operators/intent.py)：`intent`；
11. [`codesense/ql/script.py`](../codesense/ql/script.py)：生成脚本白名单和执行预算；
12. [`tests/integration/test_handwritten_queries.py`](../tests/integration/test_handwritten_queries.py)：手写 QL 综合示例。

## 31. API 速查

```python
from codesense import Project
from codesense.ql import Frag, IndexField
from codesense.ql.operators import (
    degree,
    eval_unit,
    hop,
    intent,
    only,
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

核心心智模型：

```text
QueryUnit 描述“要找的概念”
Satisfier 描述“代码如何满足这个概念”
eval_unit 执行基础索引搜索
Frag 保存候选、图结构和证据
其他算子继续组合、连图、过滤、排序和判断
```
