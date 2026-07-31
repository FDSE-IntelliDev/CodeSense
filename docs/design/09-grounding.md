# 09 落地到具体项目：分词、嵌入与扩展索引

查询单元需要一批能在代码里定位的词：`performance` → `buffer`、`async`、`cache`。
但项目里可能写 `buf`，注解可能是自定义的 `@AppCache` 而不是 `@Cacheable`。

**这一章讲怎么在不调用大模型的前提下拿到这批词。**

> **约束（设计前提）**：**不增加 LLM 调用次数**。
> 每个单元单独过一次大模型、或者为扩展再开一次往返，
> 延迟和 token 成本都不可接受。
>
> 但查询分解成单元**本来就有一次调用**——词的派生可以搭在这次调用里，
> 边际成本只是输出 token，没有额外往返。见[第六节](#六第一跳搭在已有的那次-llm-调用上)。
>
> 其余能力必须离线预计算、查询时纯查表。第三处 LLM 是 `intent` 算子
> （[05](05-operators.md)，刻意放在最后、候选最少时）。

---

## 一、两个测量结果决定了方案

### 1. 项目词表 86% 是普通英文词

样例项目（youlai-boot，1718 个符号）实测：

| | 词表大小 | 是英文词 | 非词（缩写/专名） |
|---|---|---|---|
| 调用链语料 | 302 | **261（86%）** | 41 |
| 符号切分单元 | 462 | 382（83%） | 80 |

非词那 41 个是：`auth`、`dict`、`config`、`sms`、`dep`、`vo`、`impl`、
`codegen`、`captcha`、`addr`、`sys`、`jti`……

**这直接给出了分工**：

| | 负责什么 | 覆盖 |
|---|---|---|
| **Embedding** | 语义邻近：`performance` → `cache`、`flush`、`async` | 86% |
| **Lexical rules** | 正字法变体：`buffer` → `buf`、`manager` → `mgr` | 14% |

两者**几乎不重叠**，所以不是二选一，是串起来用。这也正是
「不指望 embedding 找同义词，只指望它找语义相似的，再结合 lexical rules」
的量化依据。

### 2. 单个项目的语料训不出可用的向量

| 量 | 实测 |
|---|---|
| 增强调用链语料 | 154,686 条 / **103 万 token** |
| 语料词表 | **302 个词** |
| 当前实现 | `FastText(vector_size=128, min_count=1, epochs=100)`，**从零训** |

302 个词配 128 维、跑 100 轮、`min_count=1`——参数量远超语料能支撑的信号量，
学到的主要是这个项目的调用链拓扑，不是词义。

更关键的是**有些词根本不在语料里**。实测：

```
dept    出现 9 次    department    出现 0 次
dict    出现 6 次    dictionary    出现 0 次
config  出现 4 次    configuration 出现 0 次
```

在单项目语料上训的模型，词表里没有 `department`，
所以「查 `department` 的近邻找到 `dept`」这条路走不通。

---

## 二、全局预训练 + repo 微调

### 为什么全局预训练能解决上面那个问题

`dept ≈ department` 在**一个** repo 里学不到——因为 `department` 出现 0 次。
但在**很多** repo 里学得到：有的项目写 `department`，有的写 `dept`，
两者都出现在 `user`、`role`、`employee`、`org` 附近。

**跨项目的语料把同一概念的不同表层写法放进了同一个上下文分布。**
这是单项目语料结构性缺失的信号，换语料就有了。

所以全局模型不只是「词表大」，它**本身就编码了缩写与全称的对应关系**——
这正是我们最需要的那部分知识，而且不用 LLM。

### 现成的预训练模型：三个候选，没有一个全中

| | 词级静态向量 | 可续训 | 有子词 | 代码域词义 | 体积 |
|---|---|---|---|---|---|
| **fastText `cc.en.300.bin`** | ✓ | **✓ 原生** | **✓** | ✗ | 4.5G(gz) / 7G |
| **SO_vectors_200** | ✓ | △ 只能 seed | ✗ | ✓ SE 领域 | 1.5G |
| **code2vec java14m tokens** | ✓ | ✗ | ✗ | **✓✓ Java 标识符** | ~2G |
| CodeBERT / UniXcoder | ✗ 上下文 BPE | — | BPE | ✓✓ | — |

```
https://dl.fbaipublicfiles.com/fasttext/vectors-crawl/cc.en.300.bin.gz
https://doi.org/10.5281/zenodo.1199620          # SO_vectors_200.bin
https://s3.amazonaws.com/code2vec/model/java14m_model.tar.gz
```

**只有 fastText 能当底座。** 原因是格式：`load_facebook_model()` 拿到的是
**完整模型**（输入+输出权重都在），`build_vocab(update=True)` + `train()`
就是标准续训。另两个只有输入向量——SO_vectors 是 word2vec C 格式，
读出来是 `KeyedVectors`；code2vec 的 `tokens.txt` 是纯文本向量，
而且它的训练目标是「从 AST 路径预测方法名」，没有可复用的语言模型。
没有输出权重就没法继续训。

### 实测 cc.en.300（2M 词表 / d=300）

> 下面全部是在本项目词表上跑出来的真实数字，不是估计。
> 探针脚本：`scripts/probe_embeddings.py`（`coverage` / `senses` / `abbrev` 子命令）。

**a. 覆盖率 99%——比预期好得多。**

| | 词表大小 | 在 cc.en.300 里 | OOV |
|---|---|---|---|
| 调用链语料 | 302 | **299（99%）** | `result<t>`、`data<t>`、`mybatis` |
| 符号切分单元 | 462 | **457（99%）** | `aliyun`、`minio`、`starttls`、`redisson`、`mybatis` |

OOV 全是库名和品牌名。但**这个 99% 是虚的**——
「在词表里」不等于「向量能用」：

| 词 | 项目频次 | cc.en.300 排名 | 通用近邻 |
|---|---|---|---|
| `scopes` | 20,501 | 39,299「训得充分」 | Leupolds, 3-9x, reticles ← **步枪瞄准镜** |
| `perms` | 12,103 | 134,154 | perming, permed, relaxers, hair ← **烫发** |
| `oss` | — | 110,085 | om, när, gør, göra, som ← **瑞典语「我们」** |
| `vo` | 1,689 | 97,994 | ne, ro, ri, pe, si, va ← **纯噪音** |
| `jti` | 136 | 1,532,899 | i.o, ,o, ,c, iuw ← **垃圾** |

`scopes` 是项目第 11 高频词、排名 3.9 万属于「训得充分」——训的是瞄准镜。
**真正的问题不是覆盖率，是词义错配**，见[第七节](#七微调仍然必需但目标要说清)。

**b. 缩写↔全称：确实编码了，而且不是拼写像。**

fastText 有子词，`dept` 和 `department` 共享 n-gram `<de`/`dep`，
所以余弦会被正字法重叠本身抬高。**必须拿同前缀但语义无关的词作对照**：

| 缩写 | 全称 | cos | 同前缀干扰词 | cos | |
|---|---|---|---|---|---|
| `dept` | department | **0.749** | depot | 0.301 | ✓ |
| `auth` | authentication | **0.673** | authority | 0.205 | ✓ |
| `config` | configuration | **0.641** | conflict | 0.139 | ✓ |
| `mgr` | manager | **0.579** | mugger | 0.064 | ✓ |
| `addr` | address | 0.578 | adder | 0.236 | ✓ |
| `dict` | dictionary | 0.520 | diction | 0.296 | ✓ |
| `pwd` | password | 0.515 | powder | 0.238 | ✓ |
| `buf` | buffer | 0.468 | buffalo | 0.219 | ✓ |
| `msg` | message | 0.442 | massage | 0.126 | ✓ |
| `impl` | implementation | 0.410 | imply | 0.209 | ✓ |
| `cfg` | configuration | 0.367 | cog | 0.056 | ✓ |

**11/13 通过**（随机词对基线：均值 0.074，95 分位 0.222）。

这直接证实了本节开头的判断——**跨语料确实编码了缩写与全称的对应**，
而且**不需要任何训练就能用**。第二跳（规范形 → 项目表层写法）今天就能做。

> 两个没通过的是测试设计问题不是模型问题：`req`↔`request` 输给了
> `reque`（Common Crawl 词表里的垃圾词条），`dto`↔`object` 本来就不是
> 截断关系（`dto` 是 data transfer object 的首字母缩合）。

**c. 但单元扩展完全做不到——这才是真正的短板。**

```
performance 的 top-15 近邻:
  perfomance, peformance, performace, performance.The, perfromance,
  performance.This, performanc, preformance, performance.But, ...
```

**全是拼写变体和分词垃圾，零语义信息。** `disk` 一样。`io` 更糟——
`digr, lu, eio, uiv, iini, wnu, aiiu` 完全是噪音（两字符的词子词哈希撑不住）。

即便绕开 top-k 直接看两两余弦，期望词也都在噪音水平：

```
performance:  cache 0.18   buffer 0.14   async 0.17   pool 0.08
              （随机基线 95 分位 = 0.222，这些还不如随机）
              仅 throughput 0.43 / latency 0.32 有效
disk:         storage 0.43  sync 0.27  sector 0.26  flush 0.19
```

道理很直白：自然语言里 "performance" 挨着的是 "improve"、"review"，
不是 "buffer"。**这个关联只存在于代码语料里。**

### 结论：两跳的来源不同

| | 靠什么 | 现状 |
|---|---|---|
| **第一跳** unit → 规范词<br>`performance` → `cache`/`buffer` | **必须用代码语料训**（0/7 实测确认） | 要做 |
| **第二跳** 规范词 → 项目写法<br>`buffer` → `buf`，`department` → `dept` | cc.en.300 现成的 + lexical rules | **今天就能用** |

**这比原来的设计更省事**：第二跳不需要微调，直接查预训练向量配合正字法规则
即可；代码语料的训练只服务于第一跳。两件事可以分开做、分开验证。

### 两个必须加的护栏

**1. 近邻必须先滤掉形态变体。** fastText 的 top-k 被拼写变体霸占
（`pool → pools, pool.The, pool.This`），直接用等于什么都没扩。
建表时要先按编辑距离/词干去掉变体，再取前 N。

**2. 长度 ≤2 的单元不可信。** `io` 的近邻是纯噪音。这类单元
（`io`、`vo`、`id`、`ts`）只能走 lexical/精确匹配，不能走向量扩展。

### 另两个模型的定位

- **code2vec tokens** 的词表就是 Java 标识符子词，`vo`、`impl`、`dto` 都有
  真实训出来的向量——正好补 cc.en.300 那 5 个 OOV 和短单元的空缺。
- **SO_vectors** 做**评估参照**：同一批词在两个独立模型里的近邻是否一致，
  可以在无标注的情况下交叉验证。

### 全局语料怎么建

**别为此搭 LSP 流水线。** 全局模型要的是标识符共现，不是精确调用图——
直接拿现成的 Java 源码数据集，按方法/文件切「句子」就够：

```
CodeSearchNet (Java 子集, ~50 万方法)   ← 推荐起点，HuggingFace 直接可取
The Stack v2 (BigCode)                  ← 更大，但磁盘代价高
java-large (code2vec 的 9500 个项目)     ← 与 code2vec 词表同源
```

每个方法体里的标识符切成单元、去重后作为一个「句子」，
不需要解析调用关系。50 万方法大致能到 10⁷~10⁸ 量级 token、10⁵ 量级词表，
足以支撑 d=200~300。这是**一次性**成本，所有项目共享。

**上下文定义的不一致要记一笔**：全局用方法内共现，微调用调用链，
两者的窗口语义不同（前者是「同一段代码里出现」，后者是「调用路径上相邻」）。
这不致命——都是「标识符在同一代码单元里共现」，调用链只是更长程的版本——
但它会体现在锚点漂移上，所以下面那个漂移指标要认真看。

### 微调：别把预训练空间搞坏

103 万 token 的微调很容易把预训练的结构冲掉。三条措施：

**a. 低学习率、少轮次。** 不是 `epochs=100`，是 2~5 轮、LR 比预训练低一个量级。
微调的目的是**移动**这个项目的词，不是重学词义。

**b. 锚定正则。** 对预训练里已有的词加 `λ‖v − v_pretrain‖²`，
让它们只能在原位附近微调。目标是「`user` 在这个项目里稍微偏向 `role`」，
不是「`user` 跑到别的地方去」。

**c. 用 FastText 的子词，别用 Word2Vec。** 微调时 repo 里的新词
（`vo`、`jti`、`codegen`）不是随机初始化，而是由字符 n-gram 组合出来——
天然落在预训练空间里的合理位置。这也是**免费的正字法桥**：

```
buf     n-grams: <bu, buf, uf>
buffer  n-grams: <bu, buf, uff, ffe, fer, er>     → 有重叠 ✓
mgr     n-grams: <mg, mgr, gr>
manager n-grams: <ma, man, ana, nag, age, ger, er> → 零重叠 ✗
```

**前缀截断型缩写 FastText 免费搞定，辅音骨架型搞不定**——
后者留给 lexical rules，见第四节。

### 用锚点词监控漂移

微调完必须验证空间没坏。现成的检查：那 **261 个既在项目里、
又在预训练词表里的英文词**就是锚点。

```
drift = mean( 1 − cos(v_finetuned(w), v_pretrained(w)) )   w ∈ 261 个锚点
```

- drift 很小（<0.1）：微调基本没起作用，加大 LR 或轮次
- drift 适中（0.1~0.3）：**目标区间**，项目特有的关联学到了，全局语义还在
- drift 很大（>0.5）：预训练空间被冲垮了，降 LR / 加大 λ

这个指标不需要任何标注，训完就能算。

### 如果一定要分开训再对齐

另一条路是全局、repo 各训一个，再用 Procrustes 把 repo 空间旋转到全局空间
（261 个共享词作对齐锚点，`min‖X_repo·W − X_global‖_F`，`W` 正交，SVD 闭式解）。

**但实测数据不支持这条路**：

| 维度 | 锚点/维度 | |
|---|---|---|
| d=50 | 4.7x | ✓ |
| d=100 | 2.3x | ✗ |
| d=128（当前实现） | 1.8x | ✗ |
| d=300 | 0.8x | ✗ |

234 个高频锚点要拟合一个 d×d 的正交矩阵，d 超过 50 就开始过拟合对齐本身。
**而继续训练（微调）根本不需要对齐矩阵**——它一直在同一个空间里。

所以：**推荐微调，不推荐分开训再对齐。** 上面这张表是这个选择的依据。

---

## 三、统一分词

Embedding 的词表由分词决定，所以分词要先定。

### 用 `srctoolkit` 的 `Delimiter.split_camel`，不要自己写

项目已经依赖 `srctoolkit`，且 `codesense/tokenizer/tokenizer_core.py:37`
就是 `return Delimiter.split_camel(word)`。**不要再写第二个切分器**——
两个行为不一致的切分器意味着索引侧和查询侧对不上。

更要紧的是：**它底层是 `ronin.split`（Spiral 包），
不是驼峰正则，而是一个基于约 4.6 万个 GitHub Java 项目频率表的标识符切分器，
本身就处理同大小写的 run-on 复合词。**

也就是说**本节原本要设计的 Viterbi，已经在依赖里了，而且训练语料
比本项目能提供的多几个数量级。不用建，也不用训。**

### 实测：Java 上很好，系统代码上不行

Java 项目上它表现很好——run-on 能切（`bufmgr`→buf|mgr、`spinlock`→spin|lock、
`getattr`→get|attr），而且不过切（`config` 保持整体，**朴素词典 Viterbi
一定会切成 `con|fig`**）。在本项目上与朴素驼峰正则结果几乎一致（459 vs 462 个单元）。

**但换到 Linux kernel 风格的标识符上只有 68%（28/41）**：

| 失败模式 | 数量 | 例 |
|---|---|---|
| **该切不切** | 11 | `iostat`、`kmalloc`、`vmalloc`、`softirq`、`rwlock`、`runqueue`、`readahead`、`sockfd`、`vmstat`、`blkmq`、`inode` |
| **切错位置** | 1 | `dentry` → **`den\|try`**（比不切更糟） |

根因在对照组里一目了然：

```
IOStat  → io|stat  ✓        io_stat → io|stat  ✓     ← 边界显式时 100% 正确
iostat  → iostat   ✗                                  ← 只在实心串上失败
```

**Ronin = 显式边界（完美）+ 对实心串的频率猜测（Java 偏置）。**
它的频率表挖自约 4.6 万个 GitHub **Java** 项目，对内核词汇零暴露。
Java 里实心串罕见，所以它看着很好；C/内核里实心串是常态，就掉到 68%。

> 这对本设计是要害问题——贯穿全文的例子 `io performance on disk`
> 正是系统代码，而 `iostat` 恰好是它切不对的那类。

### 换频率表这条路走不通（实测）

`ronin.init(frequencies=...)` 确实接受自定义频率表。但实测**换了更糟**：

```
iostat:  默认表 → iostat        换成内核词表 → ios|tat      ✗ 更差
```

因为 Ronin 除频率表外还有词典检查和 6 个超参
（`camel_bias=8.63`、`recognition_bias=3.6e-07`、`short_min_freq=286540`……），
**全部按官方那张大表的量级标定**。换一张小表就得连这 6 个参数一起重标，
而 `init` 的文档自己写着生成全局表 "not a trivial undertaking"。

**结论：不要试图改造 Ronin，在它外面加一层。**

### 方案：三段式，Ronin 只做第一段

```
标识符
  │
  ├─ 1. Delimiter.split_camel        显式边界，100% 可靠
  │
  ├─ 2. 领域词表 Viterbi 二次切分      只处理「留成实心且长度>4」的单元
  │                                   修「该切不切」
  │
  └─ 3. 覆盖表                        修「切错位置」+ 库名
```

**第 2 段实测修好 9/10，弄坏 0 个**：

| 词 | 仅 Ronin | + 二次切分 |
|---|---|---|
| `iostat` | iostat | **io \| stat** |
| `kmalloc` | kmalloc | **k \| malloc** |
| `softirq` | softirq | **soft \| irq** |
| `blkmq` | blkmq | **blk \| mq** |
| `sockfd` | sockfd | **sock \| fd** |
| `readahead` | readahead | **read \| ahead** |
| `config` | config | config（未被破坏） |
| `dentry` | **den\|try** | den\|try（修不了，见下） |

**只在 Ronin 留成实心的单元上跑，所以它不可能破坏 Ronin 已经切对的结果**——
这是「弄坏 0 个」的结构性原因，不是运气。

**这一段的词表是免费的**：内核大量使用 snake_case，
`blk_mq_init`、`io_uring`、`sock_fd_lookup`、`soft_irq` 直接给出
`blk`、`mq`、`io`、`sock`、`fd`、`soft`、`irq` 及其频次。
**同一个代码库里边界清楚的标识符，就是边界不清楚那些的标注数据。**
这一段不需要训练，只需要数词频（一元文法 + DP，`O(n·L)`，微秒级）。

### 第 3 段：二次切分修不了的那类

`dentry` → `den|try` 说明了结构上的限制：**二次切分只能修「该切不切」，
修不了「切错位置」**——Ronin 已经把它切开了，第 2 段根本看不到它。

这类只能靠覆盖表，来源两处：

- **库/品牌名**：从 `pom.xml` / `Cargo.toml` / `go.mod` 的依赖列表生成
  （`redisson`→redis|son、`minio`→min|io 是 Ronin 在本项目上唯一的系统性错误）
- **领域专名**：`dentry`、`inode`、`kobject` 这类，人工列，几十个量级

### 兜底：超集索引

三段之后仍会有残留错误（`dentry` 就是），**所以不要依赖切分绝对正确**——
每个标识符按多种分解形式同时入索引，查询侧同样处理，任一分解对上即命中。
具体形态见[第五节](#超集索引一个标识符发多种形式)。

### 判定标准是一致性，不是语言学正确

这条决定了上面所有取舍：**目标不是切得对，是两边切得一样。**

```
查询   "buffer manager"  ──split_camel──►  [buffer, manager]
标识符  bufMgr / bufmgr   ──split_camel──►  [buf, mgr]
```

`readahead` 保持整体还是切成 `read|ahead` 都可以接受——
只要查询侧和代码侧走的是**同一个函数**，倒排就能对上。

而超集索引比这个要求更弱：**两边都发多种形式，连一致都不必强求。**

**所以这里要的是一个确定性函数，不是一个学出来的模型。**
`Delimiter.split_camel` 带 `lru_cache`，确定性和性能都有了。

反过来，全局预训练语料也必须用同一套切分——
否则全局模型的词表和项目的词表对不上，微调就没有共同的锚点。

---

## 四、Lexical rules：补 embedding 补不到的 14%

Embedding 给的是**语义邻近**，给不了**正字法变体**。`buf` 和 `buffer`
语义上确实近（如果两者都在全局语料里出现过），但 `vo`、`jti` 这类
项目专名，全局模型也没见过几次。

对这些用规则：

| 模式 | 例 | 强度 |
|---|---|---|
| 前缀 | `buf` ⊂ `buffer` | 强 |
| 辅音骨架 | `mgr` vs `manager` | 强（FastText 补不到，规则必需） |
| 子序列 | `bfr` ⊂ `buffer` | 中 |
| 首字母缩合 | `dto` ← `data transfer object` | 中 |

现有的 `AbbreviationGenerator` 已经实现了前三种，但用法是**反的**：
它从查询词生成候选缩写再去碰语料。这里要**倒过来用**——
拿项目里真实存在的 80 个非词单元，去和候选全称配对。

倒过来后候选空间从「所有可能的缩写」（`abbreviate("readahead")` 会生成 46 个）
缩到「项目里真实出现的 80 个」，而且不会漏掉规则生成不出的怪写法。

### 关键：两个通道互相确认

单通道都不够准：

- 只看正字法：`buf` 兼容 `buffer`，也兼容 `buffalo`
- 只看 embedding：`user` 和 `role` 很近，但不是变体关系

**取合取**：

```
score(surface, canonical) = orth(surface, canonical) · cos(v(surface), v(canonical))
```

`buf`↔`buffer`：正字法前缀匹配（高），且微调后两者在向量空间里也近（高）→ 通过。
`buf`↔`buffalo`：正字法也匹配（高），但向量空间里离得远（低）→ 挡掉。
`user`↔`role`：向量近（高），但正字法零关系（0）→ 挡掉。

**这就是「结合 lexical rules」的具体形态**：两个都不可靠的信号，
取合取之后可用。而且两个通道都是离线算的，查询时不产生任何成本。

---

## 五、索引

### 现状的三个问题

| 问题 | 实测 | 后果 |
|---|---|---|
| **posting 存符号对象而非 ID** | `ngramed_symbol.json` 449 term / 3707 posting = **2197 KB**，而全部 1718 个符号才 750 KB | 外推到内核量级（80 万符号）是 **1 GB vs 11 MB** |
| **只索引 `name` 字段** | 符号里有 `signature`/`doc`/`container`，都没进索引 | `structural`/`semantic` satisfier 无处落地 |
| **ICF 量纲不对** | 现有 `icf_term_embedding.py` 用 `log(154686 调用链 / df_chains)` | 索引打分需要的是 `log(1718 符号 / df_symbols)`，两者不可混用 |

第一个是唯一会致命的：

```
              符号数      存对象      存 ID
youlai-boot     1718        2 MB     0.03 MB
中型项目       50,000       62 MB      0.7 MB
内核量级      800,000      999 MB     11.4 MB
```

### Schema：四个产物，职责分离

```
symbols     symbol_id → {name, type, file, range, signature, doc, container, ...}
                        ← 符号对象的唯一存放处，其余产物只引用 id

postings    term → [(symbol_id, field, tf), ...]
                        ← 只存 id，不存对象

terms       term → {df, icf, source}
                        ← df/icf 按**符号**算，不是按调用链
                        ← source: ronin | second_pass | override | whole

expansion   canonical → [(project_term, score, reason), ...]
                        ← 离线建，见第六、七节；索引本身保持精确
```

**四者分离的理由**：`expansion` 会随 LLM/embedding 迭代频繁重建，
`postings` 只随代码变化重建，`symbols` 是解析产物。绑在一起就得整体重建。

### 超集索引：一个标识符发多种形式

第三节的三段式切分不保证正确，所以**不依赖它正确**——
每个标识符把所有分解形式都入索引，并记来源：

```
符号 iostat_show
  ├ Ronin           → iostat, show          source=ronin
  ├ 二次切分         → io, stat              source=second_pass
  └ 整体            → iostat_show           source=whole

postings 里出现 5 个 term，其中 io/stat 的权重打折（来源不如 ronin 可靠）
```

查询侧同样发多种形式，**任一分解对上即命中**。
代价是 posting 数量增加约 1.5~2 倍——按上表，内核量级仍在 20 MB 内。

> 这比「保证两边切得一致」是更弱的要求，因此更稳健：
> 切分器换版本、词表更新，都不会让旧查询突然失配。

### 分域：命中在哪个字段不一样重

`field` 不是可选字段，它是 `structural` 和 `semantic` satisfier 的落地点：

| field | 来自 | 初始权重 | 说明 |
|---|---|---|---|
| `name` | 符号名 | **1.0** | 最强证据 |
| `signature` | 参数/返回类型 | 0.6 | `Buffer` 作参数类型也算相关 |
| `container` | 所属类/包 | 0.5 | **`structural` satisfier 靠它** |
| `doc` | 注释 | 0.3 | 召回高、精度低 |
| `annotation` | 注解名（切分后） | **0.9** | 见第八节；当前索引里还没有 |
| `annotation_arg` | 注解参数 | 0.7 | `@Schema(description=…)`、权限串、URL 路径 |

权重是初值，按 [08](08-open-questions.md) 的评测集调。

### 查询时：两次查表 + 一次合并

```python
def eval_unit(unit) -> Frag:
    acc = defaultdict(float); ev = defaultdict(list)
    for canon, w1 in expansion[unit.name]:          # 第一跳（LLM 离线产物）
        for term, w2, reason in expansion[canon]:   # 第二跳（向量 + lexical）
            t = terms.get(term)
            if not t or t.icf < ICF_FLOOR:          # get/data/name 直接跳过
                continue
            for sym_id, field, tf in postings[term]:
                s = unit.weight * w1 * w2 * FIELD_W[field] * t.icf
                acc[sym_id] += s
                ev[sym_id].append((term, canon, field, reason, s))
    return Frag(nodes=acc, evidence=ev)
```

**没有 LLM，没有向量计算，两层哈希查表加一次累加。**
`evidence` 逐条记下「命中 `buf`，因为它是你查询词 `buffer` 的缩写，
出现在 `name` 字段」——这是 [03](03-data-model.md) 要求的证据链。

### 打分：为什么是乘不是加

```
score = 单元权重 × 跳一相似度 × 跳二映射分 × 字段权重 × ICF
```

乘法的含义是**每一跳都是一次打折**：经过语义联想（0.76）+ 缩写映射（0.91）
+ 命中在 doc 而非 name（0.3）之后，这条证据只值 0.21——
它**应该**远低于直接命中 `name` 的那条。加法做不到这个衰减。

同一符号被多个 term 命中时用**累加**（不同证据互相印证），
但单元内部的多信号合成仍按 [04](04-query-unit.md) 的 `combine`（默认 `noisy_or`）走。

### ICF 用符号级，不是调用链级

```
term       df(符号)   icf = log(1718/df)
get           207        2.12      ← 12% 的符号都含它
user          125        2.62
token          64        3.29
login          20        4.45      ← 有区分度
captcha        26        4.19
```

**`ICF_FLOOR` 直接卡在这里**：`get`/`id`/`name` 这类 icf < 2.5 的词
不进扩展表也不参与打分，只作为精确匹配时的辅助条件。
现有的 `icf_term_embedding.py` 算的是调用链级 ICF，两者量纲不同，
**索引打分必须用符号级的，不能复用**。

### 增量重建

四个产物的重建触发条件不同，这决定了它们必须分开存：

| 产物 | 何时重建 | 代价 |
|---|---|---|
| `symbols` | 代码变化 | 增量：只重解析变动文件 |
| `postings` | `symbols` 变化 或 切分器/词表更新 | 增量：按 symbol_id 删旧插新 |
| `terms` | `postings` 变化 | 全量重算 df/icf，但很便宜 |
| `expansion` | LLM 提示词、向量模型、阈值变化 | 全量，但与代码无关，可离线慢慢跑 |

**`expansion` 与代码解耦是关键**——调阈值、换 embedding 不需要碰索引，
这也是第七节坚持「索引保持精确、模糊性放扩展表」的实际收益。

## 六、第一跳搭在已有的那次 LLM 调用上

上一节说第一跳（`performance` → `cache`/`buffer`）必须靠代码语料训。
**其实有条更省的路**：那次把查询分解成单元的 LLM 调用本来就存在，
让它在同一次响应里连词一起给出来，边际成本只是输出 token。

### 实测：LLM 的通用联想确实落得到这个项目上

拿通用知识会给出的词，去查项目里实际有没有：

| 单元 | 通用词在项目里命中 | 未命中 |
|---|---|---|
| **io** | **12/14**：`save`×37621、`send`×9981、`export`×3643、`write`×1612、`load`、`file`、`download`、`upload`、`read`、`import`、`stream`、`input` | `output`、`receive` |
| **performance** | 6/13：`page`×10377、`cache`×8585、`redis`×1289、`async`×194、`limit`、`lock` | `buffer`、`batch`、`pool`、`queue`、`thread`、`sync`、`concurrent` |
| **disk** | 1/12：`path`×306 | `disk`、`storage`、`swap`、`sector`、`flush`、`oss`、`minio` … |

**io 那行说明问题**：项目用的是 `save`/`send`/`export`/`write`/`load`/`file`
这种**普通概念词**，不是什么怪异的项目黑话。LLM 被问「哪些词标志 IO 代码」
会毫不费力地给出这一整串。**通用联想在这里是够用的。**

`performance` 那行暴露了真正的缺口：命中的 `redis`、`page` 是
**技术栈相关**的词——LLM 不知道这个项目用 Redis、用分页，就不会说。

### 所以：把项目词表放进 prompt

缺口不用训练来补，**直接给 LLM 看**：

```
整个项目词表 302 个词 = 2381 字符 ≈ 595 tokens
```

595 tokens。每条查询都带上完全可行，而且它是**固定前缀，走 prompt cache**。

于是提示词从「生成相关词」变成「**从这个项目的词表里挑相关词**」：

```
本项目词表（按频次）: get, role, user, mobile, save, auth, ..., redis, oss
查询: io performance on disk
→ 拆成单元，每个单元从上表里选出相关词并给分
```

**这一步把接地问题在构造上消掉了**：输出的词天然就在项目词表里，
不存在「LLM 说 `department` 但项目写 `dept`」的问题——
因为词表里只有 `dept`，LLM 只能选 `dept`。

**等于第一跳和第二跳合并成了一步。**

### 那前面几节还有什么用

三处，都还需要：

1. **LLM 说了词表外的词时**——比如它坚持输出 `buffer` 而项目只有 `buf`。
   这时第二跳（cc.en.300 + lexical rules，已实测可用）把它映射进来。

2. **项目大到词表塞不进 prompt 时。** 302 个词是这个项目；
   几万个 term 的大项目放不下。那就先用 ICF + 向量把候选缩到几百个再交给 LLM。
   **这是这套 embedding 机制真正不可替代的场景。**

3. **查全率兜底。** LLM 挑词受它自己判断的限制；向量近邻是一路独立的、
   几乎免费的信号，用来捞它漏掉的。

### 代价与收益

| | |
|---|---|
| 增加的调用 | **0**（搭在已有那次上） |
| 增加的输入 | ~595 tokens，固定前缀可缓存 |
| 增加的输出 | ~200–400 tokens，即约 1–2 秒解码 |
| 省掉的 | **多 repo 语料 + 全局预训练这一整块前置工作** |

**结论：代码语料的预训练从「阻塞项」降为「大项目的扩展项」。**
整条链路可以先不做它就跑起来。

> **顺带一个诚实的观察**：本项目 disk 相关的词只有 `path` 一个。
> youlai-boot 是业务后台系统，压根没有磁盘层代码——
> `io performance on disk` 这条贯穿全文的例子**对这个样例项目并不适用**，
> 验证时要换一个有系统层代码的项目，否则测不出东西。

---

## 七、微调仍然必需，但目标要说清

第六节把第一跳交给了 LLM，但**微调这一步不能省**。理由不是 OOV——
OOV 只有 3 个词——而是**高频词的词义错配**。

### 量化：28.6% 的 token 落在词义对不上的词上

对每个词 t，取它在项目语料里的共现词（ICF 加权，压掉 `get`/`save`），
算 t 与这些词在**通用空间**里的平均余弦。分数低 = 通用向量放的位置
和项目怎么用它对不上。脚本：`scripts/probe_embeddings.py mismatch`。

| 词 | 项目频次 | 错配分 | 项目里的共现词 | 通用义 vs 项目义 |
|---|---|---|---|---|
| `clean` | 136 | **0.009** | prefix, bearer, token, invalidate | 清洁 vs **清理 JWT** |
| `recur` | 325 | 0.048 | dept, list, vo, options | — vs **递归部门树** |
| `vo` | 1,689 | 0.050 | route, build, routes, user | 噪音 vs **Value Object** |
| `union` | 187 | 0.058 | data, scope, filter, segment | 联合 vs **SQL UNION** |
| `silent` | 2,288 | 0.062 | login, bind, mobile, token | 安静 vs **静默登录** |
| `around` | 146 | 0.072 | log, async, save, username | 周围 vs **AOP `@Around`** |
| `success` | 6,114 | 0.068 | judge, code, result, msg | 成功 vs **返回码** |
| `exception` | 7,174 | 0.080 | business, change, mobile, bind | 例外 vs **BusinessException** |
| `commence` | 92 | 0.098 | error, write, failed, code | 开始 vs **Spring Security 入口** |
| `emitter` | 110 | 0.097 | remove, send, event, broadcast | 发射器 vs **SSE Emitter** |

**错配分 < 0.15 的有 63/187 个词，占项目 token 总量的 28.6%。**

看这批词的构成就明白微调在补什么：

- **框架惯用法**：`around`（AOP 切面）、`commence`（Spring Security）、
  `authorities`、`emitter`（SSE）、`segment`（SQL）、`authorities`
- **中式英语**：`judge`＝判断（不是法官）、`silent`＝静默、
  `recur`＝递归、`business`＝业务

**通用语料里根本没有这些义项。** 这不是罕见词问题——`exception` 排名 4,257、
`business` 排名 273、`clean` 排名 801，全是极常见的词，只是义项不对。

> **指标的局限要说明**：它把「真错配」和「共现词全是 `get`/`save`
> 这类无信息动词」混在了一起。`role`（9.6 万次，0.090）多半属于后者。
> **榜单前 25 可信，28.6% 是上界。**

### 微调要满足的两个门禁

有了上面的排名，微调终于有了可测的目标——而不是拍个学习率了事。

**门禁 1（不能坏）：缩写映射不许退化。**
第二跳依赖 `cos(dept, department) = 0.749`。微调时 `dept` 朝项目上下文移动，
而 `department` 在项目里 0 次、原地不动，距离可能被拉开。

**但实测下来这个张力基本是假的**（`scripts/probe_embeddings.py gates`）：

| 分组 | 对数 | 例 | 门禁1 风险 |
|---|---|---|---|
| 全称侧项目里 0 次 | **6/13** | `buf`↔buffer、`mgr`↔manager、`pwd`↔password、`cfg`、`impl`、`req` | **无**——拿不到梯度就不会漂 |
| 项目侧已匹配（错配分 ≥0.15） | 5 | `dept` 0.181、`auth` 0.198、`config` 0.244、`addr` 0.280、`dep` 0.254 | 冻结即可 |
| 项目侧错配（<0.15） | 6 | `vo` 0.050、`recur` 0.048、`msg` 0.079、`scopes` 0.131、`perms` 0.148 | **微调应改善** |

两件事让门禁 1 基本自动成立：

**a. 第二跳的全称侧本来就不在项目里。** 这不是巧合——
第二跳存在的理由就是「LLM 说的通用词项目里没有」。没有出现就没有梯度，
`buffer`、`manager`、`password` 在微调中一动不动。**6/13 结构性安全。**

**b. 高 cos 的对恰好是已匹配的词，低 cos 的对恰好是错配的词。**
`dept`(0.749)/`auth`(0.673)/`config`(0.641) 错配分都 ≥0.18；
而 `perms`↔permission 只有 **0.174**、`vo`↔object **0.069**、
`recur`↔recursive **0.211**——正因为 `perms` 现在在「烫发」那片。
**把 `perms` 拉向 auth/role，是在拉近它和 `permission`，不是拉远。**

所以门禁 1 的正确表述不是「都别降」，而是：

```
冻结组（错配分 ≥0.15）: cos 不得下降
放开组（错配分 <0.15）: cos 应当上升
```

**真正有风险的只有「高 cos + 低错配分」的词**——本项目只有
`dict`(cos 0.520 / 错配 0.140) 一个。这种少到可以直接列出来显式冻结。

### 怎么冻结：FastText 做不到，改用 Word2Vec

gensim 有逐词学习率（`wv.vectors_vocab_lockf`），但**在 FastText 上不管用**：
词向量 = 词条向量 + 子词向量之和，`lockf=0` 只锁住词条那一半，
子词照样被更新。实测冻结词仍移动 0.0525，对照未冻结的 0.0676——只压下 22%。

**所以微调这一步改用 Word2Vec**（无子词，逐词锁精确生效）：

```
1. 用 cc.en.300 给所有词算初始向量
   —— 包括 3 个 OOV（result<t>/data<t>/mybatis），由子词合成
2. 词表 = 项目词表 ∪ 第二跳需要的全称词
   全称词以 lockf=0 冻结在预训练位置 → 一个空间，不用对齐
3. lockf(t) = clip(1 − 错配分(t)/0.15, 0, 1)
   已匹配的词接近 0（冻住），错配的词接近 1（放开）
4. 在项目语料上训 2~5 轮
```

这样**子词只用于初始化、不参与训练**，两个诉求都满足：
OOV 拿到了合理起点，逐词控制又精确。门禁 1 对冻结组**按构造成立**。

> 若坚持用 FastText 续训，逐词锁只有 22% 效果，
> 门禁 1 就只能靠回归测试事后发现，不能事前保证。

**回归测试照跑**（错配分只是代理指标，可能误判）：

```
微调前后跑 scripts/probe_embeddings.py abbrev
冻结组：11/13 通过率不下降，cos(dept, department) ≥ 0.70
放开组：cos(perms, permission) 应从 0.174 上升
```

### 兜底：扩展表是离线算的，可以不做选择

真到两难时还有一条退路：**两个空间都留着，建表时各算一次取较高的**，
并在表里记下这条来自哪个空间。查询时仍是查表，零成本，而且可审计——
能直接看出多少条扩展靠预训练、多少条靠微调。

**门禁 2（必须好）：错配榜前 25 要真的被修好。**

```
微调后 around  的近邻应出现 aspect / log / async，而不是 surrounding
微调后 perms   的近邻应出现 auth / role / scopes，而不是 perming / hair
微调后 scopes  的近邻应出现 token / auth / perms，而不是 Leupolds
```

两个门禁刚好是一对张力：门禁 2 要动，门禁 1 要别动太多。
**锚定正则的 λ 就该按这两个数来调**，不是凭感觉。

### 顺带解决 OOV

`result<t>`、`data<t>`、`mybatis`、`minio`、`redisson` 这几个 OOV 词
在微调时通过 FastText 子词获得初始向量，再由项目语料上的共现修正。
这是微调的副产品，不是主要目的——只有 3~5 个词。

---

## 八、注解

[07](07-mapping-to-current.md) 把 `annotated_by` 列为**性价比最高的一条边**：
抽取几乎免费，语义信号最强。但当前索引里**完全没有**。这一节说清楚要抽什么、怎么用。

### 实测：项目里有 77 种注解

源码不在手边，但 `dependency_graph.json` 的 2107 条 import 边能反推出来：

| 类别 | 数量 | 例 |
|---|---|---|
| **文档/契约** | 最多 | `@Schema`(63 文件)、`@Tag`(14)、`@Operation`(13)、`@Parameter`(12) |
| 框架结构 | 多 | `@RestController`、`@RequestMapping`、`@Bean`、`@Configuration`、`@Mapper` |
| 行为语义 | 中 | `@Transactional`(7)、`@Scheduled`(2)、`@CacheEvict`(2)、`@Around`/`@Aspect`/`@Pointcut` |
| **项目自定义** | 少但最有价值 | **`@Log`(12)、`@RepeatSubmit`(6)、`@DataPermission`(3)** |
| 持久化 | 中 | `@TableName`(12)、`@TableField`(6)、`@TableId`、`@EnumValue` |

> `@Around`/`@Aspect`/`@Pointcut` 的存在正好解释了[第七节](#七微调仍然必需但目标要说清)
> 测出的 `around` 词义错配——它在这个项目里是 AOP 切面，不是「周围」。

### 抽取：tree-sitter 里现成，零成本

`java_parser.py` 已经在用 tree-sitter，注解节点就在 AST 上（实测确认）：

```
marker_annotation   name=RestController   args=—
annotation          name=RequestMapping   args=("/api/v1/users")
annotation          name=Log              args=(module = "user")
annotation          name=PreAuthorize     args=("@ss.hasPerm('sys:user:query')")
```

`SymbolItem` 现在是：

```python
name, type, file, range, name_pos, signature, language, doc, container
```

加一个字段即可：

```python
annotations: list[Annotation]     # Annotation = {name, args: dict|str, target}
```

`target` 记它标在哪（class / method / field / parameter）——
标在方法上的 `@Transactional` 和标在类上的含义不同。

### 注解是三样东西，不是一样

这是设计上最容易漏的一点：

| 用法 | 落在哪 | 支撑哪个 satisfier |
|---|---|---|
| **注解名** | `postings` 的 `annotation` 域（权重 0.9） | `annotation` |
| **被标注关系** | `annotated_by` 边 | `hop` / 图算子 |
| **注解参数** | `postings` 的 `annotation_arg` 域 | `lexical` |

三者不可互相替代。举例：

```java
@PreAuthorize("@ss.hasPerm('sys:user:query')")
```

- 名字 `PreAuthorize` → 命中 `auth` 单元
- **参数里的 `sys:user:query`** → 命中 `user`、`query` 单元，且这是**权限字符串**，
  搜「用户查询权限」时它是最直接的证据
- 边 `method --annotated_by--> PreAuthorize` → 可以问「所有带鉴权的入口」

只索引名字就丢了后两者。

`@Schema(description="用户分页查询")` 尤其值钱——**参数里是自然语言描述**，
比 javadoc 更结构化、更可靠，是 `semantic` satisfier 最好的输入。
本项目有 63 个文件用它。

### 注解名走同一套切分与扩展

注解名就是标识符，直接复用[第三节](#三统一分词)的三段式切分：

```
@RestController → [rest, controller]
@AppCache       → [app, cache]        → 命中 cache 单元
@CacheEvict     → [cache, evict]      → 命中 cache 单元
@RepeatSubmit   → [repeat, submit]
```

所以 `annotation` satisfier **不是对字面名字的正则**——
[04](04-query-unit.md) 里 `annotation(r"@(Async|Cacheable|Scheduled)")` 那种写法
需要 LLM 现场生成正则，正是[第六节](#六第一跳搭在已有的那次-llm-调用上)要去掉的。
改成对切分后的单元匹配，项目自定义的 `@AppCache` 自动和 `@Cacheable` 一起命中。

### 元注解：框架自己声明了同义关系

**这是注解相对其它信号的独有优势。** Spring 里：

```
@RestController  =  @Controller + @ResponseBody
@GetMapping      =  @RequestMapping(method = GET)
@PostMapping     =  @RequestMapping(method = POST)
@Service/@Repository/@Component  ← 都是 @Component
```

这层关系**是框架在源码里声明的，不是猜的**——比 embedding 近邻和 LLM 联想
都可靠。一条查询问「HTTP 入口」，应当同时命中
`@RestController`、`@GetMapping`、`@PostMapping`、`@RequestMapping`。

三层来源，按成本从低到高：

1. **硬编码表**——Spring / JPA / Jackson / MyBatis 的常用元注解关系。
   几十条，覆盖绝大多数，一次写完长期有效。
2. **解析项目自己的 `@interface` 声明**——自定义注解的元注解在源码里，
   直接可得。`@RepeatSubmit` 标了什么、`@DataPermission` 继承什么，一目了然。
3. **落回名字切分**——不认识的注解，走上一小节。

展开后写进 `expansion` 表，与词的扩展表同构：

```
expansion["@RequestMapping"] = [("@GetMapping", 1.0, "meta"),
                                ("@PostMapping", 1.0, "meta"),
                                ("@RestController", 0.8, "meta-transitive")]
```

`reason="meta"` 与 `reason="prefix"`/`"ctx"` 并列，**但可信度是 1.0**——
因为它是声明的事实，不是估计。

### 项目自定义注解是最高价值信号

`@Log`、`@RepeatSubmit`、`@DataPermission` 这三个是本项目自己定义的。
它们的特点：

- **语义极强且无歧义**——`@RepeatSubmit` 就是防重复提交，没有第二种解释
- **通用模型完全不认识**——cc.en.300 里没有，LLM 也猜不到
- **但它们在项目词表里**——所以[第六节](#六第一跳搭在已有的那次-llm-调用上)
  「把项目词表放进 prompt」的做法能覆盖到：LLM 看得见 `repeat`、`submit`、
  `permission` 这些单元

这正是那套方案相对「训练 embedding」的优势所在——
自定义注解出现次数少（3~12 次），embedding 学不出来，但 LLM 看一眼就懂。

### `annotation` satisfier 规格

```python
annotation(
    units=["cache", "async"],      # 切分后的单元，不是正则
    names=["@Transactional"],      # 也可以直接点名，会经元注解展开
    target="method",               # 可选：只算标在方法上的
    args_match=None,               # 可选：对参数再加词法条件
    weight=0.9,
)
```

求值时：`names` 先过元注解展开 → 与 `units` 合并 →
查 `postings` 的 `annotation` / `annotation_arg` 域 → 产出 Frag。
与其它 satisfier 一样是纯查表，无 LLM。

### 一个几乎免费的附加信号

**共同标注在同一批方法上的注解是相关的。** 若 `@RepeatSubmit` 总是和
`@PostMapping` 一起出现，那么问「表单提交」时两者应互相加分。

这是从 `annotated_by` 边直接数出来的共现，不需要额外抽取。
本项目规模小、这个信号弱，但在大型 Spring 项目里很可用——
**而且它是项目特有的，通用知识给不了。**

## 九、怎么验证

两个都不需要端到端评测：

**a. 锚点漂移**（第二节）——不需要任何标注，微调完立刻能算（`probe_embeddings.py gates`），
用来判断微调的强度对不对。

**b. 一份小 gold set**——人工标 50~100 对 `(项目表层写法, 规范形)`，
从那 80 个非词单元起手，半小时能标完。指标是扩展表相对这份标注的准召。

比端到端评测便宜一个量级，且直接对准这一层。调阈值、调两个通道的权重
都该看这个数——顺带也回答了 [08](08-open-questions.md) 里
Q4a/Q4b「权重怎么定」。

---

## 小结

1. **不增加 LLM 调用次数。** 词的派生搭在已有的那次「查询→单元」调用上，
   并把项目词表（**实测 595 tokens**）放进 prompt，让 LLM
   **从项目词表里挑**而不是凭空生成——接地问题在构造上就没有了。

2. **全局预训练 + repo 微调，不从零训。** 单项目 302 词 / 103 万 token
   撑不起 128 维；而 `dept ≈ department` 在单个 repo 里学不到
   （`department` 出现 0 次），在跨 repo 语料里学得到——换语料就有信号。
   **实测确认**：cc.en.300 里 `cos(dept, department) = 0.749`，
   而同前缀干扰词 `depot` 只有 0.301。

3. **微调必需，但目标是词义错配不是 OOV。** OOV 只有 3 个词；
   而**错配分 <0.15 的词占项目 token 总量 28.6%**——`around`（AOP 切面）、
   `commence`（Spring Security）、`judge`（判断）、`silent`（静默登录）
   这些义项通用语料里没有。微调要过两个门禁：13 对缩写映射不退化、
   错配榜前 25 被修好。

4. **两跳的来源不同。** 实测 cc.en.300 覆盖本项目词表 99%（但「在词表里」
   不等于「向量能用」，见上条），
   缩写↔全称 11/13 通过正字法对照——**第二跳（规范词→项目写法）
   不需要训练，今天就能用**。第一跳它给不出（`performance` 与
   `cache`/`buffer`/`async` 的余弦全在随机噪音水平），但交给 LLM 即可：
   实测 io 单元的通用词有 **12/14** 真的出现在本项目里。

   于是**代码语料预训练从阻塞项降为大项目的扩展项**——
   词表塞不进 prompt 时才需要它先做候选收窄。

5. **微调要克制。** 低 LR、少轮次、锚定正则、用 FastText 子词；
   拿 261 个共享英文词当漂移探针。分开训再 Procrustes 对齐这条路，
   实测锚点数不够（d=128 时只有 1.8x），不推荐。

6. **切分是三段式，`srctoolkit.Delimiter.split_camel` 只做第一段。**
   它底层的 Ronin 在显式边界上 100% 可靠，但频率表挖自 GitHub **Java** 项目——
   **在 Linux kernel 风格的标识符上只有 68%**（`iostat`、`kmalloc`、`softirq` 都切不开）。
   换频率表实测更糟（`iostat`→`ios|tat`），因为它另有 6 个按官方表标定的超参。
   **正确做法是在外面加层**：领域词表 Viterbi 二次切分（只处理 Ronin 留成实心的单元，
   实测修好 9/10、弄坏 0 个），加覆盖表修 `dentry`→`den|try` 这类切错位置的，
   最后用超集索引兜底。二次切分的词表从代码库自身带分隔符的标识符里数出来，
   **不需要训练**。

7. **embedding 和 lexical rules 取合取。** 前者给语义邻近，后者给正字法变体。
   另加两个护栏：近邻先滤形态变体（fastText 的 top-k 被 `pool → pools,
   pool.The` 这类霸占），长度 ≤2 的单元（`io`、`vo`）不走向量扩展。

8. **索引保持精确，模糊性放进预计算的扩展表。** 可解释、可审计、
   阈值按 ICF 自适应。

9. **注解要抽，而且要当三样东西用。** 实测项目里有 77 种注解，
   当前索引里一种都没有，而 tree-sitter 已经在用、节点现成，抽取零成本。
   注解名进 `annotation` 域、被标注关系进 `annotated_by` 边、
   **参数进 `annotation_arg` 域**——`@PreAuthorize("@ss.hasPerm('sys:user:query')")`
   的权限串和 `@Schema(description=…)` 的自然语言描述都在参数里，只索引名字就全丢了。
   另外**元注解是框架声明的同义关系**（`@GetMapping` = `@RequestMapping(GET)`），
   可信度 1.0，比任何 embedding 近邻都可靠。

10. **索引拆成四个产物：`symbols` / `postings` / `terms` / `expansion`。**
   现在 posting 里存的是符号对象而非 id——1718 个符号就占 2.2 MB，
   外推到内核量级是 **1 GB vs 11 MB**。分开还有个实际收益：
   `expansion` 与代码解耦，调阈值、换 embedding 不必碰索引。
   另外 posting 必须**分域**（name/signature/container/doc/annotation），
   否则 `structural` 和 `semantic` satisfier 无处落地；
   ICF 必须用**符号级** `log(1718/df)`，现有的调用链级 ICF 量纲不同、不能复用。

依赖关系：**分词决定词表，词表决定微调能否对齐，扩展表决定召回上限。**
三者要一起设计——而分词这一环已经有现成实现，是三者里最省事的。
