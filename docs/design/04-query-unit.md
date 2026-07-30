# 04 Query Unit：查询单元与关键词派生

整套设计里最重要的一个概念。**它不是关键词，是语义槽位。**

## 定义

一个 query unit 由三部分组成：

```python
@dataclass(frozen=True)
class QueryUnit:
    name: str                  # "performance"
    intent: str                # "和性能表现有关的代码" —— 给 intent 算子和人看
    terms: tuple[Term, ...]    # 派生出来的词
```

```python
@dataclass(frozen=True)
class Term:
    value: str
    source: str        # literal | synonym | derived
    weight: float = 1.0
    reason: str = ""   # 为什么派生出这个词
```

单元是**后续所有条件的挂载点**：

```python
hop(io, disk)                      # 两个单元之间的图约束
intent(perf_elements, perf.intent) # 单元自带的意图描述
```

## 为什么不把关键词拼成一条 regex

朴素做法：

```python
# ❌
match(r"\b(io|input|output|performance|latency|disk|swap|block)\b")
```

四个问题：

**1. 单元身份丢了。** 命中 `swap` 的元素和命中 `latency` 的元素被混成一堆，
后面没法说「performance 那组的元素调用了 disk 那组的元素」。

**2. 语义被稀释成 OR。** 三个单元本该是 AND 关系（都要沾边），拼成一条 regex
之后变成了「沾上任意一个词就算」，噪音爆炸。

**3. 命中强度没法区分。** 命中 `disk` 和命中 `swap` 的置信度不同，
拼进一条 regex 之后无法分别加权。

**4. 没法分别调。** 发现结果里全是 io 噪音时，想单独收紧 io 那组——
一条大 regex 改起来牵一发动全身。

所以：**一个单元一条 regex，单元之间的关系由算子表达。**

```python
io   = q.unit("io",          match(r"\b(io|input|output|read|write|stream|flush)\w*"))
perf = q.unit("performance", match(r"\w*(buffer|async|cache|batch|pool|latency)\w*"))
disk = q.unit("disk",        match(r"\w*(disk|swap|block|sector|volume)\w*"))
```

> 当前实现里的 `SurfaceKeywordGroup`（组内 OR、组间 AND）已经是这个形态的雏形。
> 差别在于：现在的组只在**匹配阶段**有身份，匹配完就拍平了；
> 新设计里单元的身份贯穿全程，直到最终证据。

## 关键词从哪来：三种来源

这是本章的另一半。`io performance on disk` 字面上只有三个词，
但真正能在代码里定位的词远不止这三个。

### literal —— 查询里直接出现的

`io`、`performance`、`disk`。置信度最高，但在代码里**往往最不常出现**：
没有多少函数叫 `performance`。

### synonym —— 同义词

`disk` → `storage`、`volume`。语义等价，可以互换。
当前实现的 `terms[].source: synonym` 覆盖的就是这一类。

### derived —— 语义联想

**这是当前实现缺的一类，也是最有价值的一类。**

`performance` → `buffer`、`async`、`cache`、`batch`、`pool`

`buffer` **不是** `performance` 的同义词。它们的关系是：

> 代码在处理性能问题时，通常会出现 buffer 这样的东西。

同理 `disk` → `swap`、`flush`、`sync`、`sector`：这些不是「磁盘」的同义词，
是磁盘相关代码的**典型词汇**。

这类词的特点：

| | synonym | derived |
|---|---|---|
| 关系 | 语义等价 | 共现指示 |
| 在代码里出现的频率 | 低 | **高** |
| 单独命中的可信度 | 高 | **低**（`cache` 可能跟性能无关） |
| 作用 | 提高召回，不太伤精度 | **大幅提高召回，明显伤精度** |

所以 derived 词**必须配合别的条件用**——要么和同单元的其它词一起加权，
要么靠 `hop` 的图约束把它锚住，要么最后交给 `intent` 复核。
单靠一个 derived 词就下结论，噪音会淹没结果。

这也解释了为什么 `Term` 要带 `weight` 和 `source`：
它们不只是给人看的注释，是排序和阈值判断的输入。

### 派生怎么做

编译期由 LLM 完成，输入是单元名 + 原始查询上下文，输出是带来源和理由的词表：

```json
{
  "unit": "performance",
  "intent": "和性能表现有关的代码",
  "terms": [
    {"value": "performance", "source": "literal",  "weight": 1.0},
    {"value": "perf",        "source": "synonym",  "weight": 0.9},
    {"value": "latency",     "source": "synonym",  "weight": 0.8},
    {"value": "buffer",      "source": "derived",  "weight": 0.5,
     "reason": "缓冲是减少 IO 次数的常见手段"},
    {"value": "async",       "source": "derived",  "weight": 0.5,
     "reason": "异步化是常见的性能手段"},
    {"value": "batch",       "source": "derived",  "weight": 0.4,
     "reason": "批处理减少单次开销"}
  ]
}
```

`reason` 不是装饰。它有两个实际用途：结果不对时能看出是哪个联想跑偏了；
以及人工审查词表时能快速判断该不该删。

### 派生要不要看代码库

上面的派生是**语料无关**的——只靠通用知识。还可以再走一步：
拿单元的词去项目词表里找共现词。

```
"buffer" 在本项目里常和 "flush"、"sink"、"drain" 一起出现
   → 把这三个也加进 performance 单元
```

这一步能显著提高召回，因为它用的是**这个项目自己的命名习惯**。
项目词表和共现数据当前实现里已经有了（`term_project_vocab.json`、
`enhanced_call_chain_corpus.json`、ICF 通道），可以直接接。

代价是引入了对索引的依赖，编译不再纯粹。**建议做成可选的第二阶段**：
先出语料无关的词表，需要时再用项目语料扩一轮。

## 匹配到哪些字段

一个词可以在多个位置命中，可信度不同：

| 字段 | 说明 | 相对权重 |
|---|---|---|
| `name` | 标识符名 | 最高 |
| `signature` | 签名（含参数名、类型） | 高 |
| `container` | 所属类 / 包 | 中 |
| `doc` | 文档注释 | 中 |
| `body` | 函数体文本 | 低，噪音大 |

`match` 默认匹配 `name`，其余靠参数打开。这个默认值是有意的：
`body` 匹配几乎总能命中，但几乎总是噪音。

## 分词与缩写扩展

代码标识符不是自然语言，`flushBuffer` 要先拆成 `flush` + `buffer` 才能匹配。
当前实现的 `CodeTokenizer`（驼峰 + sentencepiece）和 `AbbreviationGenerator`
（前缀 / 辅音骨架 / 子序列）解决的正是这个问题，直接复用。

方向上有个区别值得注意：

- **缩写扩展**是把查询词展开成代码里可能的写法：`buffer` → `buf`、`bfr`
- **分词**是把代码标识符拆成词：`flushBuffer` → `flush buffer`

两者是同一件事的两个方向，实现上通过倒排索引（`标识符 → 缩写子词`）连起来。
新设计不改这一层，只是把它的产物按单元组织。

下一篇：[05 算子](05-operators.md)。
