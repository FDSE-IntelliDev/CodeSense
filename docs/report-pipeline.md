# CodeSense 流水线报告

> 记录截至 2026-08-01 的实现状态、所有实测数字、以及尚未解决的问题。
> 设计动机与推导见 `docs/design/01..10`；本文只讲**做成了什么、量到了什么**。

---

## 1. 现在是什么

一条自然语言查询 → 一段针对代码库的查询脚本 → 带证据的结果。

```python
from codesense import Project

p = Project.build("~/src/netty", index_dir="~/.codesense/netty")
p = Project.open("~/.codesense/netty")
print(p.search("写缓冲积压时的背压").explain())
```

命令行先 `init` 再 `query`，索引像 `.git` 一样落在仓库里，之后从任何子目录都能找到：

```bash
cd ~/src/netty
codesense init                          # 建 ./.codesense/
codesense query "写缓冲积压时的背压"       # 不用再指路径
codesense info                          # 当前作用域下是哪份索引
```

规模：`codesense/` 6,888 行，`tests/` 4,060 行（492 个测试），`scripts/` 1,122 行（研究脚手架）。
重写前的实现归档在 `legacy/`（19 935 行），不参与构建、lint、测试。

---

## 2. 分层

```
codesense/
├── lang/              怎么"读"一门语言        ← 扩展点
│   ├── base.py          Language 协议 + Declaration / Invocation / AnnotationUse
│   └── java/            scanner（tree-sitter）/ annotations / frameworks（Spring、JPA、JUnit）
├── text/              词怎么工作（语言无关）
│   ├── split.py         Splitter：delimiter + Viterbi
│   └── corpus.py        把声明变成训练语料
├── indexing/          怎么"建"产物
│   ├── pipeline.py      编排（只管顺序）
│   ├── postings.py      PostingTable，声明 → (term, field)
│   ├── graph.py         GraphBuilder + TypeTable，contains / calls
│   └── grounding.py     词表接地（lexical / vectors / finetune）
├── ql/                查询层，**契约上只用标准库**
├── llm/               模型适配器（llm → ql 单向依赖）
├── index.py           产物"是"什么（存取 + to_context）
├── search.py          三条查询路径
└── project.py         主类
```

依赖方向单向：`indexing` 用 `lang` / `text` / `ql` 的类型，反过来都不认识。
`tests/contract/test_ql_isolation.py` 机械强制 `codesense/ql/` 不导入任何其它子包、
不依赖任何第三方库、import 无副作用。

### 加一门语言

写一个 adapter，四个描述属性加一个 `scan()`：

```python
class GoLanguage:
    name = "go"
    file_globs = ("*.go",)
    skip_parts = ("/vendor/",)
    indexed_kinds = frozenset({"function", "struct"})
    container_kinds = frozenset({"struct"})
    def scan(self, source) -> Sequence[Declaration]: ...
    def expansions(self): return {}       # 没有框架关系就返回空

LANGUAGES.register(GoLanguage())
```

`tests/unit/lang/test_registry.py` 在测试文件里现场定义一个 `ToyLanguage` 并索引它，
**没动 `codesense/indexing` 一行**。这条测试哪天要改 indexing 才能过，就说明扩展点失效了。

注册失败是记日志跳过而不是抛：一台机器没装 tree-sitter，不该连带别的语言都用不了。

---

## 3. 索引：netty 实测

一次扫描 21 秒，42 221 符号、603 733 posting、120 322 边。

| 维度 | 数据 |
|---|---|
| 符号种类 | method 25 868 / field 9 928 / constructor 3 395 / class 2 416 / interface 412 / enum 187 |
| posting 域 | container 148 975 / doc 145 954 / signature 111 917 / name 106 017 / modifier 54 546 / annotation 34 228 / annotation_arg 2 096 |
| 边 | calls 80 257 / contains 40 065 |
| 边来源 | typed_receiver 48 379（0.9）/ derived_container 40 065（1.0）/ name_match 31 878（≤0.6） |
| 词表 | 7 148，其中 4 924 过得了 ICF 门槛 |

**域要分开。** 符号名里的 `buffer` 和 javadoc 里的 `buffer` 强度完全不同，合并就丢掉了
「关于缓冲区的类」和「顺口提了一句的类」之间唯一的区分信号。

**调用边永远不是 1.0。** 这不是真的调用图，标成 1.0 会让 `hop` 返回不存在的路径。
把 AST 里免费的类型信息用足，唯一解析率从 13% 提到 44%——虚方法分派、泛型、跨库调用
仍然处理不了，那些确实要 CodeQL。

`contains` 是性价比最高的一步：数据本来就在 `container` 字段里，物化成边之后
孤点从 33% 掉到 2%。

---

## 4. 分词：Viterbi 那层

先验证再实现。Ronin（srctoolkit）实测：

```
netlink   -> net link      ✓        iostat  -> iostat     ✗
strlen    -> str len       ✓        nfsd    -> nfsd       ✗
vfsmount  -> vfs mount     ✓        printk  -> printk     ✗
filesystem-> file system   ✓        kmalloc -> kmalloc    ✗
```

所以第二层是必要的。**不需要训练**：一元代价直接来自项目自己的词表——delimiter
已经成功切出来的词，计数即可。一个到处写 `io` 和 `stat` 的库因此告诉分词器
`iostat` 是两个词；一个从不单独用 `stat` 的库就保持整体，这对那个库才是对的。

三道闸：

1. 每个 piece 必须在词表里
2. 不许单字母（否则 `nfsd → nfs + d`）
3. **切开的总代价要比整体低一个 margin**——这条让 `interface` 不会变成 `inter + face`

时机：跑在扫描**之后**，因为词表要整个仓库扫完才有。落在 postings 上做，不用二次解析——
postings 本来就记着哪些符号带 `iostat`，碎片直接继承。

netty 上切出 **78** 个：

```
iouring   -> io uring        （netty 的 io_uring transport）
iobuffer  -> io buffer
httpmethod-> http method
abstractchannel -> abstract channel
```

错 2 个：`belong → be long`、`intend → int end`。约 97% 准确。两个都是**多加**一条
posting 而不是删掉，代价是噪音不是丢答案。

`MIN_PIECE` 定成 2 而不是 3：3 会把 `io` 挡掉，正好废掉 `iostat` 这个核心例子。
2 是安全的，因为每个 piece 都必须已在词表里。

---

## 5. Embedding：做了，但要如实说

三档策略：`lexical`（无依赖，默认）/ `vectors` / `finetune`。`finetune` 又分为
默认的 `lightweight`、子进程加载完整模型的 `full_force`，以及需要危险确认的
`warn_full`。前两种最终训练紧凑 Word2Vec，保存到 `.codesense/embedding/`；查询期
只读接地表，永远不加载 embedding 模型。

本节后面的 FastText 近邻数字来自轻量化改造前的实验，保留作为算法判断依据，不代表
当前默认部署成本。

### 第一次跑出来是垃圾

5 890 个键全是 `she → then`、`people → they`、`ability → alt` 这种。三个真 bug：

1. **没在构建期按 ICF 过滤目标词。** 而 satisfier 在**查询时**本来就会丢弃低于
   `icf_floor` 的扩展——所以生成它们纯属浪费，只会被丢掉。现在构建期用同一个阈值，
   有测试钉住两边一致。
2. **通用词表取"最高频 N 个"**，那个头部全是虚词。改成取频率**带**（跳过前 3 000）。
3. **打分函数平方压缩**，把 cosine 0.8 以下全压到 0.01 以下——那种权重乘进排序等于没有。
   改成线性，加最低分截断。

修完 5 890 → 796 个有效条目。

### 但结果印证了设计文档自己的警告

向量给的主要是**形态变体**，不是同义词：

```
messages -> message      drivers -> driver      printed -> print
versions -> version      lists   -> list        causing -> cause
technique-> method   ← 少数真语义的
```

这有用（词形归一化），但别指望它解决 `backpressure → watermark`。

### 一个值得记的结构性发现

ICF 门禁会把 `pool`(298/996)、`connection`(245) 这些**领域核心词**挡在扩展目标之外——
因为在一个聚焦的代码库里，领域词天然高频。这和之前 netty 上 `buf`(15.4%) 被丢是同一个病根。
**小项目上向量接地因此几乎使不上劲。**

### lexical 规则的质量分布（netty，8 321 条）

| 规则 | 条数 | 分数 | 质量 |
|---|---|---|---|
| prefix | 3 848 | 0.75 | 好 |
| abbrev（辅音骨架） | 360 | 0.75 | 好 |
| subseq | **4 113** | 0.50 | **差** |

`subseq` 占比最大也最烂：`abandoned → add`、`ability → alt`。这些分 0.5，且目标词
多半过不了 ICF 门禁，所以实际伤害有限——但这条规则**噪音多过信号，该再收紧一次**。
（已经加过一道：缩写侧最长 5 字符，去掉了 `allocation → action`。）

---

## 6. 查询：三条路径

| 路径 | R@100 | 说明 |
|---|---|---|
| `codegen` | 55–58% | 把算子 spec 和**带 df 的词表**给模型，它自己写脚本。唯一能表达控制流的 |
| `planned` | 50–52% | 模型只做 NLP，统计定结构与顺序。计划可在执行前检视 |
| `lexical` | — | 不用模型。查询自己的词 + 接地表。没有 key 时跑的就是它 |

任何一条路径失败都**降级**到 lexical 而不是抛异常——一次 401 就让搜索死掉，
比悄悄少做一点更糟。

### 一次查询的实际过程（netty，"pooled buffer allocation and recycling"）

```
1  eval_unit            10 790 行   244 ms
2  reach（凝聚种子）        832 行    17 ms   477 个落在邻域
3  narrow（加权）            60 行    <1 ms
```

**图邻近性是加权不是过滤。** `reach` 产出的 Frag 没有分数，用它替换候选集
等于把词法分数全丢了。

每条结果都带证据：`recycle←recycler(prefix)@name` 说明是哪个词、经什么规则、命中哪个域。

---

## 7. Benchmark

8 条查询、3 个真实工业项目（netty / HikariCP / spring-petclinic），指标是**召回率**
而非准确率：gold 集只求确凿不求完备。

**LLM 运行间方差和要测的效应同量级**（同一条查询、temperature=0，两次能差 20 个点），
所以派生结果一律缓存，跨运行对比必须固定住它，否则比的是噪音。

### 翻译成英文提示词有没有退化

受控 A/B：同代码、同索引，只有 prompt 语言不同，各 3 次。

| arm | 中文 prompt | 英文 prompt | delta | 自身极差 |
|---|---|---|---|---|
| grounded | 51% | 46% | −4 | 4–9 pts |
| graph | 50% | 52% | +2 | 11–14 pts |
| planned | 51% | 50% | −1 | 5–9 pts |
| codegen | 57% | 58% | +1 | 2–4 pts |

全部落在自身采样极差内。**没有退化。**

> 中途出过一次假警报：拿英文结果和记忆里的旧数字比，grounded/graph 看着掉了 9–11 点。
> 查下去发现旧缓存的 key 集合对不上——那批数字是在**另一个 index build** 上跑的，不可比。
> 结论：跨运行比较必须确认索引同源。

### 重构有没有改坏东西

同一份旧索引 + 同一份缓存跑 3 次，96 个缓存条目**全部命中**（零 LLM 调用、零方差），
结果与重构前**逐位相同**。纯代码对比，无退化。

### 新流水线（含 Viterbi）有没有影响召回

| arm | 旧索引 | 新索引 | delta | 新方差 |
|---|---|---|---|---|
| generic | 38% | 39% | +2 | 3 pts |
| grounded | 46% | 47% | +1 | 6 pts |
| graph | 52% | **56%** | +4 | 7 pts |
| planned | 50% | 52% | +2 | **0 pts** |
| codegen | 58% | 55% | −3 | 3 pts |

全部落在采样极差内。没有退化；graph / planned 略升但还不能算显著。
`planned` 三次都是 52%，零方差。

---

## 8. 还没解决的

### netty-backpressure：六条路径、三次采样，全 0%

查询问「写缓冲积压时的背压与流量控制」，gold 是 `WriteBufferWaterMark`、
`ChannelOutboundBuffer`、`isWritable`、`incrementPendingOutboundBytes`、`setUnwritable`。

netty 管这个叫 **watermark / writability**，字面词一个都对不上。模型走的是
"flow control / congestion" 方向，语义没错但落在 HTTP/2 和 QUIC 的流控上。

这**正是第 09 章 grounding 想解决的问题，而现在一条都没找到**。
词表里 `watermark` df=2（icf 0.935）、`writability` df=110（icf 0.559），
两个都在、都高度可区分——所以问题不在索引，在从「背压」到这两个词的那一跳。

### 四条缺口测试（`tests/integration/test_handwritten_queries.py`）

| 缺口 | 状态 |
|---|---|
| gap1 注解没灌进真实索引 | **抽取已实现**，只差索引构建调用 |
| gap3 修饰符没灌进真实索引 | **抽取已实现**，同上 |
| gap2 没有字段读写边 | 要新写 |
| gap4 没有数据流边 | 唯一需要**新建分析能力**的一项 |

gap1 / gap3 是接线不是实现，性价比最高。

### 领域概念 → 项目命名，这一跳还是断的

和上一条同源，CLI 上跑 netty 又撞到一次。查「零拷贝的文件传输」，
前 5 条全是 HTTP 的 `setContentTransferEncoding`，真正对的
`FileRegion` / `DefaultFileRegion` / `transferTo` 一个没进。

排查结论：**不是索引、ICF 或词表截断的问题**。

| 词 | df | icf | 全表排名 | 是否送给模型 |
|---|---|---|---|---|
| `region` | 224 | 0.492 | 358 | **是** |
| `transfer` | 86 | 0.582 | 711 | 是 |
| `sendfile` | 2 | 0.935 | 4824 | 否（超出 1200 截断） |

`region` 就在模型眼前，连同它的 df，模型仍然选了 `transfer`。
**把词表给模型是必要条件，不充分**——它还得知道这个项目把这个概念叫什么。
没有任何词法规则能把「zero copy」连到「region」，而向量给的是形态变体。

### 其它

- `subseq` 接地规则噪音多过信号，该再收紧。真实查询里已经现形：
  `recycling → ring`（`ring` 是 io_uring 的 BufferRing），lexical 前 6 条
  有 3 条被它拖进无关的 io_uring 类
- `lexical` 路径只能处理与代码库同语言的查询——中文查询必然 0 命中，
  这是正确行为，但要写在文档里
- ICF 门禁把领域核心词挡在扩展目标外（小项目尤甚），这个张力还没有好答案
- `LlmConfig` 默认端点是 dashscope，而常用的是 OpenAI key，不传 `--base-url` 会 401

---

## 9. 可视化

`docs/artifacts/codesense-internals.html`（已发布为 artifact）。
索引内部构成、ICF 门槛、Viterbi 格子、查询漏斗、证据链、模型真实生成的脚本，
每个数字都是 netty 上实测的。
