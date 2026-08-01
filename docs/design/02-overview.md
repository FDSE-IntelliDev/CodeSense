# 02 总览

## 端到端

```
自然语言 query
      │
      ▼  ① 单元切分          NL → [query unit]
   query units：io / performance / disk
      │
      ▼  ② 信号派生          每个 unit → 多种命中信号
   io   → 词法(io|read|write|flush…)
   perf → 词法(buffer|async|cache…) + 注解(@Async|@Cacheable) + 包位置
   disk → 词法(disk|swap|block…)   + 包位置(*.storage.*)
      │
      ▼  ③ 编排              units + 约束 → QL 脚本
   一段 Python：unit / hop / intent / 片段代数
      │
      ▼  ④ 执行
   带证据的连通片段
```

①②③ 由 LLM 驱动（编译期），④ 是纯执行。**编译与执行分离**是这套设计的前提：
编译一次产出的脚本可以反复跑、可以手工改、可以存档复现。

---

## 贯穿全文的例子

```
io performance on disk
```

选它是因为它把当前实现的短板全暴露出来了：三个词都很泛、没有一个元素会同时
含有这三个词、真正的信号在元素之间的关系上。

### ① 单元切分

拆成三个查询单元，而不是三个关键词：

| unit | 在问什么 |
|---|---|
| `io` | 和输入输出有关的代码 |
| `performance` | 和性能有关的代码 |
| `disk` | 和磁盘有关的代码 |

单元是**语义槽位**，不是字符串。后面的图约束和意图判断都挂在单元上。

### ② 关键词派生

每个单元独立派生出自己的命中信号（为什么不把词拼成一条大 regex，见
[04](04-query-unit.md)）：

```python
perf = q.unit("performance", concept="代码在优化或影响性能表现", satisfiers=[
    lexical(["buffer", "async", "cache", "batch", "pool", "latency"], weight=0.5),
    annotation(r"@(Async|Cacheable|Scheduled)", weight=0.9),
    structural(package=r".*\.(cache|pool|buffer)\..*", weight=0.7),
    modifier("async", weight=0.6),
])
```

两点值得注意。

**词法只是其中一种信号。** `@Async` 标注的方法名字里可能一个性能词都没有，
纯词法必然漏；反过来一个叫 `cacheKey` 的字段命中了 `cache` 却与性能无关，
但它没注解、不在相关包下，合成后分数自然低。单元可以由词法、注解、
结构位置、修饰符、语义相似度、图位置等多种信号满足，见 [04](04-query-unit.md)。

**`performance` 的词表里一个同义词都没有。** `buffer`、`async` 是**联想**
出来的——代码在处理性能问题时通常长这样。这类词召回收益大但明显伤精度，
必须配合别的信号或图约束，不能单独下结论。

### ③ 编排

```python
from codesense.ql import Query, annotation, hop, intent, lexical, of_kind, structural

q = Query("io performance on disk")

io   = q.unit("io",   concept="输入输出", satisfiers=[
    lexical(["io", "input", "output", "read", "write", "stream", "flush"])])
disk = q.unit("disk", concept="磁盘存储", satisfiers=[
    lexical(["disk", "swap", "block", "sector", "volume", "storage"]),
    structural(package=r".*\.(storage|fs|vfs)\..*", weight=0.7)])
perf = q.unit("performance", concept="代码在优化或影响性能表现", satisfiers=[
    lexical(["buffer", "async", "cache", "batch", "pool", "latency"], weight=0.5),
    annotation(r"@(Async|Cacheable)", weight=0.9)])

# 磁盘 IO 的落点：同时沾 io 与 disk 的可调用元素
disk_io = of_kind(io & disk, "function", "method")

# 从落点出发，三跳内够到 performance —— 产出是一个连通片段，不是两个端点
f = hop(disk_io, perf, edge=["calls", "flows_to"], len=(1, 3), avoid=q.tests)

# 只对路径起点做语义判定：intent 最贵，放最后、作用在最小片段上
answer = intent(f.roots(), perf.concept)

q.emit(answer, context=f)
```

### ④ 执行

产出的不是元素列表，是一个**带证据的连通片段**：哪些节点、它们之间的边、
每个节点命中了哪个单元的哪种信号、经由哪条路径连到另一个单元。

```json
{
  "symbol_id": 812,
  "name": "flushBuffer",
  "units": {
    "io":   [{"signal": "lexical",    "detail": "flush",       "field": "name",   "score": 0.8}],
    "disk": [{"signal": "structural", "detail": "pkg io.block", "score": 0.7},
             {"signal": "lexical",    "detail": "block", "field": "container", "score": 0.5}]
  },
  "paths": [
    {"to_unit": "performance", "len": 2,
     "via": ["flushBuffer", "writeBatch", "AsyncWriter.submit"]}
  ],
  "intent": {"verdict": "match", "reason": "缓冲写盘，批量提交以减少 syscall"}
}
```

---

## 为什么单元会落在不同元素上

这是整套设计最重要的一点，值得单独说。

朴素做法是把三个单元当成三个必须**同时满足**的谓词：

```python
answer = io & perf & disk        # 同时满足三个单元的元素
```

在真实代码库里这样的元素几乎不存在。`flushBuffer` 有 io 和 perf 的味道，
但没有 disk；`BlockDevice.write` 有 io 和 disk，但没有 perf。

真实的答案形态是：**几个单元分布在一条调用路径上**。

```
   BlockDevice.write        AsyncWriter.submit         BatchPool.acquire
   ├─ 命中 io、disk          ├─ 命中 perf(async)         ├─ 命中 perf(batch/pool)
   └───────── calls ────────►└────────── calls ─────────►
```

所以 `hop` 不是过滤器，是**产生候选的手段**：它把「分散在多处的语义」
通过图连成一个整体。

这也解释了为什么核心数据类型是**片段**而不是集合（[03](03-data-model.md)）：
上面那三个节点加两条边就是答案本身，把它拍成
`[write, submit, acquire]` 会丢掉使它成为答案的结构。

---

## 编译期与执行期的分工

| | 编译期（LLM 参与） | 执行期（纯计算） |
|---|---|---|
| 干什么 | 切单元、派生信号、决定算子编排与顺序 | 跑索引、查图、算相似度、调 judge |
| 产物 | 一段 QL 脚本 | 带证据的片段 |
| 可复现 | 存档脚本即可完整复现 | 依赖索引版本 |
| 出错怎么查 | 读脚本，它就是可读的 | 单步跑，注释掉一行看差异 |

`intent` 算子在执行期仍会调 LLM——它是执行期唯一的模型调用点，
也是代价最高的算子，所以编排时应尽量把它放在最后、作用在最小的片段上。

**编译不必是一次性的。** 编译器可以先出一版脚本、试跑、看结果规模与质量，
再修正——比如发现 `io & disk` 只剩 3 个候选，就该放宽成 `io | disk`
再靠图约束收。这个回路见 [06](06-script-and-execution.md)。

下一篇：[03 数据模型](03-data-model.md)。
