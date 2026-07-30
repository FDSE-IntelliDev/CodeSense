# 02 总览

## 端到端

```
自然语言 query
      │
      ▼  ① 单元切分          NL → [query unit]
   query units：io / performance / disk
      │
      ▼  ② 关键词派生        每个 unit → 一组词 → 一条 regex
   io   → \b(io|input|output|read|write|stream|flush)\b
   perf → \b(buffer|async|cache|batch|pool|latency|throughput)\b
   disk → \b(disk|swap|block|sector|volume|storage)\b
      │
      ▼  ③ 编排              units + 约束 → QL 脚本
   一段 Python：match / hop / intent / 集合代数
      │
      ▼  ④ 执行
   带证据的结果
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

每个单元独立派生出一组词，各自成一条 regex（为什么不拼成一条，见
[04](04-query-unit.md)）：

```python
io   = unit("io",          terms=["io", "input", "output", "read", "write", "stream", "flush"])
perf = unit("performance", terms=["buffer", "async", "cache", "batch", "pool",
                                  "latency", "throughput", "perf"])
disk = unit("disk",        terms=["disk", "swap", "block", "sector", "volume", "storage"])
```

注意 `performance` 那一组里一个「performance」的同义词都没有——
`buffer`、`async` 是**联想**出来的：代码在处理性能问题时通常长这样。
这类词的来源和置信度要单独记，见 [04](04-query-unit.md)。

### ③ 编排

```python
from codesense.ql import Query, endpoints, intent, match, of_type, unit

q = Query("io performance on disk")

io   = q.unit("io",          match(r"\b(io|input|output|read|write|stream|flush)\w*"))
perf = q.unit("performance", match(r"\w*(buffer|async|cache|batch|pool|latency|throughput)\w*"))
disk = q.unit("disk",        match(r"\w*(disk|swap|block|sector|volume|storage)\w*"))

# 磁盘 IO 的入口：同时命中 io 与 disk 的可调用元素
disk_io = of_type(io & disk, "function", "method")

# 从磁盘 IO 出发，三跳内能到达 performance 相关代码的调用路径
paths = hop(disk_io, perf, edge="calls", max_len=3)

# 在这些路径的起点里，挑真正在做「磁盘 IO 性能」这件事的
answer = intent(
    endpoints(paths, "source"),
    "这段代码影响磁盘 IO 的性能表现",
)

q.emit(answer, evidence=paths)
```

### ④ 执行

产出的不只是元素列表，还有**为什么**：哪个单元、命中哪个词、
经由哪条路径连到另一个单元。

```json
{
  "symbol_id": 812,
  "name": "flushBuffer",
  "units": {
    "io":   {"term": "flush",  "source": "derived",  "field": "name"},
    "disk": {"term": "block",  "source": "derived",  "field": "container"}
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
answer = io & perf & disk        # 名字里同时含有这三类词的元素
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
通过图连成一个整体。这也是为什么它返回**路径**而不是元素集——
路径本身就是答案的一部分，也是给用户看的证据。

---

## 编译期与执行期的分工

| | 编译期（LLM 参与） | 执行期（纯计算） |
|---|---|---|
| 干什么 | 切单元、派生关键词、决定算子编排与顺序 | 跑索引、查图、算相似度、调 judge |
| 产物 | 一段 QL 脚本 | 元素集 + 路径集 + 证据 |
| 可复现 | 存档脚本即可完整复现 | 依赖索引版本 |
| 出错怎么查 | 读脚本，它就是可读的 | 单步跑，注释掉一行看差异 |

`intent` 算子在执行期仍会调 LLM——它是执行期唯一的模型调用点，
也是代价最高的算子，所以编排时应尽量把它放在最后、作用在最小的集合上。

下一篇：[03 数据模型](03-data-model.md)。
