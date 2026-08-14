# CodeSearch 与 CodeSense 的功能及设计思路对比

本文对比以下两个项目：

- 原始项目：`/Users/huangzhuochen/PycharmProjects/CodeSearch`
- 改造后项目：`/Users/huangzhuochen/PycharmProjects/CodeSense`

对比主要基于两个项目的 README、设计文档、索引与查询实现，以及 CodeSense 的提交历史。重点关注功能、查询模型和设计思路的变化，不把目录调整、模块拆分、命名修改或一般代码重构作为主要差异。

对提交历史的分析需要先说明一个口径问题：如果直接将“提交人不是 `old6ma`”的提交全部视为后期改造，会同时包含 2026 年 4—6 月由 `huangzhuochen` 完成的 DSL、过滤器和 embedding 开发。真正明显的系统性重构主要从 7 月 29 日 `Chong Wang` 的提交及 7 月 30 日 `cs-wangchong` 的提交开始，尤其以 `7f9cf1e` 引入新的 `docs/design/` 设计稿为分界点。

## 一、结论

CodeSense 不是 CodeSearch 的普通工程重构，而是在保留“使用自然语言进行代码搜索”这一目标后，对检索抽象、执行模型、索引策略和产品形态进行的一次重新设计。

最大的思想变化是：

> CodeSearch 把搜索理解为一条固定的多阶段候选过滤流水线；CodeSense 把搜索理解为在带证据的代码子图上执行一段可组合的查询程序。

两者仍然解决同一类问题，但以下方面都发生了实质变化：

- 如何表达查询；
- 如何组合词法、结构和意图能力；
- LLM 在系统中负责什么；
- 代码图在查询中的地位；
- embedding 在离线和在线阶段的用途；
- 查询产物是否可阅读、修改和重新执行；
- 系统面向研究实验还是面向日常工具使用。

## 二、总体定位对比

| 维度 | CodeSearch | CodeSense |
|---|---|---|
| 基本目标 | 使用自然语言查询代码 | 使用自然语言查询代码 |
| 核心范式 | 多阶段召回、过滤和精排流水线 | 将自然语言编译成可执行 QL 程序 |
| 查询中间表示 | SemCon → 三份 SemQL 计划 | `QueryUnit` / `QuerySpec`，或直接生成 Python QL |
| 运行中的核心对象 | `list[dict]` 候选集和各阶段 JSON | 带节点、边、路径和证据的 `Frag` |
| 能力组合 | Surface → Relation → Intention，基本按阶段组合 | 所有算子统一组合，支持集合代数、图运算和控制流 |
| LLM 角色 | 抽取条件、生成领域计划、最终 Judge | 可直接生成脚本，也可只做 NLP 或最终 Judge |
| 代码图角色 | 后置 Relation Filter | 与词法召回平级的一等查询基底 |
| 查询结果 | 最终代码元素以及大量中间 JSON | 排名结果、证据、生成脚本或执行计划 |
| 典型使用形态 | 研究 pipeline，路径和产物驱动 | 安装后使用 `init` / `query` / `info` 的仓库工具 |
| 当前内置语言 | Python、Java、JavaScript/TypeScript、C/C++ | 只有 Java |
| 外部工具依赖 | Java 关系分析依赖 JDT.LS，可选 CodeQL | 建索引不要求编译、LSP 或 CodeQL |

从产品方向看，CodeSense 更接近一个供开发者或 Agent 使用的代码查询引擎；CodeSearch 更接近一套研究实验流水线。

## 三、查询模型的变化

### 3.1 从“三类条件、三套执行器”变成统一算子代数

CodeSearch 的核心抽象是三类 SemCon：

```text
SurfaceCon
RelationCon
IntentionCon
```

三类条件分别进入三套 Planner 和 Executor：

```text
SurfaceCon   → SurfacePlanner   → SurfaceExecutor
RelationCon  → RelationPlanner  → RelationExecutor
IntentionCon → IntentionPlanner → IntentionExecutor
```

旧系统的典型执行过程是：

```text
全库
  ↓ Surface：词法召回
候选集
  ↓ Relation：调用关系、文件、角色过滤
更小候选集
  ↓ Intention：聚类、embedding、LLM Judge
最终结果
```

该设计隐含了一个较强的架构假设：Surface、Relation 和 Intention 是三个不同阶段的能力，候选集需要逐阶段向下传递。

CodeSense 不再把这三类条件作为顶层架构，而是引入统一数据类型 `Frag`：

```text
Frag = 节点 + 边 + 路径见证 + 每个节点的证据
```

所有核心查询能力统一接收和返回 `Frag`：

```text
Frag → Frag
```

主要算子包括：

- `eval_unit`：使用一个或多个信号召回概念对应的代码元素；
- `hop`：寻找两个 fragment 之间满足约束的图路径；
- `reach`：从一组节点出发探索图邻域；
- `only`：按类型、文件、语言或自定义条件过滤；
- `top`：选择得分最高的候选；
- `degree`：按图节点度数过滤；
- `intent`：调用 LLM 对候选进行意图判别；
- `a | b`：并集；
- `a & b`：交集；
- `a - b`：排除；
- `hop(a, b)`：表达两个概念之间的结构关系。

因此，变化不仅是把三套执行器重构为统一接口，而是搜索语义模型发生了变化：

```text
CodeSearch：候选列表经过固定阶段过滤

CodeSense：多个带证据的代码子图经过查询代数组合
```

相关实现：

- [`codesense/ql/frag.py`](../codesense/ql/frag.py)
- [`codesense/ql/operators/unit.py`](../codesense/ql/operators/unit.py)
- [`codesense/ql/operators/hop.py`](../codesense/ql/operators/hop.py)

### 3.2 从固定 pipeline 变成查询程序

CodeSearch 会生成三份主要领域计划：

```text
surface_semql.json
relation_semql.json
intention_semql.json
```

不同条件的组合主要发生在对应 Executor 内部。例如，Surface Executor 负责：

- keyword group 内部的 OR；
- group 之间的 AND(n)；
- include/exclude；
- 多个 Surface condition 之间的集合组合。

Relation Executor 再单独解释：

- 文件路径；
- graph role；
- caller；
- callee；
- include/exclude clause。

CodeSense 的查询产物可以是一段真正的 Python QL：

```python
buffer = eval_unit(...)
disk = eval_unit(...)
related = hop(buffer, disk, ctx, edge="calls", hops=(1, 3))
answer = top(only(related, kind="method"), 20)
```

脚本可以包含：

- 中间变量；
- `if`；
- `for`；
- `while`；
- 函数定义；
- 试探后扩大搜索范围；
- 多个查询分支；
- 任意受支持算子的组合。

因此，CodeSense 的查询不再只是一棵静态条件树，而是一段受控的小程序。

生成的脚本会先经过 AST 白名单检查，并受到最大行数和执行步数预算约束。脚本不能导入模块、访问文件或网络，也不能访问未列入白名单的属性。不过这一机制主要用于防止模型误写危险代码，并不是抵御恶意输入的完整安全沙箱。

这一变化主要来自以下提交：

- `99c73eb`：将计划输出成脚本，并验证脚本与计划执行结果等价；
- `02997ba`：直接生成查询脚本，并允许使用控制流。

相关文件：

- [`codesense/ql/script.py`](../codesense/ql/script.py)
- [`codesense/llm/codegen.py`](../codesense/llm/codegen.py)
- [`docs/design/06-script-and-execution.md`](design/06-script-and-execution.md)

## 四、LLM 职责的变化

### 4.1 CodeSearch：结构化抽取和最终过滤

CodeSearch 中 LLM 的主要职责是：

1. 将自然语言抽取成 SemCon；
2. 生成 Surface、Relation 和 Intention 计划；
3. 在灰区候选上执行 LLM Judge。

可以概括为：LLM 负责填充 schema，Executor 负责解释 schema。

这种方式结构明确，但存在两个限制：

- schema 中没有字段的意图很难表达；
- 增加新的组合逻辑经常需要同步修改 schema、Planner 和 Executor。

### 4.2 CodeSense：三种查询路径

CodeSense 当前提供三条不同的查询路径。

#### `codegen`

LLM 根据自然语言、QL 算子说明和项目词表直接生成查询脚本：

```text
自然语言 + 算子说明 + 项目词表
                    ↓
                Python QL
```

这是表达能力最强的路径，也是当前实测召回率最高的路径。

#### `planned`

LLM 只负责语言理解，包括：

- 提取语义单元；
- 选择项目词汇；
- 判断单元之间的关系；
- 提议类型约束。

统计信息和代价模型负责：

- 验证模型输出；
- 决定起始候选集合；
- 排列执行顺序；
- 选择图遍历方向；
- 生成最终脚本。

对应的原则是：让模型做 NLP，让统计信息做优化。

#### `lexical`

该路径完全不使用 LLM：

- 从原始查询中提取项目词表能够识别的词；
- 使用倒排表和构建期生成的接地表；
- 使用代码图邻近性对候选进行加权；
- 没有 API key 或 LLM 路径失败时自动降级至此路径。

因此，CodeSense 不再只有“LLM 抽取 SemCon”这一条查询路径，而是明确支持：

```text
模型直接写查询程序
模型只理解自然语言
完全不使用模型
```

相关文件：

- [`codesense/search.py`](../codesense/search.py)
- [`codesense/llm/compiler.py`](../codesense/llm/compiler.py)
- [`codesense/ql/compile/planner.py`](../codesense/ql/compile/planner.py)

## 五、代码图地位的变化

### 5.1 CodeSearch：图主要作为后置过滤器

CodeSearch 中的调用关系主要用于：

- caller 过滤；
- callee 过滤；
- entry point、leaf、isolate 等图角色过滤；
- 在一定 hop 范围内补齐 Surface group coverage。

图查询通常接收 Surface 已经召回的候选，再决定保留哪些元素。

Java 调用关系主要有两条较重的分析链路：

- JDT.LS / LSP；
- CodeQL 数据库。

这类方案可以得到更丰富、可能更准确的关系，但要求安装额外工具，部分场景还要求目标项目能够被正确分析或构建。

### 5.2 CodeSense：图成为查询结果的一部分

CodeSense 中 `Frag` 本身就能够携带边和路径，图不再只是过滤条件。

例如查询“性能代码调用磁盘代码”时，两个概念可能落在不同方法上。简单交集：

```text
performance_candidates ∩ disk_candidates
```

很可能为空，因为未必有一个方法同时包含两套词。CodeSense 可以使用：

```python
hop(performance, disk, ctx, edge="calls")
```

返回连接两类语义节点的路径，包括：

- 起点；
- 终点；
- 中间节点；
- 路径上的边；
- 路径见证；
- 原始匹配证据。

这意味着检索单位从“单个相似代码元素”向“满足结构约束的代码子图”移动。

此外，CodeSense 的图构建不再依赖 LSP：

- `contains` 从声明容器直接构造，置信度为 1.0；
- `calls` 优先利用 AST 中的 receiver 和局部类型；
- 无法按类型解析时进行保守的方法名匹配；
- 每条调用边记录置信度和解析来源；
- 高歧义调用会被放弃，而不是生成大量低质量边。

这降低了调用图的精确度上限，但显著降低了部署和建库门槛。

相关文件：

- [`codesense/indexing/graph.py`](../codesense/indexing/graph.py)
- [`docs/design/10-graph.md`](design/10-graph.md)

## 六、索引和词汇扩展思路的变化

### 6.1 CodeSearch 的索引和在线精排

CodeSearch 的词法与语义部分由多个独立产物和模块组成：

- `symbols_index.json`；
- `dependency_graph.json`；
- `ngramed_symbol.json`；
- `invert_index.json`；
- 缩写扩展；
- SentencePiece；
- fuzzy、regex 和 exact search；
- 聚类模型；
- term embedding；
- 调用链训练语料。

在线查询阶段还会执行：

- 聚类过滤；
- embedding 过滤；
- LLM Judge。

也就是说，embedding 既参与词义匹配，也是在线精排的重要阶段。

### 6.2 CodeSense 的构建期词汇接地

CodeSense 将现役索引收敛为三个生命周期不同的产物：

```text
meta.json
index.json
expansion.json
```

其中：

- `meta.json` 保存项目、语言、构建时间和统计信息；
- `index.json` 保存符号、多字段 posting 和代码图边；
- `expansion.json` 保存通用概念到项目实际词汇的映射。

例如，接地表理论上可以记录：

```text
allocation → alloc
recycling  → recycle
```

这里有一个重要思想变化：

> embedding 的主要用途从在线对候选代码进行语义精排，转为在构建期把通用概念接地到项目词汇。

向量模型只在构建索引时加载，查询阶段只读取较小的 expansion 表。这样查询时不需要加载数 GB 的 fastText 模型。

当前支持三种接地策略：

- `lexical`；
- `vectors`；
- `finetune`。

不过现有实测表明，`vectors` 和 `finetune` 尚未稳定超过 lexical，并且 subsequence 扩展仍存在明显噪声。

这一变化带来的结果是：

- 查询阶段更轻；
- 向量模型不再是运行时必需品；
- 项目词表成为查询编译的重要上下文；
- 旧系统完整的在线 embedding/cluster 精排链路暂时不在现役实现中。

相关文件：

- [`codesense/indexing/grounding.py`](../codesense/indexing/grounding.py)
- [`docs/design/09-grounding.md`](design/09-grounding.md)

## 七、证据模型的变化

CodeSearch 已经比较重视中间产物和可解释性，会输出：

- 每组关键词的直接命中；
- Surface evidence；
- 各阶段保留和丢弃的候选；
- Relation 执行报告；
- LLM Judge reasoning。

但是，这些证据主要分散在多个 JSON 文件和 Executor 添加的字段中。

CodeSense 将证据变成核心数据模型的一部分：

```python
Evidence(
    unit_hits=(...),
    verdicts=(...),
)
```

证据会跟随 `Frag` 一起经过：

- 并集；
- 交集；
- 差集；
- 图路径操作；
- 排序；
- Intent Judge。

特别是在执行交集时，两边的证据都会合并，不会只留下最终分数。

用户可以直接执行：

```bash
codesense query "..." --why
```

查看每条命中的主要证据，也可以执行：

```bash
codesense query "..." --script
```

查看生成的查询脚本和完整执行信息。

因此，CodeSense 把“可解释”从旁路日志提升成了查询代数的正式契约。

## 八、查询优化思路的变化

CodeSearch 也包含 Planner，而且 Surface → Relation → Intention 本身就是按代价从低到高排列：

```text
倒排检索 < 图查询 < embedding / LLM
```

但优化范围主要是：

- 每个领域内部如何组织条件；
- 固定阶段内使用哪些策略和阈值。

CodeSense 引入了更明确的代价规划器：

- 根据 posting 的文档频率估计选择性；
- 优先从更小的候选集合开始；
- 选择图遍历方向；
- 决定约束执行顺序；
- 由模型提出词汇和关系，再由统计数据验证；
- 将同一个物理计划输出成脚本，并验证脚本执行等价性。

因此 CodeSense 实现了部分数据库式思路：

```text
自然语言 → 逻辑规格 → 代价规划 → 可执行脚本
```

不过当前的 CBO 仍然是轻量实现，不是成熟数据库那种覆盖丰富物理算子和统计模型的完整代价优化器。

对应提交：

- `ecef319`：加入 cost-based operator ordering；
- `3607cc5`：让模型做 NLP，让统计信息做优化；
- `372701f`：模型提出、统计验证；
- `99c73eb`：将计划发射成脚本。

## 九、用户使用方式的变化

### 9.1 CodeSearch：实验流水线

CodeSearch 的主要入口是：

```bash
python main.py \
  --project_path /path/to/project \
  --output_dir /path/to/output \
  --query "..."
```

它具有以下特点：

- 需要显式管理源码路径和 output 路径；
- 每条查询产生独立的 query 目录；
- 多个中间 JSON 是主要交互对象；
- `main.py` 中存在写死的本机测试参数；
- 当前检出的入口中部分阶段被注释，通过已有产物继续执行。

这更适合实验和调试 pipeline。

### 9.2 CodeSense：仓库级查询工具

CodeSense 的入口变成：

```bash
cd /path/to/project
codesense init
codesense query "..."
codesense info
```

`.codesense` 的发现方式类似 `.git`：

- `init` 在仓库旁生成索引；
- `query` 从当前目录向上寻找最近的索引；
- 仓库移动时索引可以一同移动；
- 不需要维护全局项目注册表；
- 必要时可以通过 `--index` 显式指定索引位置。

同时提供稳定的 Python API：

```python
from codesense import Project

project = Project.build("/path/to/project")
project = Project.open("/path/to/project/.codesense")
result = project.search("buffer allocation")
```

这不只是 CLI 重构，而是将使用场景从“运行一条研究实验脚本”转变成“初始化一次并持续搜索”。

对应提交：

- `221beea`：建立端到端 `Project.build/open/search`；
- `26deecf`：增加正式 CLI。

## 十、旧功能中被削弱或暂时丢失的部分

CodeSense 的核心抽象更加统一，但当前实现并不是 CodeSearch 的完整功能超集。

### 10.1 多语言现成支持退化

CodeSearch 声称支持：

- Python；
- Java；
- JavaScript / TypeScript；
- C / C++。

CodeSense 设计了可插拔的 `Language` adapter，但当前只内置 Java。

因此，多语言扩展架构变得更清楚了，但现成功能范围反而缩小。

### 10.2 CodeQL/LSP 精确关系能力不在现役链路中

CodeSearch 可以使用：

- JDT.LS；
- CodeQL；
- caller/callee；
- graph role；
- 文件和包约束；
- 更复杂结构条件的 `code_ql` 字段。

CodeSense 当前主要提供：

- `contains`；
- 轻量 `calls`；
- `hop` / `reach`；
- kind/file/language 过滤；
- annotation/modifier satisfier。

CodeQL 和 LSP 已经被放入 `legacy/`，不参加现役构建和查询。

因此，新系统部署简单很多，但复杂虚分派、泛型调用、外部库调用以及任意 CodeQL 结构条件暂时没有等价能力。

### 10.3 Cluster 与在线 embedding 精排被移除

CodeSearch 的 Intention Executor 是：

```text
Cluster
  ↓
Embedding
  ↓
Gray-zone LLM Judge
```

CodeSense 当前的主要意图过滤方式是：

```text
廉价算子缩小候选
  ↓
LLM Judge 最终候选
```

旧版完整的聚类分层和在线 term embedding 决策流水线没有保留在现役实现中。这既是运行时简化，也是功能取舍。

### 10.4 精确代码片段、代码行和 regex 查询不再是一等能力

CodeSearch 的 Surface 条件显式覆盖：

- code element；
- code line；
- code snippet；
- exact search；
- regex/fuzzy search。

CodeSense 现役索引主要查询结构化声明字段：

- name；
- signature；
- container；
- doc；
- annotation；
- annotation argument；
- modifier。

方法体完整代码、任意代码行、regex 和 code snippet 搜索目前没有成为 QL 的一等 satisfier。

因此，对于“查找一个明确代码片段”这类任务，旧系统的能力面更宽；CodeSense 更聚焦于符号级语义和结构查询。

### 10.5 部分旧关系谓词没有完整映射

CodeSearch 具有显式的：

- entry point；
- leaf；
- isolate；
- caller/callee；
- file/package；
- arbitrary CodeQL。

CodeSense 可以使用 `degree`、`only`、`hop` 等算子组合表达其中一部分，但并非所有旧字段都有直接等价物，尤其 arbitrary CodeQL 目前没有替代能力。

## 十一、CodeSense 新增加的核心能力

CodeSense 相比 CodeSearch 真正新增的能力包括：

1. **查询脚本成为正式产物**：脚本可以阅读、修改、重跑和调试。
2. **统一的代码子图片段类型**：结果可以同时包含节点、边、路径和证据。
3. **任意算子组合及控制流**：查询不再被三阶段 schema 限制。
4. **真正的图路径结果**：不只是判断候选是否满足 caller/callee，而是保留连接路径。
5. **基于边置信度的查询**：调用边记录解析来源和置信度。
6. **模型失败自动降级**：没有 key、认证失败或生成失败时自动使用 lexical 路径。
7. **完全无模型的查询路径**：查询不再强依赖 LLM/SemCon。
8. **构建期词汇接地**：大向量模型不进入查询阶段。
9. **正式 CLI 和索引发现机制**：初始化一次后，可在仓库任意子目录查询。
10. **明确的语言扩展协议**：增加新语言原则上只需实现一个 adapter。
11. **查询层标准库隔离**：`codesense/ql/` 契约上只依赖 Python 标准库，可以独立测试。
12. **统一的字段级评分**：名称、注解、签名和文档等信号在同一证据模型中计算。

## 十二、提交历史反映的改造阶段

### 12.1 阶段一：工程化整理，主要思路尚未变化

约发生在 7 月 29—30 日：

- `f87175d`：移除大文件和实验产物；
- `ac928f9`：调整配置和密钥管理；
- `7a7b937`：将 relation filter 抽象为注册表；
- `a329aaf`：分离入口胶水和业务逻辑；
- `6fbc659`：将 Python 代码收入 `codesense/` 包；
- `a016942`：增加 `pyproject.toml`；
- `648c776`：建立测试、评估和实验目录；
- `ab1915c`：重写 README 和架构文档。

这一阶段主要改善可安装性、可测试性和可维护性。如果改造停在这里，仍然可以认为是同一个系统的工程化重构。

### 12.2 阶段二：重新定义查询模型

从 `7f9cf1e` 开始：

- 新增 `docs/design/`；
- 不再让新设计迁就现有 Surface/Relation/Intention Executor；
- 引入 `Frag`；
- 引入统一算子；
- 重新定义词汇接地；
- 重新定义代码图基底和 CodeQL 的位置。

`e69552a` 的提交说明是“撤掉受现有实现约束的四处妥协”，这说明从这一阶段开始，目标已经不再是整理旧实现，而是从新的设计目标反推实现。

### 12.3 阶段三：归档旧实现，新 QL 落地

主要提交包括：

- `68b2ee7`：引入 `Frag` 数据模型；
- `5c40162`：建立存储抽象；
- `2053444`：实现 satisfier、组合策略和 unit evaluation；
- `84fc1d2`：实现 `hop` 和 `reach`；
- `50cf4a7`：使用手写查询验证算子集合；
- `3039a8a`：将旧实现移动到 `legacy/`。

`3039a8a` 是明确的实现分界线。此后旧系统不再参与构建、lint 和测试。

### 12.4 阶段四：端到端产品化

主要提交包括：

- `9948d9a`：提取注解和元注解；
- `9c47caa`：提取修饰符；
- `7dfbb12`：建立不依赖项目构建的调用图；
- `ecef319`：实现代价规划器；
- `02997ba`：允许 LLM 直接生成查询脚本；
- `221beea`：实现 `Project.build/open/search`；
- `e996bb6`：引入语言 adapter、分词和真实分层；
- `26deecf`：实现正式 CLI；
- `63edb84`：记录在 Netty 上的实测结果。

这一阶段形成了当前可以实际使用的 CodeSense。

## 十三、最终判断

### 13.1 没有改变的目标

两者都在做：

> 将自然语言意图转化为针对代码库的结构化、可解释搜索。

CodeSense 也保留了 CodeSearch 最重要的设计认识：

- Surface、Relation 和 Intention 信号需要区分；
- 应该先做便宜的召回，再进行昂贵判断；
- 词法、图关系和语义判断需要组合；
- 查询结果必须保留证据；
- 代码搜索不能被简化成单一向量相似度排序。

### 13.2 确实改变的核心路线

核心路线的变化可以概括为：

1. 从固定流水线变成可执行查询语言；
2. 从候选列表变成带证据的代码子图；
3. 从 LLM 填充 schema 变成 LLM 可以直接写程序，也可以只做 NLP；
4. 从依赖重型分析与在线模型，变成轻量建库和构建期词汇接地。

### 13.3 CodeSense 并非 CodeSearch 的完整功能超集

CodeSense 在核心抽象、可组合性、可解释性、部署方式和使用体验上更加统一，但当前确实牺牲或尚未恢复以下能力：

- 多语言现成支持；
- CodeQL/LSP 精确关系；
- 任意 CodeQL 条件；
- 在线 cluster + embedding 精排；
- code line、snippet 和 regex 搜索；
- 部分显式关系谓词。

因此，更准确的评价不是“CodeSense 对 CodeSearch 进行了全面升级”，而是：

> CodeSense 使用一个功能面暂时较窄，但统一、可组合、可运行的核心，替换了 CodeSearch 功能较宽但分散、流水线耦合较强的能力集合。

如果后续目标是构建真正可扩展、可供 Agent 编程调用的代码查询引擎，CodeSense 的方向更合理；如果目标是立即覆盖多语言、精确 CodeQL 和已有 embedding 精排实验，CodeSearch 当前仍然保留更多现成功能。

## 十四、延伸阅读

- [CodeSense README](../README.md)
- [设计文档总览](design/README.md)
- [设计动机](design/01-motivation.md)
- [总体设计](design/02-overview.md)
- [脚本与执行](design/06-script-and-execution.md)
- [旧实现到新设计的映射](design/07-mapping-to-current.md)
- [词汇接地](design/09-grounding.md)
- [代码图设计](design/10-graph.md)
- [当前端到端实测报告](report-pipeline.md)
- [项目接手说明](../HANDOFF.md)
