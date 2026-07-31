# 05 算子

## 全表

所有算子的类型都是 `Frag -> Frag`（[03](03-data-model.md)）。**没有类型转换，
因此可以任意串联。**

| 算子 | 签名 | 代价 | 说明 |
|---|---|---|---|
| **取数** | | | |
| `unit` | `(name, concept, satisfiers) -> Frag` | 低 | 求值一个查询单元，多信号合成 |
| `lexical` `annotation` `structural` `modifier` | satisfier | 低 | 单元的命中方式（[04](04-query-unit.md)） |
| `universe` | `(kind=, file=) -> Frag` | 低 | 全集。图优先的查询用它起手 |
| **图** | | | |
| `hop` | `(src, dst, edge=, dir=, len=, via=, avoid=) -> Frag` | 中 | 两片段之间满足图约束的路径 |
| `reach` | `(src, edge=, dir=, len=) -> Frag` | 中 | 单向可达，无目标 |
| `degree` | `(f, in_=, out=, edge=) -> Frag` | 低 | 按出入度筛 |
| `neighbors` | `(f, radius=, edge=) -> Frag` | 中 | 邻域扩展，补上下文 |
| **结构** | | | |
| `contains` / `within` | `(a, b) -> Frag` | 低 | 结构包含，非调用 |
| `of_kind` | `(f, *kinds) -> Frag` | 低 | 按元素种类筛 |
| `in_file` | `(f, pattern) -> Frag` | 低 | 按路径筛 |
| `has_modifier` | `(f, *mods) -> Frag` | 低 | static / async / abstract |
| **语义** | | | |
| `intent` | `(f, concept) -> Frag` | **高** | 语义判定，调 LLM |
| `similar` | `(f, text, top=) -> Frag` | 中 | embedding 相似度 |
| **片段代数** | | | |
| `&` `\|` `-` | `(Frag, Frag) -> Frag` | 低 | 交并差 |
| `rank` | `(f, by=, top=) -> Frag` | 低 | 排序取前 N |
| `roots` `leaves` `only_nodes` | `(f) -> Frag` | 低 | 投影 |

代价一列是编排的依据：**贵的算子作用在尽量小的片段上**，所以 `intent` 通常最后。

---

## 取数

### `unit`

```python
def unit(name: str, *, concept: str, satisfiers: Sequence[Satisfier],
         combine: str = "noisy_or") -> Frag
```

求值一个查询单元，返回命中它的片段（只有节点）。多信号合成见
[04](04-query-unit.md)。

```python
perf = q.unit("performance", concept="代码在优化或影响性能表现", satisfiers=[
    lexical(["buffer", "async", "cache", "batch", "pool"], weight=0.5),
    annotation(r"@(Async|Cacheable)", weight=0.9),
    structural(package=r".*\.(cache|pool|buffer)\..*", weight=0.7),
])
```

**这是词法进入系统的唯一入口。** 没有裸的 `match` 算子——
词法匹配是 `lexical` satisfier，必须挂在某个单元下。

这个限制是刻意的：脱离单元的关键词命中没法解释（「它为什么在结果里」
答不上来），也没法参与后续的图约束。

### `universe`

```python
def universe(*, kind: Sequence[str] | None = None, file: str | None = None) -> Frag
```

不做匹配，直接取全集（可按种类/路径预筛）。

存在的理由：**有些查询根本没有关键词。** 「哪些方法被 `TokenManager.refresh`
调用」——没有词可匹配，起手就该是图。

```python
refresh = q.unit("target", concept="令牌刷新", satisfiers=[lexical(["refresh"])])
callers = hop(universe(kind=["method"]), refresh, edge="calls", len=1).roots()
```

---

## 图

### `hop`

```python
def hop(
    src: Frag,
    dst: Frag,
    *,
    edge: str | Sequence[str] = "calls",
    direction: str = "forward",              # forward | backward | any
    hops: int | tuple[int, int] = (1, 3),    # 跳数，闭区间
    via: Frag | None = None,               # 必须经过
    avoid: Frag | None = None,             # 不得经过
    min_confidence: float = 0.0,           # 边的置信度门槛
    max_paths: int | None = 10_000,
    max_degree: int | None = 64,           # hub 限流
) -> Frag
```

> 参数取名 `hops` / `direction` 而不是 `len` / `dir`：后者遮蔽内置名，
> 函数体内就用不了 `len()`。

**两个片段之间满足图约束的路径。整套设计的核心算子。**

它不是过滤器，是**把分散在多处的语义连成一个整体**（[02](02-overview.md)）。
返回的 Frag 含路径上的全部节点、边，以及路径见证。

```python
f = hop(disk_io, perf, ctx, edge="calls", hops=(1, 3), avoid=q.tests)
```

设计点：

- **`hops` 是区间不是上限。** `hops=(2, 2)` 表示恰好两跳——「间接调用而非直接调用」
  是真实意图。
- **`edge` 可以给多种。** `edge=["calls", "flows_to"]` 表示「调用或数据流可达」。
- **`direction="any"` 走无向**：「这两块有没有关系」不关心方向。
- **`min_confidence`** 用来排除动态分派、反射这类低置信边。
- **`max_paths` 必须有默认值且截断要 log。** 路径数随跳数指数增长；
  静默截断会让人以为「结果就这么多」。实测修复后平均度约 4.4，
  `hops=(1,5)` 单向就是 2133 条路径/起点。

边从哪来、遍历怎么实现、路径爆炸怎么控制，见 [10 图基座](10-graph.md)。
**当前实现的图跑不动**——调用解析率 18%、64% 的符号是孤点。

### `reach`

```python
def reach(src: Frag, ctx, *, edge="calls", direction="forward", hops=(1, 3)) -> Frag
```

无目标的可达，回答「从这里出发能到哪」。与 `hop` 的区别是没有 `dst`
——它在探索，不在验证约束。

### `degree`

```python
def degree(f: Frag, *, in_=None, out=None, edge: str = "calls") -> Frag
```

按出入度筛。三个常见图角色是它的特例：

```python
entry_points = degree(f, in_=0, out=(1, None))
leaves       = degree(f, in_=(1, None), out=0)
hot          = degree(f, in_=(20, None))          # 被调用超过 20 次
```

做成通用谓词而非三个命名角色，是因为「热点」这类条件同样有用，
没必要每种情况新增一个算子。**`edge` 参数让它能问「被多少个类实现」
（`edge="implements"`）**，不只是调用度。

### `neighbors`

```python
def neighbors(f: Frag, radius: int = 1, *, edge="calls", dir="any") -> Frag
```

邻域扩展，给结果补上下文——单看一个函数常看不懂，带上直接调用方和被调用方
就清楚了。与 `hop` 的区别：`hop` 有目标（约束），`neighbors` 没有（补全）。

---

## 结构

### `contains` / `within`

```python
def contains(outer: Frag, inner: Frag) -> Frag   # 返回 outer 的子集
def within(inner: Frag, outer: Frag) -> Frag     # 返回 inner 的子集
```

**结构包含，不是调用**——类含方法、文件含类、包含文件。走 `contains` 边。

单列是因为它与 `hop` 语义完全不同，混在一起脚本会难读：

```python
buffer_classes = contains(universe(kind=["class"]), perf)   # 含性能相关方法的类
```

### `of_kind` / `in_file` / `has_modifier`

```python
def of_kind(f: Frag, *kinds: str) -> Frag
def in_file(f: Frag, pattern: str) -> Frag
def has_modifier(f: Frag, *mods: str) -> Frag     # async / static / abstract ...
```

属性过滤。`has_modifier` 是相对初稿新增的——「异步的写盘方法」里
`async` 是语言级事实，比任何关键词都准，不该靠正则从签名里抠。

---

## 语义

### `intent`

```python
def intent(f: Frag, concept: str, *, mode="judge", threshold=0.5,
           batch_size=5, fallback="similar") -> Frag
```

**返回片段中满足某个意图的部分。** 执行期唯一调 LLM 的算子，也是最贵的。

```python
answer = intent(f.roots(), "这段代码影响磁盘 IO 的性能表现")
```

三条纪律：

1. **放最后，作用在最小片段上。** 如果它前面还有没用上的便宜约束，是编排错了。
2. **判定必须进证据**（verdict + reason），否则用户无从判断该不该信。
3. **必须能降级**（`fallback`）。LLM 不可用时退化成 `similar` 或放行，
   而不是让整条查询失败。

### `similar`

```python
def similar(f: Frag, text: str, *, top=None, threshold=None) -> Frag
```

Embedding 相似度，比 `intent` 便宜一个数量级。典型用法是把片段压到
`intent` 负担得起的规模。

---

## 片段代数

```python
a & b      # 交：节点交，边保留两端都在的，证据合并
a | b      # 并
a - b      # 差
```

**交集必须合并证据**——一个元素同时满足两边，两边的理由都要留。
这条最容易漏，漏了就出现「结果里有个元素但说不出为什么」。

```python
def rank(f: Frag, *, by: str = "score", top: int | None = None) -> Frag
```

`by` 可以是证据里任一分数：单元命中合成分、embedding 相似度、
路径长度倒数、路径条数……

---

## 待定的算子

见 [08](08-open-questions.md)：

- **`co_change`** —— 「git 历史里总是一起改」。信号很强，但它是与代码结构
  **正交**的信息源，塞进 `hop(edge="co_change")` 会让「边」的含义变味。
  倾向单独做工具，不进 QL。
- **路径级否定** —— `-` 是片段差。「不经过任何 IO 函数的性能路径」是
  路径级否定，`avoid` 参数能表达一部分，但「不存在任何一条路径满足 X」
  这种全称否定还表达不了。
- **聚合** —— 「调用 IO 最多的那个类」需要按容器分组再排序，
  现在的算子都是过滤，没有 group-by。

下一篇：[06 脚本与执行](06-script-and-execution.md)。
