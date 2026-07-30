# 07 索引需要什么：能力需求与当前差距

设计不受现有索引限制。**先定查询需要什么，再看现在缺什么。**
本章前半是能力需求，后半是与当前实现的差距。

## 索引必须提供的能力

### A. 元素与属性

| 能力 | 用在哪 |
|---|---|
| 符号表（id、名、种类、文件、位置、签名、容器） | 一切的基础 |
| **修饰符**（`async` / `static` / `abstract` / `final`） | `has_modifier` |
| **注解**（`@Async`、`@RestController`……） | `annotation` satisfier —— Java 项目最强的语义信号 |
| 文档注释 | `lexical(field="doc")`、`semantic` |
| 包 / 模块层级 | `structural` satisfier |

### B. 边

[03](03-data-model.md) 定义了九种边。按对查询的价值排：

| kind | 价值 | 抽取难度 |
|---|---|---|
| `calls` | 高，最基础 | 中（LSP / AST） |
| `contains` | 高，几乎免费（符号表里就有容器信息） | 低 |
| `annotated_by` | **高**，Java 场景语义密度最大 | **低**（AST 直接可得） |
| `implements` | 高 | 中 |
| `imports` | 中 | 低 |
| `reads` / `writes` | **高**，「谁改了这个状态」这类查询靠它 | 中 |
| `flows_to` | **高**，数据流查询靠它 | **高**（需要 def-use 分析） |
| `instantiates` / `throws` | 中 | 中 |

**`annotated_by` 是性价比最高的一条**：抽取几乎免费（注解就在 AST 上），
但语义信号极强——`@RestController` 直接告诉你这是 HTTP 入口，
比任何关键词组合都准。

**`flows_to` 是最贵但最不可替代的一条**：没有它，
「这个参数的值从哪来」「哪些路径会把用户输入带到 SQL 拼接处」
这类查询完全答不了，而它们恰恰是理解和审计代码时最常问的。

### C. 图查询语义

- **保留路径的遍历**：`hop` 要的是路径，不是可达集
- **按边类型与置信度过滤**
- **双向遍历**：`dir="any"`
- **等价符号合并**：接口方法与其实现应视为同一个查询目标
  （当前 `equivalent_symbol_ids` 已在做）

### D. 向量与词表

- 项目词表与共现（`derived` 词的语料扩展、`similar` 算子）
- 元素级向量（`semantic` satisfier）
- **术语扩展表 + 全局预训练词向量**（[09](09-grounding.md)）——
  决定召回上限的一层，且必须离线建好：单元扩展在查询时不能调 LLM。
  切分器已有（`srctoolkit`），只需补库名保护表

---

## 当前实现的差距

| 需求 | 现状 | 差距 |
|---|---|---|
| 符号表 | ✅ 1718 个符号，schema 够用 | 缺 `modifiers` 字段 |
| 注解 | ❌ **完全没有** | 需要在 parser 里抽，成本低收益大 |
| `calls` 边 | ✅ 677 条 | — |
| `contains` 边 | ⚠️ 信息在 `container` 字段里，不是边 | 需要物化成边 |
| `implements` | ⚠️ 独立表 `code_implementations`（159） | 需要统一进边视图 |
| `imports` | ⚠️ 独立表 `code_dependencies`（2107，文件级） | 同上 |
| `reads`/`writes`/`flows_to` | ❌ 没有 | 需要数据流分析 |
| 保留路径的遍历 | ❌ `reachable()` 返回可达集，路径在遍历时丢弃 | **最大的一处改动** |
| 置信度过滤 | ✅ `code_edges.confidence` 已有 | — |
| 等价符号合并 | ✅ `equivalent_symbol_ids` | — |
| 项目词表与共现 | ✅ ICF / semantic / hybrid 三个通道 | — |
| 词向量 | ⚠️ 单项目**从零训**（d=128, epochs=100, 词表仅 302） | 改全局预训练 + repo 微调（[09](09-grounding.md)） |
| 全局预训练语料 | ❌ 没有 | 多 repo、与本项目同一套切分与上下文定义 |
| 元素级向量 | ⚠️ 现在是 term 级 | 需要补元素级 |
| 统一切分器 | ✅ `srctoolkit.Delimiter.split_camel`（底层 Ronin）已在用 | 补库名保护表 + 复合词超集索引（[09](09-grounding.md)） |
| 项目缩写词典 | ❌ 现在是运行时**生成**候选再碰语料 | 改为离线**挖掘**并落成可审的表 |
| 术语扩展表 | ❌ 没有 | 索引保持精确，模糊性放这里 |

### 三处结构性差距

**1. 边散在三张表里。** `code_edges` / `code_implementations` /
`code_dependencies` 各一张表、各自 schema。`hop(edge=[...])` 要跨表查，
每加一种边都要改 `hop` 的实现。**应当统一成一个边视图**，
`kind` 作为普通查询条件。

**2. 遍历丢路径。** 现在是 BFS 求可达集：

```python
def reachable(self, start_ids, direction, max_depth, ...) -> Set[int]:
    result: Set[int] = set()          # ← 只留终点
```

`hop` 需要前驱链。改动会显著增加内存——所以 `max_paths` 和截断日志
在设计里是必需项而非可选优化。

**3. 没有数据流。** 这是唯一需要**新建分析能力**的一项，
其余都是重组已有数据。Java 侧可以走 CodeQL（当前已有 `codesense/codeql/`
的链路）或扩 LSP 用法。

---

## 可直接复用的

不用重写，这些是资产：

- **倒排索引 + 缩写扩展 + 分词**（`indexing/`、`expansion/`、`tokenizer/`）
  —— `lexical` satisfier 的底座
- **LLM judge**（批量、结构化返回、三分）—— `intent` 的实现
- **Embedding 三通道**（ICF / semantic / hybrid）—— `similar` 与 `semantic` satisfier
- **SemCon 抽取** —— 编译期第一步（切单元）
- **等价符号合并** —— `hop` 直接要用

还有一处**已经存在的雏形**值得记：`SurfaceGroupLogic(groups, graph_scope,
hop_count)` 允许 keyword group 之间声明图约束——那正是「unit 之间 hop」的
原型，只是产出被拍平成扁平集合，路径丢了。所以这不是新增能力，是把埋着的
能力提上来并让它返回片段。

---

## 迁移路径

**不要一次性替换。** 三个阶段，每阶段独立可验证：

### 阶段 A：QL 层 + 索引补齐

实现 `codesense/ql/`（数据模型 + 算子），同时补三件索引欠账：
注解抽取、边视图统一、遍历保留路径。

**手写** 3–5 段 QL 覆盖不同查询类型，验证算子集合够不够。
写不出来的地方就是缺的算子——这一步很便宜（几百行），
但能避免把编译器建在错的算子集上。

验证：拿真实查询手写等价 QL，结果应与现有 golden 一致
（`tests/integration/test_golden_relation_filter.py` 已经在锚定这条）。

### 阶段 B：编译器与编译回路

SemCon → QL 脚本，含试跑修正（[06](06-script-and-execution.md)）。
此时两条路并存，在同一批查询上比对差异——**差异本身就是最好的评测材料**。

### 阶段 C：数据流与切换

补 `flows_to`，解锁数据流类查询；新路径不差于老路径后切换。

**每个阶段结束都应能回答「比老的好在哪、差在哪」**，
而这需要先有评测——见 [08](08-open-questions.md)，那是最高优先级的前置工作。

下一篇：[08 待定问题](08-open-questions.md)。
