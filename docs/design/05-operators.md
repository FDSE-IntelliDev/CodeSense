# 05 算子

## 全表

| 算子 | 签名 | 代价 | 说明 |
|---|---|---|---|
| **取数** | | | |
| `match` | `(pattern, field=, types=) -> ElementSet` | 低 | 正则匹配，产生候选 |
| `all_of` | `(types=, file=) -> ElementSet` | 低 | 全集，图优先的查询用它起手 |
| **图** | | | |
| `hop` | `(src, dst, edge=, dir=, len=) -> PathSet` | 中 | 两集合之间满足图约束的路径 |
| `endpoints` | `(paths, side) -> ElementSet` | 低 | 路径集回到元素集 |
| `nodes` | `(paths) -> ElementSet` | 低 | 含中间节点 |
| `expand` | `(set, radius, dir=) -> ElementSet` | 中 | 邻域扩展，补上下文 |
| `degree` | `(set, in_=, out=) -> ElementSet` | 低 | 按出入度筛（entry_point / leaf / isolate） |
| **结构** | | | |
| `contains` | `(outer, inner) -> ElementSet` | 低 | 包含关系（类含方法、文件含类） |
| `within` | `(inner, outer) -> ElementSet` | 低 | `contains` 的反向 |
| `of_type` | `(set, *types) -> ElementSet` | 低 | 按元素类型筛 |
| `in_file` | `(set, pattern) -> ElementSet` | 低 | 按路径筛 |
| **语义** | | | |
| `intent` | `(set, description) -> ElementSet` | **高** | 语义判定，会调 LLM |
| `similar` | `(set, text, top=) -> ElementSet` | 中 | embedding 相似度筛/排 |
| **集合** | | | |
| `&` `\|` `-` | `(ElementSet, ElementSet) -> ElementSet` | 低 | 交并差 |
| `rank` | `(set, by=, top=) -> ElementSet` | 低 | 排序取前 N |

代价一列是编排时的依据：**贵的算子应该作用在尽量小的集合上**，
所以 `intent` 通常排在最后。

---

## 取数

### `match`

```python
def match(
    pattern: str,
    *,
    field: str | Sequence[str] = "name",   # name | signature | container | doc | body
    types: Sequence[str] | None = None,    # 限定元素类型
    unit: str | None = None,               # 归属的 query unit
) -> ElementSet
```

接收正则，返回元素集。**这是唯一从无到有产生候选的词法入口。**

- 匹配前标识符已经过分词与缩写扩展（[04](04-query-unit.md)），
  所以 `flushBuffer` 能被 `\bbuffer\b` 命中。
- `field` 默认 `name`。开 `body` 要谨慎，它几乎总能命中。
- 每个命中都往证据里追加一条 `UnitHit`，记下 unit、term、field、位置。

```python
io = match(r"\b(io|input|output|read|write|stream|flush)\w*", unit="io")
```

### `all_of`

```python
def all_of(*, types: Sequence[str] | None = None, file: str | None = None) -> ElementSet
```

不做词法匹配，直接取全集（可按类型/路径预筛）。

存在的理由：**有些查询根本没有有意义的关键词**。
「哪些函数被 `TokenManager.refresh` 调用」——这里没有词可匹配，
起手就该是图。当前实现表达不了这种查询，因为管线强制先跑 surface。

```python
callers = endpoints(hop(all_of(types=["method"]), refresh, edge="calls", len=1), "source")
```

---

## 图

### `hop`

```python
def hop(
    src: ElementSet,
    dst: ElementSet,
    *,
    edge: str | Sequence[str] = "calls",       # calls | implements | contains | imports
    dir: str = "forward",                      # forward | backward | any
    len: int | tuple[int, int] = (1, 3),       # 跳数，闭区间
    via: ElementSet | None = None,             # 路径必须经过
    avoid: ElementSet | None = None,           # 路径不得经过
    max_paths: int | None = 10_000,            # 防爆
) -> PathSet
```

**两个元素集之间，满足图约束的所有路径。** 整套设计的核心算子。

它的作用不是过滤，是**把分散在多处的语义连成一个整体**（[02](02-overview.md)
里论证过为什么必须如此）。

```python
paths = hop(disk_io, perf, edge="calls", len=(1, 3))
```

几个设计点：

- **`len` 是区间不是上限。** `len=(2, 2)` 表示恰好两跳——
  「间接调用而非直接调用」是真实的查询意图。
- **`dir="any"` 走无向。** 「这两块代码有没有关系」不关心方向。
  当前 `undirected_call_pairs` 已经在做这件事。
- **`avoid` 的典型用途是排除测试代码**，让路径落在生产代码里。
- **`max_paths` 必须有默认值。** 路径数随跳数指数增长，没有上限的话
  三跳就能打爆内存。截断时要 `log` 出来，不能静默——
  静默截断会让人误以为「就这么多结果」。

> 当前 `RelationGraphStore.reachable()` 返回可达元素集，遍历时丢掉了路径。
> 支持 `hop` 需要改成保留前驱链，见 [07](07-mapping-to-current.md)。

### `endpoints` / `nodes`

```python
def endpoints(paths: PathSet, side: str = "source") -> ElementSet   # source | target | both
def nodes(paths: PathSet) -> ElementSet                             # 含中间节点
```

路径集回到元素集的**唯一通道**。

`nodes` 单列是因为中间节点常常才是答案：`io → perf` 路径上那个
`AsyncWriter.submit`，往往比两端更值得看。

### `expand`

```python
def expand(set_: ElementSet, radius: int = 1, *, dir: str = "any",
           edge: str = "calls") -> ElementSet
```

邻域扩展。和 `hop` 的区别：

- `hop` 有**目标集**，是约束，回答「A 和 B 有没有关系」
- `expand` **没有目标**，是补全，回答「A 周围还有什么」

用途是给结果补上下文——单看一个函数往往看不懂，把它的直接调用方和被调用方
带上就清楚了。对应 `semql-report.md` 里的 Bundle 概念。

### `degree`

```python
def degree(set_: ElementSet, *, in_: int | tuple | None = None,
           out: int | tuple | None = None) -> ElementSet
```

按调用图出入度筛。当前实现里的三个图角色是它的特例：

```python
entry_points = degree(s, in_=0, out=(1, None))    # 无人调用，但调用别人
leaves       = degree(s, in_=(1, None), out=0)
isolates     = degree(s, in_=0, out=0)
```

做成通用的 `degree` 而不是三个命名角色，是因为「被调用超过 20 次的函数」
（热点）这类条件同样有用，没必要为每种情况新增一个算子。

---

## 结构

### `contains` / `within`

```python
def contains(outer: ElementSet, inner: ElementSet) -> ElementSet   # 返回 outer 的子集
def within(inner: ElementSet, outer: ElementSet) -> ElementSet     # 返回 inner 的子集
```

**结构包含，不是调用关系**——类含方法、文件含类、包含文件。
它走的是 `container` / `file` / 行号区间，不是 `code_edges`。

单列出来是因为它和 `hop` 语义完全不同，混在一起会让脚本难读：

```python
# 含有缓冲相关方法的类
buffer_classes = contains(of_type(all_of(), "class"), perf)
```

### `of_type` / `in_file`

```python
def of_type(set_: ElementSet, *types: str) -> ElementSet
def in_file(set_: ElementSet, pattern: str) -> ElementSet
```

属性过滤。`of_type` 对应当前的 `type_filter`。

---

## 语义

### `intent`

```python
def intent(
    set_: ElementSet,
    description: str,
    *,
    mode: str = "judge",      # judge | rank
    threshold: float = 0.5,
    batch_size: int = 5,
) -> ElementSet
```

**接收一个集合，返回其中满足某个意图的子集。** 执行期唯一会调 LLM 的算子，
也是最贵的一个。

```python
answer = intent(candidates, "这段代码影响磁盘 IO 的性能表现")
```

三条使用纪律：

1. **放在最后，作用在最小的集合上。** 编排时如果 `intent` 前面还有便宜的
   约束没用上，那是编排错了。
2. **判定结果必须进证据。** `verdict` + `reason` 都要留，
   否则用户没法判断该不该信。
3. **要能降级。** LLM 不可用时应退化成 `similar`（embedding 打分）
   或直接放行，而不是让整条查询失败。

当前 `filters/llm_judge_filter.py` 就是它的实现原型（批量、结构化返回、
kept/discarded/uncertain 三分），可以直接复用。

### `similar`

```python
def similar(set_: ElementSet, text: str, *, top: int | None = None,
            threshold: float | None = None) -> ElementSet
```

Embedding 相似度。比 `intent` 便宜一个数量级，适合做粗筛，
把集合压到 `intent` 能负担的规模。

当前 `filters/embedding_filter.py` 与 `filters/cluster_pipeline.py` 是它的原型。
两者的分工（cluster 粗筛 → embedding 细筛 → judge 兜底）在新模型里
变成脚本里的三行，顺序由编排决定而不是写死。

---

## 集合代数

```python
a & b     # 交
a | b     # 并
a - b     # 差
```

对应当前 surface executor 的四层集合运算（term OR、group AND(n)、
condition subtract、跨 condition 合并）。区别是：现在这四层是**写死的执行顺序**，
新模型里就是脚本里的普通表达式。

### `rank`

```python
def rank(set_: ElementSet, *, by: str = "score", top: int | None = None) -> ElementSet
```

排序取前 N。`by` 可以是证据里的任一分数（unit 命中权重之和、embedding 相似度、
路径长度倒数……）。

---

## 还需要什么算子？（待定）

以下是想到但没定下来的，见 [08](08-open-questions.md)：

- **`dataflow`** —— 定义-使用链上的 hop。现在图里只有 `calls` 边，
  没有 def-use，做不了。要先扩索引。
- **`same_file` / `co_change`** —— 「在同一个文件里」「在 git 历史里
  总是一起改」。后者信号很强（我在整理这个仓库时用共现分析找出过真实的抽象泄漏），
  但需要接 git 历史。
- **`text_search`** —— 在函数体全文里搜，而不是标识符。
  当前 `exact_code_search` 的 `code_line` 模式是雏形。
- **`negate` 的语义** —— `-` 是集合差，但「不调用任何 IO 函数的性能代码」
  是路径级的否定，集合差表达不了。

下一篇：[06 脚本与执行](06-script-and-execution.md)。
