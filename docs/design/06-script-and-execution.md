# 06 脚本形态与执行

## 为什么产物是脚本，不是 JSON 计划

当前编译产物是三份 JSON（`surface_semql.json` / `relation_semql.json` /
`intention_semql.json`），由三个 executor 解释执行。改成一段 Python 脚本的理由：

| | JSON 计划 | Python 脚本 |
|---|---|---|
| 出问题怎么查 | 读日志，猜解释器怎么走的 | 打断点、注释一行看差异 |
| 改一下再试 | 改 JSON，还得懂 schema | 改代码 |
| 表达组合 | 受限于 schema 预设的算子和顺序 | 任意表达式、变量、注释 |
| 复现 | 要连同解释器版本一起存 | 脚本本身就是完整描述 |
| 新增算子 | 改 schema + 改解释器 | 加一个函数 |

研究场景里「改一下再试」发生得极其频繁，这一条基本决定了选择。

**代价**：脚本是可执行代码。如果 LLM 生成的脚本直接跑，就等于执行模型产出的
代码。缓解手段见下面「安全」一节。

## 脚本长什么样

```python
"""io performance on disk

编译自：io performance on disk
生成时间：<stamp>    索引版本：<commit>
"""
from codesense.ql import Query, endpoints, hop, intent, match, of_type

q = Query("io performance on disk")

# ── query units ──────────────────────────────────────────────
io = q.unit(
    "io",
    match(r"\b(io|input|output|read|write|stream|flush)\w*", unit="io"),
    intent="和输入输出有关的代码",
)
perf = q.unit(
    "performance",
    match(r"\w*(buffer|async|cache|batch|pool|latency|throughput)\w*", unit="performance"),
    intent="和性能表现有关的代码",
)
disk = q.unit(
    "disk",
    match(r"\w*(disk|swap|block|sector|volume|storage)\w*", unit="disk"),
    intent="和磁盘有关的代码",
)

# ── 编排 ─────────────────────────────────────────────────────
# 磁盘 IO 的落点：同时沾 io 和 disk 的可调用元素
disk_io = of_type(io & disk, "function", "method")

# 从落点出发，三跳内够到 performance 相关代码
paths = hop(disk_io, perf, edge="calls", len=(1, 3), avoid=q.tests)

# 只对路径起点做语义判定 —— intent 最贵，放最后、作用在最小集合上
answer = intent(endpoints(paths, "source"), "这段代码影响磁盘 IO 的性能表现")

q.emit(answer, evidence=paths)
```

几个刻意的选择：

- **每个单元一个变量。** 脚本读起来就是查询的语义结构。
- **注释解释编排理由，不解释算子。** 算子语义在 [05](05-operators.md) 里，
  这里只说「为什么这么排」。
- **`q.emit` 显式声明产物。** 结果和证据分开传，避免把路径塞进元素集。
- **头部记录索引版本。** 同一段脚本在不同索引上结果不同，不记就没法复现。

## 执行

`Query` 对象承担三件事：注册单元、记录执行轨迹、收敛产物。
算子本身是纯函数，不持有状态——状态在 `Query` 和数据里。

```
脚本 ── 逐行执行 ──► 每个算子
                        │
                        ├─ 读索引 / 图库（IO 在这里发生）
                        ├─ 追加证据
                        └─ 返回新的 ElementSet / PathSet
```

**IO 边界**：算子内部会读倒排索引、codegraph、embedding 产物。按
[ARCHITECTURE.md](../../ARCHITECTURE.md) 的规则，这些资源应当在
`Query` 构造时注入，而不是算子里现开——否则算子没法在内存数据上测试。

```python
q = Query("...", index=index, graph=graph_store, judge=judge_client)
```

## 优化

编译期决定顺序，执行期做局部优化。**编译期的顺序更重要**——
算子代价差好几个数量级，排错了差百倍。

### 编排原则（编译期）

1. **便宜的先跑。** `match` / `of_type` / `degree` 是索引查询，
   `hop` 是图遍历，`similar` 要算向量，`intent` 要调 LLM。
2. **选择性高的先跑。** 能把候选从 1718 压到 30 的条件，
   比只能压到 800 的先跑。
3. **`intent` 永远最后。** 如果它前面还有没用上的便宜约束，是编排错了。

### 执行期优化

- **短路**：集合空了就不再往下算。
- **缓存**：同一个 `match` 在脚本里出现多次（`io & disk` 和后面的
  `hop(..., io)`）只算一次。按 `(算子, 参数)` 做 key。
- **路径截断**：`hop` 的 `max_paths` 到顶就停，并 `log` 出来。
  **不能静默截断**——那会让人以为「结果就这么多」。
- **`intent` 批量**：当前 `llm_judge_filter` 已经在按 batch 调，直接复用。

### 一个反直觉的点

「先词法后图」不总是对的。`io performance on disk` 里三个单元的词都很泛，
`match` 之后可能还剩几百个候选；而 `hop` 在只有 677 条边的图上非常快。
先跑 `hop` 圈定范围、再在范围内做词法匹配，可能整体更省。

**所以顺序应该由编译期根据查询特征决定，而不是写死。**
这正是当前固定管线做不到的事（[01](01-motivation.md) 问题 1）。

## 安全

脚本是 LLM 生成的可执行代码，这是新设计引入的**新风险**，当前 JSON 方案没有。

初稿倾向的做法（未定，见 [08](08-open-questions.md)）：

1. **受限命名空间**：`exec` 时只注入 `codesense.ql` 的算子，
   不给 `open` / `__import__` / `os`。
2. **AST 白名单校验**：执行前解析脚本，只允许调用白名单里的算子、
   赋值、集合运算；出现 `import`、属性访问链、循环就拒绝。
3. **人工确认**：脚本对用户可见，敏感场景下先给人看再跑。

第 2 条最实在——QL 脚本的语法本来就该很窄，宽了反而说明编排出了问题。
反过来也是个好信号：**如果生成的脚本需要循环和条件分支才能表达，
说明缺算子。**

## 产物

每次查询落一个目录，和当前 `output/<project>/query_<id>/` 的组织一致：

```
query_<id>/
├── query.ql.py             编译出来的脚本（复现的唯一依据）
├── units.json              单元与派生词表，含来源与理由
├── result.json             最终元素集
├── evidence.json           每个元素的证据链
├── paths.json              路径集
└── trace.json              每个算子的输入输出规模与耗时
```

`trace.json` 是新增的，用来回答「时间花在哪」「哪一步把结果砍没了」——
当前排查这类问题只能靠 print。

下一篇：[07 与当前实现的对应](07-mapping-to-current.md)。
