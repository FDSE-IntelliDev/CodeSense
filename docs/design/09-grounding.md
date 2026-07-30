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
> 探针脚本：`scripts/probe_pretrained_embedding.py`。

**a. 覆盖率 99%——比预期好得多。**

| | 词表大小 | 在 cc.en.300 里 | OOV |
|---|---|---|---|
| 调用链语料 | 302 | **299（99%）** | `result<t>`、`data<t>`、`mybatis` |
| 符号切分单元 | 462 | **457（99%）** | `aliyun`、`minio`、`starttls`、`redisson`、`mybatis` |

OOV 全是库名和品牌名，靠子词合成即可。**覆盖率不是问题**——
Common Crawl 里技术文本足够多，`auth`、`cfg`、`impl`、`dict` 这些都在词表里。

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

### 换掉 BPE

当前用 sentencepiece BPE。BPE 的目标是**最小化编码长度**，不是找语素，
切分边界和词形无关：`buf` 可能切成 `bu|f`，`buffer` 整体成词，两者零共享单元。

**改成词典驱动的 Viterbi 切分**：在词典上求使 `Σ log P(unit)` 最大的路径。
词典三部分：英文词表 + 项目自己的 camel/下划线单元 + 通用缩写表。

好处是边界落在语素上（`readahead` → `read|ahead`，不会切成 `rea|dahead`），
而且确定性、可解释。

### camelCase 是免费的监督信号

**作者写 `bufMgr` 时，已经告诉了你 `buf` 和 `mgr` 是这个项目的原子单元。**

实测：1718 个符号切出 462 个单元、3884 次出现，其中 80 个不是英文词——
那 80 个就是本项目的缩写候选，扫一遍符号表就有，不用人工整理。

### 两端必须共用同一个切分器

```
查询  "io performance on disk"  ──切分──►  [io, performance, disk]
标识符 bufMgr / readahead       ──切分──►  [buf, mgr] / [read, ahead]
```

这是「统一」的全部含义。全局预训练语料也要用同一套切分——
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

## 五、扩展表：查询时纯查表

### 两跳扩展，全部预计算

```
第一跳（embedding）:  unit concept  →  语义邻近的规范词
    performance → [cache 0.81, buffer 0.76, async 0.74, flush 0.71, pool 0.68]

第二跳（lexical）:    规范词  →  项目里的表层写法
    buffer → [buf 0.91, bfr 0.72]
    config → [cfg 0.88]
```

查询时：查表 → 对每个表层写法做**精确**倒排查询 → 分数 = 单元权重 × 跳一 × 跳二。

**没有 LLM，没有向量计算，两次哈希查表。**

第一跳的产物就是 [04](04-query-unit.md) 里 `derived` 那类词——
原先设想由 LLM 生成，现在由微调后的 embedding 生成。而且它**比 LLM 更好**：
LLM 给的是通用联想，微调后的 embedding 给的是**这个项目里真实共现**的词
（`buffer` 在本项目常与 `flush`、`sink` 同现）。

### 索引保持精确，模糊性放进扩展表

不要把倒排索引本身改成相似度索引：

1. 索引保持精确，不改数据结构，不引入 ANN 近似误差
2. 可解释——能说清「命中 `buf`，因为它是你查询词 `buffer` 的缩写，0.91」
3. 可审计——扩展表能 dump 出来给人看
4. 调阈值不用重建索引

代价是要离线算。但词表几百量级，全量比较也就几十万次，秒级完成。

### 阈值按 ICF 自适应

```
threshold(t) = base + k · (1 − norm_icf(t))
```

- 高 ICF 的稀有词（`swap`、`sector`）：阈值放低，多扩几个
- 低 ICF 的泛词（`get`、`data`、`by`）：阈值拉高，基本不扩

实测 `get` 出现 207 次——什么都和它「相似」，扩展只会制造噪音。
省事的做法：**低 ICF 的词直接不进扩展表**。

ICF 计算直接复用现有的 `icf_term_embedding.py`。

---

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

## 七、注解走同一套

注解名本身就是标识符，所以完全复用上面的机制：

```
@AppCache  ──切分──►  [app, cache]  ──扩展──►  匹配 cache 单元
```

于是 `annotation` satisfier 不该是对字面名字的正则
（[04](04-query-unit.md) 里 `annotation(r"@(Async|Cacheable|Scheduled)")` 那种写法
需要 LLM 现场生成正则，正是要去掉的），而是
**对切分后的注解名做单元式匹配**——`@AppCache`、`@CacheAside`、`@Cacheable`
一起命中 `cache`，分数由扩展表区分。

这样注解 satisfier 也变成纯查表。

---

## 八、怎么验证

两个都不需要端到端评测：

**a. 锚点漂移**（第二节）——不需要任何标注，微调完立刻能算，
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

3. **两跳的来源不同。** 实测 cc.en.300 覆盖本项目词表 99%，
   缩写↔全称 11/13 通过正字法对照——**第二跳（规范词→项目写法）
   不需要训练，今天就能用**。第一跳它给不出（`performance` 与
   `cache`/`buffer`/`async` 的余弦全在随机噪音水平），但交给 LLM 即可：
   实测 io 单元的通用词有 **12/14** 真的出现在本项目里。

   于是**代码语料预训练从阻塞项降为大项目的扩展项**——
   词表塞不进 prompt 时才需要它先做候选收窄。

4. **微调要克制。** 低 LR、少轮次、锚定正则、用 FastText 子词；
   拿 261 个共享英文词当漂移探针。分开训再 Procrustes 对齐这条路，
   实测锚点数不够（d=128 时只有 1.8x），不推荐。

5. **embedding 和 lexical rules 取合取。** 前者给语义邻近，后者给正字法变体。
   另加两个护栏：近邻先滤形态变体（fastText 的 top-k 被 `pool → pools,
   pool.The` 这类霸占），长度 ≤2 的单元（`io`、`vo`）不走向量扩展。

6. **索引保持精确，模糊性放进预计算的扩展表。** 可解释、可审计、
   阈值按 ICF 自适应。

依赖关系：**分词决定词表，词表决定预训练与微调能否对齐，扩展表决定召回上限。**
三者要一起设计。
