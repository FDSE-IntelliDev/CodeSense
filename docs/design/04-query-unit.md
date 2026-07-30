# 04 Query Unit：查询单元

整套设计里最重要的概念。**它是语义槽位，不是关键词，也不是一条 regex。**

## 定义

```python
@dataclass(frozen=True)
class QueryUnit:
    name: str                      # "performance"
    concept: str                   # "和性能表现有关的代码" —— 给人和 intent 看
    satisfiers: tuple[Satisfier, ...]   # 怎样算命中这个槽位
    combine: str = "max"           # 多信号怎么合成：max | sum | noisy_or
```

单元是**后续所有条件的挂载点**：

```python
hop(io, disk)        # 两个单元之间的图约束
intent(f, perf)      # 用单元的 concept 做语义判定
```

## 一个单元可以被多种信号满足

这是相对「一个单元一条 regex」最重要的一处解绑。

判断一段代码是不是「和性能有关」，词法只是**最弱的一种**证据。真实可用的信号：

| signal | 怎么命中 | 强度 | 例 |
|---|---|---|---|
| `lexical` | 标识符/签名/文档匹配派生词 | 弱～中 | 名字里有 `buffer` |
| `annotation` | 被特定注解标记 | **强** | `@Async`、`@Cacheable` |
| `structural` | 位于特定包/类/层 | 中 | 在 `io.buffer` 包下 |
| `modifier` | 语言级修饰符 | 中 | `async` / `volatile` |
| `graph` | 图上的位置 | 中 | 是入口点、在热路径上 |
| `semantic` | 与 concept 的向量相似度 | 中 | doc 语义接近 |
| `judged` | LLM 判定 | **强但贵** | 复核用 |

```python
perf = q.unit(
    "performance",
    concept="代码在优化或影响性能表现",
    satisfiers=[
        lexical(terms=["buffer", "async", "cache", "batch", "pool", "latency"], weight=0.5),
        annotation(units=["async", "cache", "schedule"], weight=0.9),   # 切分后按单元匹配
        structural(package=r".*\.(cache|pool|buffer)\..*", weight=0.7),
        modifier("async", weight=0.6),
    ],
    combine="noisy_or",
)
```

`annotation` 匹配的是**切分后的注解名单元**而不是字面正则，
所以项目自定义的 `@AppCache`、`@CacheAside` 会和 `@Cacheable` 一起命中
（[09](09-grounding.md) 第六节）。

**为什么这比一条 regex 好**：`@Async` 标注的方法名字里可能一个性能词都没有，
纯词法必然漏。反过来，一个叫 `cacheKey` 的字段命中了 `cache` 却和性能无关，
但它没有注解、不在相关包下，多信号合成后分数自然低。

**`combine` 的选择有实际后果**：

- `max` —— 任一强信号即可，召回优先
- `sum` —— 多个弱信号可以累积，但容易被一堆噪音词刷高
- `noisy_or` —— `1 - Π(1 - wᵢ)`，多个独立弱信号能累积但有上界，**默认**

## 为什么不把所有词拼成一条 regex

即便只谈词法，也不该拼：

```python
# ❌
match(r"\b(io|input|output|performance|latency|disk|swap|block)\b")
```

1. **单元身份丢了。** 命中 `swap` 和命中 `latency` 的元素混成一堆，
   后面说不出「performance 那组调用了 disk 那组」。
2. **语义被稀释成 OR。** 三个单元本该都要沾边（AND），拼起来变成沾任一即可。
3. **强度没法区分。** 命中 `disk` 和命中 `swap` 的可信度不同。
4. **没法分别调。** 想单独收紧 io 那组，一条大 regex 牵一发动全身。

**一个单元一组信号，单元之间的关系由算子表达。**

## 词从哪来：三种来源

`io performance on disk` 字面只有三个词，但能在代码里定位的词远不止。

### literal —— 查询里直接出现

置信度最高，但在代码里**往往最不常出现**：没多少函数叫 `performance`。

### synonym —— 同义词

`disk` → `storage`、`volume`。语义等价，可互换。

### derived —— 语义联想

**最有价值的一类。** `performance` → `buffer`、`async`、`cache`、`batch`、`pool`。

这批词由**把查询拆成单元的那一次 LLM 调用顺带给出**——不额外开调用，
不按单元逐个调。关键是同时**把项目词表放进 prompt**（实测 595 tokens，
固定前缀可缓存），让它**从项目实际用的词里挑**，而不是凭空生成通用词：

```
本项目词表: get, role, user, save, auth, ..., redis, page, cache
查询: io performance on disk        → 拆单元 + 每个单元从上表选词
```

这样输出的词天然落在项目词表里，不会出现「LLM 说 `department`
但项目写 `dept`」。词表塞不下的大项目，先用向量和 ICF 收窄候选
再交给 LLM——细节见 [09](09-grounding.md)。

`buffer` **不是** `performance` 的同义词。关系是：

> 代码在处理性能问题时，通常会出现 buffer 这样的东西。

| | synonym | derived |
|---|---|---|
| 来源 | 词表 / 全局向量 | **LLM 从项目词表中挑选** |
| 关系 | 语义等价 | 共现指示 |
| 代码里出现频率 | 低 | **高** |
| 单独命中可信度 | 高 | **低** |
| 效果 | 提召回，不太伤精度 | **大幅提召回，明显伤精度** |

所以 derived 词**不能单独下结论**——要么和同单元其它信号合成，
要么靠 `hop` 的图约束锚住，要么交给 `intent` 复核。

派生产物带来源与证据：

```json
{
  "unit": "performance",
  "concept": "代码在优化或影响性能表现",
  "terms": [
    {"value": "performance", "source": "literal", "weight": 1.0},
    {"value": "latency",     "source": "synonym", "weight": 0.8},
    {"value": "cache",       "source": "derived", "weight": 0.5, "sim": 0.81},
    {"value": "buffer",      "source": "derived", "weight": 0.5, "sim": 0.76,
     "surface": [{"form": "buf", "score": 0.91, "rule": "prefix"}]},
    {"value": "flush",       "source": "derived", "weight": 0.5, "sim": 0.71,
     "note": "本项目特有：微调后才进入 performance 的近邻"}
  ]
}
```

`sim` 和 `surface` 不是装饰：结果跑偏时能看出是哪一跳的锅，
审词表时能快速判断该不该删——和 LLM 给的自然语言 `reason` 相比，
它还是**可排序、可卡阈值**的。

### 项目特有的关联从哪来

`redis`、`page` 这类词能进 `performance` 单元，是因为**项目词表在 prompt 里**——
LLM 看得见这个项目用了 Redis、用了分页。实测本项目 `performance` 相关词里
恰恰是 `redis`（1289 次）、`page`（10377 次）这两个技术栈相关的词最有信息量，
而这正是纯通用联想给不出的部分。

## 单元的产出是片段

```python
perf: Frag = q.unit("performance", satisfiers=[...])
```

单元求值后就是一个 Frag（只有节点，没有边），每个节点的证据里记着
它被哪个 signal、以哪个 detail 命中，以及合成后的分数。

这让单元可以直接参与片段代数（`io & disk`）和图算子（`hop(io, disk)`），
不需要额外的转换。

## 分词与缩写扩展

代码标识符不是自然语言：`flushBuffer` 要先拆成 `flush` + `buffer`。
这是 `lexical` satisfier 的底层能力，方向有两个：

- **分词**：`flushBuffer` → `flush buffer`
- **缩写扩展**：查询词 `buffer` → 代码里可能的写法 `buf`、`bfr`

两者是同一件事的两个方向，通过倒排索引（`标识符 → 子词/缩写`）连起来。

**但这里有个真问题**：LLM 派生出的是通用词（`buffer`、`department`），
而项目里写的可能是 `buf`、`dept`——实测样例项目里 `department`
出现 **0 次**。通用知识怎么落到具体项目的表达习惯上，
是单独一章：[09 落地到具体项目](09-grounding.md)。

下一篇：[05 算子](05-operators.md)。
