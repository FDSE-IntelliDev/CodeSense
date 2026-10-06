# CodeSense 当前搜索流程：init、lexical、planned 与 codegen

> **状态：现役实现。** 本文同步当前 `codesense/` 与 `codesense/ql/` 的行为，
> 以 `Project.build()`、`Project.open()`、`Project.search()` 和
> `codesense.search.search()` 为准。
>
> [`search-pipeline.md`](search-pipeline.md) 中的 SemCon → SemQL → 三执行器流程
> 已经在重写中被迭代淘汰，仅保留作历史材料；它不参与当前构建、搜索和测试。

本文用一次真实评测解释三条搜索路线在每个阶段分别接收什么、做什么、产出什么。
三条路线共享同一份索引、QL 算子和结果收尾逻辑，区别只在于如何把自然语言 query
变成候选 `Frag`：

- `lexical`：不用 LLM，直接从 query 取词并检索。
- `planned`：LLM 只做结构化语义理解，统计模块校验并编排执行计划。
- `codegen`：LLM 直接生成受限 QL Python 脚本。

## 0. 示例与阅读边界

本文采用 `outputs/open_swe_traces/codesense-evaluation.json` 中当前效果最完整的一条
真实记录。该报告生成于 2026-10-06，配置为 Top 20、lexical grounding、
`qwen3.7-plus`，三条 requested route 都没有降级，并且 query-answer 文件召回率均为
`1.0`。

```text
repository: decorators-squad/eo-yaml
commit:     95a4860ccef9fb5ff1e857bb4be320003b9a4279
query:      Find where YAML scalar strings are read and surrounding escaping
            quotes or apostrophes are stripped from the value.
gold files:
  - src/main/java/com/amihaiemil/eoyaml/ReadLiteralBlockScalar.java
  - src/main/java/com/amihaiemil/eoyaml/ReadPlainScalarKey.java
```

这里的评测按命中文件去重后计算文件指标，而 CodeSense 的公共返回仍是 `Hit` 列表，
每个 `Hit` 对应一个代码元素。也就是说，同一文件里的类、构造函数和方法可能分别占据
一个名次；不能把 `Hit.file` 字段误解成“搜索结果本身是文件节点”。只有显式
`target=file` 或 route 返回文件节点时，公共结果才具有文件目标语义。

## 1. init：仓库如何变成可搜索项目

init 分成“一次性构建”和“进程内装载”两部分。前者由 `Project.build()` 完成，
后者由 `Project.open()`、`Project.context` 和 `Project.vocabulary` 延迟完成。

```text
代码仓库
  → 语言适配器扫描源码
  → symbols + postings + repository corpus
  → file nodes + in_file / calls / contains / references / imports / hierarchy edges
  → 项目词表与复合词切分
  → query vocabulary grounding → expansion table
  → meta.json + index.json + expansion.json
  → Index.load() → EvalContext + representative vocabulary
```

### 1.1 扫描与声明解析

| 输入 | 处理 | 产物 |
|---|---|---|
| 仓库根目录、已注册语言适配器 | 遍历受支持的源码文件；当前内置语言是 Java，使用 tree-sitter 解析声明和引用 | `Declaration`、`ReferenceUse`、语言和解析统计 |
| 每个声明 | 保留受支持的 kind，并记录名称、文件、行号、签名、容器、文档、修饰符和语言 | 稳定 `symbol_id` 的声明节点 |
| 声明文本 | 对 name、signature、container、doc、annotation 等字段切词 | term → `(symbol_id, field, tf)` 的 postings |
| 同一次解析得到的声明 | 生成项目语料，供可选的 grounding/finetune 使用 | `sentences`；不需要再次读取源码 |

单个文件解析失败只增加 `stats.failed` 并跳过该文件，不会让整个索引构建失败。

### 1.2 文件节点与代码图

扫描完全部声明后，构建器再为每个成功解析的文件创建一个 `kind="file"` 的
`Element`。文件节点排在声明节点之后，以保持已有声明 ID 稳定。随后建立：

- 声明 → 文件的 `in_file` 边；
- 容器关系 `contains`；
- 调用关系 `calls`；
- 引用和导入关系 `references`、`imports`；
- 语言适配器派生的 `extends`、`implements`、`overrides` 等关系。

所有边会去重，并保留 `confidence`、`provenance` 和可用时的 source site。
图既服务于 planned/codegen 的显式关系操作，也服务于 lexical 的结构邻近性加权。

### 1.3 项目词表与复合词切分

postings 建好后，系统得到整个项目的词表及每个词的 document frequency（`df`）。
复合词切分必须在全仓词表形成后执行：例如项目中同时大量出现 `io` 和 `stat` 时，
`iostat` 才有项目内证据可以被进一步切分。切分结果直接细化既有 postings，
不重新解析源码。

`df` 不只是展示数据。planned 会用它估计一个 term 或 unit 会命中多少符号，进而决定
执行顺序；`Index.vocabulary()` 也按 `df` 降序输出代表性项目词表。

### 1.4 词表接地与 expansion table

索引本身坚持精确 term，而接地阶段负责把 query 中的一般词映射到项目实际拼写。例如
query 写 `buffer`，项目可能写 `buf`；query 写 `backpressure`，项目可能写
`watermark`。

构建时可选三种 grounding strategy：

| strategy | 做什么 | 查询时依赖 |
|---|---|---|
| `lexical` | 基于前缀、缩写和子序列规则建立一般词 → 项目词映射 | 无模型，确定性 |
| `vectors` | 在 lexical 基础上加入受项目词表约束的预训练向量近邻 | 只在构建时加载向量模型 |
| `finetune` | 在 lexical 基础上使用仓库语料训练/适配紧凑项目模型 | 只在构建时训练或加载模型 |

接地只保留具有区分度的项目 term，并限制每个一般词的映射数量。最终输出
`expansion.json`；搜索时不会再加载大型 embedding 模型。

### 1.5 保存与进程内装载

索引目录有三个独立生命周期的文件：

| 文件 | 内容 |
|---|---|
| `meta.json` | 项目、源码根目录、commit、构建时间、语言、format version 和统计量 |
| `index.json` | symbols、postings、edges、声明数量 |
| `expansion.json` | 一般 query 词到项目实际 term 的接地映射 |

`Project.open()` 通过 `Index.load()` 校验 format version 并读取这三类数据。
第一次访问 `Project.context` 时，索引才会物化为 `EvalContext`：

- `SymbolStore`：按 ID 读取代码元素；
- `PostingIndex`：精确 term 和字段级 postings；
- `ExpansionTable`：构建期接地映射与语言/框架事实；
- `EdgeStore`：代码图；
- `Judge`：有 LLM 时注入真实 judge，否则使用 `NullJudge`。

第一次访问 `Project.vocabulary` 时，系统从 `Index.vocabulary()` 中选择最多 1200 个、
`df >= 2` 的代表性词。这个列表只给 planned 的 query understanding 使用；当前
codegen 提示词不携带项目词表。

本例加载的 commit-aware 索引包含 410 个元素，其中 365 个声明、45 个文件节点，
另有 5,335 条 postings、2,227 条边和 67 个 grounded terms。

## 2. 三条路线共用的入口

调用链为：

```text
Project.search(query, route, limit, target, ...)
  → 取得 lazy EvalContext 与 representative vocabulary
  → codesense.search.search(...)
  → 选择 lexical / planned / codegen runner
```

`search()` 在 route 执行前完成以下工作：

| 阶段 | 处理 | 产物 |
|---|---|---|
| 参数校验 | route 必须属于 `codegen/planned/lexical`；repair 次数不得为负 | 可执行的请求参数 |
| target 规范化 | 调用方显式 target 会变成稳定有序的 kind tuple；未显式指定时暂不猜测 | `requested_target` 或 `None` |
| judge 包装 | `judge=True` 且配置真实 judge 时，增加 query 内判定缓存 | 可复用判定结果的 `EvalContext` |
| 无 LLM 降级 | requested route 不是 lexical，但没有 LLM 配置时，立即改走 lexical | `route.fallback` trace |
| runner 分派 | 按 route 选择 `_lexical`、`_planned` 或 `_codegen` | route-local `Frag`、script、notes、target |

三条 runner 的共同契约是：

```python
(frag, script, notes, route_target)
```

`Frag` 是带节点、边、证据和路径见证的代码子图；它不是单纯 ID 列表。route 只负责
构造候选 `Frag`，最终 target、可选语义判定、排序和 `SearchResult` 由共享逻辑处理。

## 3. lexical：直接从 query 取词

lexical 不调用 LLM，也不产生 QL script。

| 阶段 | 输入 | 处理 | 产物 |
|---|---|---|---|
| 目标推断 | 显式 target 或原始 query | 仅识别明确要求返回文件的中英文句式；其余保持默认非文件结果 | `effective_target` |
| query 取词 | 原始 query、postings、expansion table | 提取英文词，casefold，去重，移除短词/停用词；只保留能精确命中或能被 expansion 接地的词 | 本例得到 `find, yaml, scalar, read, value` |
| 构造单元 | 保留下来的词 | 建一个名为 `query` 的 `QueryUnit`，内部只有一个 `LexicalSatisfier`，默认权重 0.5 | 单一 lexical unit |
| 倒排检索 | QueryUnit、EvalContext | `eval_unit()` 解析 exact/expansion surfaces，读取字段 postings，按 ICF、字段权重和 term 权重产生证据 | 初始 scored `Frag` |
| 结构邻近性 | 初始 Frag、代码图 | 取 Top 20 为 seeds，在 `calls/contains` 上双向走 1–2 跳；只给原候选中靠近 seeds 的节点增加 0.6 倍结构分，不引入纯图节点 | 带 `coherence` 证据的 Frag |
| route 输出 | 加权 Frag | 不生成脚本，notes 记录真正参与匹配的词 | `(frag, "", notes, target)` |

本例 lexical 的实际表现：

- Top 20 命中 9 个不同文件，observed file precision 为 `0.2222`；
- 两个 query gold 文件都被命中，file recall 为 `1.0`；
- 第一个 query gold 出现在 rank 7，因此 MRR 为 `0.1429`；
- rank 1 是 `ReadPlainScalarValue` 构造函数，证据为
  `value@name, scalar@name, read@name`；
- 两个 gold 首次分别出现在 rank 7 的 `ReadLiteralBlockScalar` 构造函数和
  rank 8 的 `ReadPlainScalarKey` 构造函数。

这说明 lexical 的优势是快且稳定，本例仅约 8 ms；代价是 query 中没有进入索引或
expansion table 的 `quotes/apostrophes/stripped/escaping` 不会产生任何检索约束。

## 4. planned：模型提议，统计校验和编排

planned 的边界是“语义问题交给模型，项目事实交给索引统计”。模型不直接决定最终
执行脚本。

| 阶段 | 输入 | 处理 | 产物 |
|---|---|---|---|
| 结构化理解 | query、项目名、最多 1200 个 `(term, df)` | `QueryUnderstanding` 请求 strict JSON Schema；模型给出 semantic units、terms、relations、targets、annotations、criterion；schema 无效时默认允许一次修复 | `QueryUnderstandingResult` |
| 结构转换 | understanding result | term casefold 并保留 source/weight/reason；unit 变成 term groups；relation 区分 unit↔unit 与 `$result` endpoint | terms、groups、relations、result relation、route target |
| 术语接地 | 每个模型 term、EvalContext | `TermResolver` 先查 casefold exact surface，再查 expansion；只接受最终确有 postings 的 surface，并在一次 query 内缓存 surface/postings/symbol IDs | 可执行 term 及其项目内落点 |
| 统计校验 | 模型提出的 groups/relations | `build_spec()` 忽略无法接地的 term；按 postings 重合校验分组凝聚性，按图边密度校验关系；从真实 postings 推断字段和 kind 偏好 | `QuerySpec` 与 validation notes |
| 计划生成 | QuerySpec、df、图统计 | `plan()` 估计候选规模和代价，选择更窄的 unit 起步；便宜约束先执行，`intent` 始终最后；kind 通常作为排序偏好，显式 target 才是硬约束 | 有序 `Plan.steps` 与 reasoning |
| 计划执行 | Plan、EvalContext | `Plan.run()` 逐步执行并记录预计/实际规模和耗时；working set 为空后短路后续步骤 | `State.current` Frag 与 trace |
| 可复现脚本 | Plan、QuerySpec | `to_script()` 渲染与 Plan 等价的 QL Python；测试会对比脚本与 Plan 的符号集合 | 可检查、可重放的 script |

本例结构化理解提出的行为词中，`quotes`、`apostrophes`、`stripped`、`escaping`
在当前索引中无法接地，因此 validation notes 明确记录并忽略它们。保留下来的 unit 是：

```python
read_scalar = QueryUnit(
    "read_scalar",
    concept="model group 'read_scalar', cohesion validated",
    satisfiers=(
        LexicalSatisfier(
            terms=(
                Term("read", weight=0.90),
                Term("scalar", weight=0.90),
                Term("string", weight=0.80),
            ),
            weight=0.255,
        ),
    ),
)
```

实际计划从 `read_scalar` 开始，预计命中 179 行、约占声明的 49%；随后执行
`eval_unit`、`calls/contains` 邻近性加权、method/constructor/class 硬 target 投影和
Top 20。最终脚本是 50 行的编译产物，和 `Plan.run()` 等价。

本例 planned 的实际表现：

- Top 20 命中 8 个不同文件，observed file precision 为 `0.25`；
- 两个 query gold 文件均命中，file recall 为 `1.0`；
- 第一个 gold 位于 rank 2，MRR 为 `0.5`；
- rank 2、3 分别是两个 gold 文件中的构造函数；
- route fidelity 为 true，没有降级；评测耗时约 51.43 s，主要包含 LLM understanding。

planned 的主要价值不是“让模型写得更复杂”，而是把模型建议留下来接受项目统计
校验，并产出可解释的 validation notes、plan reasoning 和等价脚本。

## 5. codegen：模型直接生成受限 QL 脚本

当前 codegen 不接收 representative vocabulary。提示词只包含 query、项目名、符号/边
规模、QL operator contract，以及是否允许真实 semantic judge。

| 阶段 | 输入 | 处理 | 产物 |
|---|---|---|---|
| 脚本生成 | query、operator spec、symbols/edges 数量、judge 状态 | 模型自行拆 concept，创建 QueryUnit，并组合 `eval_unit/only/project/reach/hop/top/intent` 等算子 | 必须赋值给 `answer` 的 Python source |
| 确定性归一化 | 生成 source | 修复可无歧义判断的 singleton tuple contract 形状 | normalized source |
| 静态校验 | source、真实 operator signatures | AST 白名单与命名空间校验；拒绝 import、越界对象访问、未知名称和错误调用签名 | 可执行 source 或精确 diagnostic |
| 静态修复 | source、diagnostic | 静态校验失败时把完整原脚本和错误交给模型；默认最多一次 repair | 修复后的完整 source |
| 受限执行 | 校验后的 source、受限 namespace | namespace 只暴露 `ctx`、QL operators、QueryUnit/Term/satisfiers；`run_script()` 使用执行步数预算 | `answer` 对象 |
| 结果契约 | answer | 必须是非空 `Frag`；其他类型或空 Frag 都视为 codegen 失败 | route-local Frag、source、attempt notes |

一旦开始执行，运行期错误不会再次交给模型修复。这样可以避免已经产生 operator/LLM
副作用后重复整段查询。只有执行前的静态错误允许 repair。

本例 codegen 一次生成并通过校验，核心脚本为：

```python
unit = QueryUnit(
    name="strip_quotes",
    concept="strip quotes or apostrophes from scalar string",
    satisfiers=(
        LexicalSatisfier(terms=(
            Term("strip"), Term("quote"), Term("apostrophe"),
            Term("escape"), Term("scalar"), Term("substring"),
            Term("read"), Term("value"),
        )),
    ),
)
frag = eval_unit(unit, ctx)
methods = only(frag, kind="method")
answer = top(methods, 10)
```

本例 codegen 的实际表现：

- 返回 10 个 method，分布在 8 个不同文件，observed file precision 为 `0.25`；
- 两个 query gold 文件均命中，file recall 为 `1.0`；
- 第一个 gold 位于 rank 2，MRR 为 `0.5`；
- `ReadLiteralBlockScalar.value` 位于 rank 2，`ReadPlainScalarKey.value` 位于 rank 7；
- route fidelity 为 true，`codegen attempts=1`，评测耗时约 24.84 s。

codegen 的脚本比 planned 更直接，也能表达条件分支和循环；相应地，它对模型是否正确
理解 operator 语义更敏感。静态校验能拦住语法和 contract 错误，但不能证明检索逻辑
合理。

## 6. route 执行后的共享收尾流程

无论 route 如何得到 Frag，都会经过同一段收尾逻辑：

```text
route-local Frag
  → 决定 effective target
  → enforce target
  → 可选的低置信度 intent fallback
  → 按 score 排序并截断 limit
  → SearchResult(query, hits, actual route, script, notes, target, elapsed)
```

### 6.1 target 决议与强制执行

target 的优先级是：

1. 调用方显式传入的 target；
2. route 自己给出的 target；
3. lexical 对明确“返回文件”句式的窄规则推断。

`_enforce_target()` 对三条路线完全一致：

- target 为空时移除 file nodes，默认返回声明类代码元素；
- 普通 kind target 对 Frag 做精确 kind 过滤；
- target 包含 `file` 时，通过 `in_file` 把候选投影到所属文件节点；
- 混合 target 会保留直接 kind 命中，并合并文件投影。

### 6.2 可选 intent fallback

只有 `judge=True` 且配置真实 judge 时才可能触发。系统不会无条件对所有候选付费判定，
而是检查 Top `limit` 的尾部得分：候选足够多且最后 5 个相对最高分都低于阈值时，
才对最多 60 个候选调用 `intent()`。同一 query 内的重复判定会被缓存。

### 6.3 排序与 SearchResult

最终节点按 `(-score, symbol_id)` 稳定排序并截断到 `limit`。每个 `Hit` 包含：

- rank、symbol ID、name、kind、file、line、score；
- `why`：分数最高的若干证据，例如 exact/expansion term、命中字段、结构邻近性或 judge verdict。

`SearchResult.route` 是实际产生 hits 的 route；`script` 保存 planned 编译脚本或 codegen
生成脚本；`notes` 保存接地丢弃、计划理由、repair diagnostic 或 fallback 原因。

## 7. 失败与 lexical 降级

降级的目标是“搜索仍可用”，但调用方必须能区分 requested route 成功和 lexical
兜底成功。

| 失败点 | 处理 | 最终可观察结果 |
|---|---|---|
| planned/codegen 请求但没有 LLM | route 执行前直接选择 lexical | `SearchResult.route == "lexical"`，trace 记录 no LLM |
| planned understanding、接地、build_spec、plan 或执行失败 | `_PlannedFailure` 保留 planned 已解析出的 target，然后 lexical 使用该 target 兜底 | notes 记录原异常类型和消息 |
| codegen 无脚本、静态校验/repair 失败、执行报错、answer 类型错误或空 Frag | `_CodegenFailure` 保存最后一版 source 与全部 diagnostics，然后 lexical 兜底 | hits 来自 lexical，但 `script` 仍保存失败 codegen source |
| route 出现其他未分类异常 | 捕获异常并执行 lexical | notes 记录异常，actual route 为 lexical |

降级后不会伪装成原 route 成功：

- `SearchResult.route` 会变成 `lexical`；
- notes 会写明 `planned/codegen failed ...; fell back to lexical`；
- codegen 失败脚本仍保留，便于复现；
- 评测中的 `route_fidelity` 因 actual route 与 requested route 不同而为 false，
  该条不计入 requested route 的完成指标。

lexical 本身返回空 Frag 不会继续无限降级：没有可接地词时 notes 记录原因；有词但无命中
时记录 `no hits for ...`，最后返回空 `SearchResult.hits`。

## 8. 三条路线在本例中的差异

| 维度 | lexical | planned | codegen |
|---|---|---|---|
| LLM 用途 | 不使用 | 只做 strict-schema 语义提议 | 直接生成 QL 脚本 |
| 项目代表词表 | 不进 prompt；运行时用 postings/expansion | 最多 1200 个 `(term, df)` 进 understanding prompt | 当前不进 prompt |
| 统计校验 | query 词必须可查或可扩展 | 接地、group/relation 校验、kind/field 推断、代价规划 | 只有算子执行时自然体现，生成前不做 planned 式统计规划 |
| 结构信号 | 固定 calls/contains 邻近性 boost | 计划内关系、邻近性和 target 投影 | 由生成脚本自行选择 project/reach/hop 等 |
| 可检查产物 | terms、evidence、notes | structured understanding、notes、Plan reasoning、等价 script | 生成 source、repair diagnostics、operator trace |
| 本例耗时 | 约 0.008 s | 约 51.43 s | 约 24.84 s |
| 本例 file recall | 1.0 | 1.0 | 1.0 |
| 本例首个 gold rank | 7 | 2 | 2 |

本例不能推出某条路线在所有 query 上更好，只能说明三条路线如何通过不同中间产物命中
同一组 gold。当前 5 条评测的总体结果也表现出不同权衡：planned 的平均文件召回更高，
codegen 的平均 observed precision 更高，而 lexical 的延迟最低。比较算法时还必须同时检查
route fidelity、索引 commit、gold 路径有效性和 target 语义，不能只看最终 hits。

## 9. 代码入口速查

| 关注点 | 当前实现 |
|---|---|
| 仓库构建与 lazy 装载 | `codesense/project.py` |
| 索引格式与 EvalContext 构造 | `codesense/index.py` |
| 扫描、postings、文件节点与边 | `codesense/indexing/pipeline.py` |
| 构建期词表接地 | `codesense/indexing/grounding.py` |
| 三 route、降级和共享收尾 | `codesense/search.py` |
| planned structured understanding | `codesense/llm/compiler.py`、`codesense/llm/schema.py` |
| planned spec/validation/planning | `codesense/ql/compile/` |
| codegen prompt 与 repair | `codesense/llm/codegen.py` |
| 受限脚本校验与执行 | `codesense/ql/script.py` |
| 术语接地与 query-local cache | `codesense/ql/term_resolution.py` |
| 评测指标与 route fidelity | `scripts/evaluation.py` |
