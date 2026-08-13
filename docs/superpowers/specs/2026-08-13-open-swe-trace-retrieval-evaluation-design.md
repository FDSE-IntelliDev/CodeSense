# CodeSense Open-SWE-Traces Java 代码检索实验设计

> 状态：设计已确认，待实现
>
> 日期：2026-08-13
>
> 范围：从 Open-SWE-Traces 的 Java 轨迹生成查询，评估 CodeSense 对补丁相关生产代码文件的检索准确率

## 1. 目标

本实验只回答一个问题：面对真实 Coding Agent 的问题解决轨迹，CodeSense 能否在较大的
Java 仓库中，把需要修改的生产代码文件排进前 20 个搜索结果。

当前阶段不评估补丁生成、Issue 修复成功率或测试文件定位，也不再保留 SWE-bench
`problem_statement` 直接生成查询的路径。唯一数据源是 Open-SWE-Traces 的 Java 子集，
查询从 trace 中已有的搜索行为及其前序上下文产生。

实验有三项主要输出：

1. 一份可复用的 trace → query 规范化数据集；
2. `lexical`、`planned`、`codegen` 三条搜索 route 的 Top-20 检索结果；
3. 以补丁修改的既有生产 Java 文件为 gold 的 Recall、Precision、MRR、turn 和耗时记录。

## 2. 范围边界

### 2.1 本次实现

- 只消费 Open-SWE-Traces 中 `language == "java"` 的记录；
- 从 trace 中识别代码搜索 episode，并输出最终可执行的自然语言 query；
- 在 trace 对应的精确项目快照上构建一次 lexical grounding 索引；
- 对每条 query 分别运行三条搜索 route；
- 只评估 gold 生产代码文件的检索准确率；
- 自动归档输入、配置、日志、逐条结果和聚合指标。

### 2.2 本次不实现

- 不接入 TraceElephant；
- 不接入 SWE-bench `problem_statement` 直接生成 query 的旧路径；
- 不评估补丁内容、补丁新增文件、测试文件或最终 Issue 是否解决；
- 不做 query 人工筛选、质量打分或二阶段精修；
- 不训练 finetune grounding，项目索引策略固定为 `lexical`；
- 不把 Open-SWE-Traces 的 model patch 当作 gold 或 query 上下文；
- 不下载、扫描额外开源仓库来生成通用词表。

## 3. 数据源和评估单元

Open-SWE-Traces 的一条记录包含 `instance_id`、`repo`、`language`、
`trajectory_id`、完整 `trajectory`、`resolved` 和带 reference patch 的 `metadata`。
同一个 PR 可能由不同 Agent 或模型产生多条 trajectory，因此原始行不是独立项目样本。

数据源固定为 Hugging Face 上 `nvidia/Open-SWE-Traces` 的四个 Java 来源组合：

```text
openhands / minimax_m25
openhands / qwen35
sweagent / minimax_m25
sweagent / qwen35
```

正式运行必须在配置中固定 Hugging Face dataset revision，不能只写 `main`。数据加载层把
记录作为迭代器交给 adapter，adapter 不绑定下载协议；首轮运行缓存最终入选的完整原始记录，
避免后续 query mining 重复读取整个远程数据集。

本实验区分四种标识：

```text
ProjectSnapshot = (repo, base_commit)
Trace           = trajectory_id
IssueInstance   = instance_id
Query           = (trajectory_id, episode_index)
```

- 项目代码量、checkout 和索引缓存以 `ProjectSnapshot` 为单位；
- gold 以 `IssueInstance` 为单位；
- trace provenance 以 `Trace` 为单位；
- 检索与逐条评分以 `Query` 为单位。

适配器必须从轨迹记录中取得或解析 `base_commit`。无法唯一确定提交、无法 checkout、
reference patch 不可解析，或者 gold 中不存在既有生产 Java 文件的记录，标记为不可评估，
不得静默使用仓库最新版本替代。

## 4. 大型 Java 项目筛选

### 4.1 为什么不使用仓库体积

Git 对象、构建产物、图片、依赖缓存、文档和历史都会扩大磁盘体积，却不增加 CodeSense
面对的候选代码元素。GitHub Star 也与检索空间无关。因此“大仓”定义为 CodeSense 在精确
`base_commit` 上实际可搜索的生产 Java 代码空间。

### 4.2 统计口径

项目预检必须复用 CodeSense 的 Java 文件发现规则：扫描 `*.java`，并跳过
`/test/`、`/tests/`、`/generated/`、`/target/`、`/build/`、`/example/`。

记录三个规模量：

- `java_files`：可扫描 Java 文件数；
- `java_nonblank_lines`：这些文件的非空物理行数；
- `indexed_symbols`：构建 lexical 索引后得到的代码元素数。

前两个值用于索引前廉价过滤，第三个值用于索引后校验。项目进入主实验必须同时满足：

```text
java_files >= 200
java_nonblank_lines >= 50_000
indexed_symbols >= 5_000
```

### 4.3 规模分层和资源保护

| 层级 | 可搜索 Java 非空行数 | 首轮处理 |
|---|---:|---|
| Large | 50,000–149,999 | 纳入 |
| XL | 150,000–499,999 | 纳入 |
| XXL | 500,000–2,000,000 | 纳入 |
| Deferred | 超过 2,000,000 | 暂缓，留给资源压力实验 |

`Deferred` 不是无效数据，只是不进入首轮准确率实验，避免超大仓的 checkout、解析和索引
成本阻塞整个批次。所有通过和拒绝的决定都写入 `project-stats.jsonl`，至少包含三个规模量、
层级、decision 和 reason。

规模判断必须发生在读取 gold 文件和运行搜索之前。不能因为 reference patch 容易、trace
完整或者 CodeSense 成绩好而选择项目。

### 4.4 分层采样

候选顺序由 `sha256(random_seed + repo + base_commit + instance_id + trajectory_id)` 固定。
首轮目标为 12 个不同仓库、24 个 trace：

- Large、XL、XXL 各选择 4 个不同仓库；
- 每个仓库最多 2 个不同 `instance_id`；
- 每个 `instance_id` 首轮最多选择 1 条 trajectory；
- 四种 Agent/模型组合目标各 6 条，选择时优先当前计数最少的组合；
- 某层项目不足时从更大层级补充，不降低最低规模门槛。

先选择 repository，再选择 issue 和 trajectory，避免轨迹数量多的仓库主导样本。
如果合法候选不足 24 条，运行不重复使用同一个 instance，而是明确记录 shortfall。固定随机
种子，并归档完整候选表、淘汰原因和最终样本清单。

## 5. Trace 适配

`evaluation/trace_adapters/open_swe_traces.py` 只负责把数据源格式转成通用内部对象，
不调用 LLM，也不运行 CodeSense。主要职责是：

1. 读取 Java 记录并保留数据集 subset、split、Agent 和模型 provenance；
2. 解析 issue statement、`base_commit` 和 trajectory events；
3. 从 reference patch 提取 gold 修改文件；
4. 分类已有文件、新增文件、生产代码文件和测试文件；
5. 输出统一的 `TraceCase`。

主 gold 定义为 reference patch 中在 `base_commit` 已存在、且符合 CodeSense Java 扫描规则的
生产 `.java` 文件。新增文件忽略；测试文件单独记录但不进入主 gold。若主 gold 为空，该
instance 不进入主指标。

## 6. Query mining

### 6.1 输入边界

`evaluation/query_mining.py` 是通用模块，输入 `TraceCase`，直接输出最终的
`PreparedQuery`。它可以抽取原始搜索意图，也可以让 LLM 生成更适合 CodeSense 的
自然语言 query，但不会输出需要人工二次筛选的中间候选。

对某个搜索动作锚点，LLM 只能看到：

```text
issue statement
+ 当前锚点之前的 trace events
+ 当前原始搜索动作
```

严格禁止提供：

- 当前动作的 tool output；
- 当前锚点之后的 trace；
- reference patch 或 gold 文件；
- model patch；
- 后续成功搜索、编辑或测试结果。

这个 prefix-only 规则防止 query 泄露未来信息。

### 6.2 搜索 episode

模块识别 grep、find、文件查看、符号查询等以定位代码为目的的动作。连续围绕同一意图的
导航动作合并为一个 episode；搜索目标明显变化时开启新 episode。因此一条 trace 可以产生
多条 query，但不能把每次重复 grep 都当成独立样本。

每个 episode 保留：

- trace event 起止位置；
- 原始动作和脱敏后的 prefix；
- `extracted` 或 `generated` 生成策略；
- 最终 query；
- query mining 的 model、prompt version 和耗时。

### 6.3 抽取与生成

如果 trace 已明确表达自然语言代码检索意图，可以规范化后直接抽取。类似
`grep -n 'class Complement' /testbed/...` 的命令依赖路径、工具语法和已知符号，不适合作为
最终查询，必须根据允许的 prefix 上下文生成自然语言 query。

生成结果必须满足：

- 英语自然语言；
- 描述想找的行为、职责或代码关系；
- 不包含 gold、补丁或未来事件才暴露的信息；
- 不包含 shell 命令和 `/testbed` 绝对路径；
- 非空且能直接传给 `Project.search()`。

结构校验失败时允许一次同 prompt 重试；仍失败则记录 `query_mining_failed` 并跳过该
episode，不启用另一条 statement-only 路径兜底。

## 7. 通用数据模型

使用少量稳定对象隔离数据源、query mining 和搜索执行：

```text
TraceCase
  dataset provenance
  repo / base_commit / instance_id / trajectory_id
  issue_statement
  events
  gold production files
  ignored test / added files

PreparedQuery
  query_id
  trace identity and episode span
  final query
  strategy: extracted | generated
  raw action provenance
  prompt/model provenance

SearchAttempt
  query_id
  requested_route / effective_route
  attempt
  top-20 elements and first file ranks
  timings / turn count / fallback / error

EvaluationReport
  per-query metrics
  route summaries
  repository and size-band summaries
```

`query_mining` 的产物 schema 就是搜索 harness 的输入，不再增加 benchmark 专属转换层。

## 8. 索引和搜索执行

### 8.1 索引

每个 `ProjectSnapshot` 只构建一次索引：

```python
Project.build(root, index_dir=index_dir, strategy="lexical", llm=llm)
```

后续 query 通过 `Project.open()` 复用索引。索引身份至少覆盖 `repo`、`base_commit`、
CodeSense commit 和索引文件内容 fingerprint。不得按仓库名复用不同提交的索引。

### 8.2 三条 route

同一个 query 在相同项目快照、索引和 Top-K 下运行：

- `route="lexical"`：1 次；
- `route="planned"`：3 次独立 attempt；
- `route="codegen"`：3 次独立 attempt。

项目按串行执行，单项目内三条 route 最多使用 3 个 worker 并行。一个 route 的三个 attempt
在该 route 内顺序执行，以避免过度占用用户进程的 CPU、内存和 LLM 并发。项目词表和只读
上下文在 route 之间共享，不重复加载索引。

每次搜索固定 `limit=20`，保存 requested route、effective route 和 fallback reason。LLM
route 降级到 lexical 时保留该结果供诊断，但不能计入原 route 的正常成绩；聚合报告分别给出
成功执行指标和 fallback rate。

## 9. 排名和指标

### 9.1 元素结果映射到文件

CodeSense 返回代码元素。本实验同时保存原始 Top-20 元素和文件级排名：遍历元素排名，
同一文件只保留首次出现的位置。文件级列表不重新填充到 20 个不同文件，因此 element
Top-20 是固定搜索预算，file rank 是它的投影。

### 9.2 主指标

在 `K ∈ {1, 3, 5, 10, 20}` 上计算：

```text
Recall@K = 命中的 gold 文件数 / gold 文件总数

PatchFilePrecision@K =
  Top-K 元素所覆盖的不同文件中，属于 gold 的文件数
  / Top-K 元素所覆盖的不同文件数

MRR@20 = 1 / 第一个 gold 文件的元素排名
```

这里的 Precision 是严格的 patch-file proxy：reference patch 只说明哪些文件确定相关，不能
证明其他检索文件一定不相关。因此报告必须使用 `PatchFilePrecision` 名称，不能把它解释为
完整语义相关性的真实 precision。

同时记录：

- `first_gold_element_rank`；
- `first_gold_file_rank`；
- `all_gold_recalled_at`；
- 搜索 wall-clock time；
- query mining time；
- turn count。

### 9.3 Turn

当前版本把对应 `rollout.jsonl` 中一条非空 JSONL 记录定义为一个 turn。query mining 或
搜索调用能够关联到 rollout 时，记录文件 identity、截止位置和行数；无法关联时写 `null`，
不得用 Open-SWE trajectory event 数冒充 turn。这个定义先作为原始成本指标保留，字段必须
携带 `turn_definition="nonempty_jsonl_line_v1"`。以后改变解析规则时创建新版本，不能覆盖
历史数据。

### 9.4 聚合

报告同时给出三层宏平均：

1. query macro：每条 query 等权；
2. trace macro：先在 trace 内平均，再让每条 trace 等权；
3. repository macro：先在仓库内平均，再让每个仓库等权。

主结论使用 repository macro，并按 Large、XL、XXL 分层展示；query macro 用于反映实际
query 总体表现。这样不会让拥有更多 issue、episode 或 trajectory 的单个仓库主导成绩。

## 10. 模块边界

```text
evaluation/
├── models.py
├── query_mining.py
├── metrics.py
├── aggregate.py
├── harness.py
└── trace_adapters/
    ├── __init__.py
    └── open_swe_traces.py

scripts/
└── run_open_swe_trace_retrieval.py
```

- `models.py`：上述通用数据对象和 JSON 序列化；
- `open_swe_traces.py`：数据源字段、patch 和 trajectory 适配；
- `query_mining.py`：episode 识别、prefix-only prompt 和最终 query 生成；
- `metrics.py`：单次搜索的纯函数指标；
- `aggregate.py`：query、trace、repository 和 size band 聚合；
- `harness.py`：checkout、规模筛选、索引缓存、并发、运行和归档编排；
- `scripts/run_open_swe_trace_retrieval.py`：只解析命令行并调用 harness，不放业务逻辑。

现有 `scripts/debug_search.py` 保持为手工 Python API 调试入口，不与实验批处理脚本合并。

## 11. 实验配置和产物

首轮实验目录遵循现有规范：

```text
experiments/EXP-0001-open-swe-java-trace-retrieval/
├── config.yaml
└── README.md
```

`config.yaml` 保存完整参数快照，不引用代码默认值；README 在运行前写问题、假设和对比方式，
运行后再填写结果与结论。正式运行要求 clean worktree。

大体积运行产物写入不进 Git 的 `runs/<run-id>/`：

```text
manifest.json
config.snapshot.yaml
selected-traces.jsonl
project-stats.jsonl
prepared-queries.jsonl
search-attempts.jsonl
per-query-metrics.jsonl
summary.json
run.log
```

每条结果都能追溯到 dataset subset/split、trajectory、项目提交、CodeSense 提交、索引
fingerprint、query prompt version、LLM model、route、attempt 和运行时间。失败作为结构化记录
保留，批次继续执行；只有配置、schema 或归档目录不可用等全局错误才终止整次运行。

## 12. 缓存和可复现性

缓存分为三层：

1. repository checkout：`repo + base_commit`；
2. CodeSense index：项目快照 + CodeSense commit + build parameters；
3. query/search：完整输入、prompt/model、route、attempt、index fingerprint。

任何影响输出的内容都进入 cache key。query mining 不得因最终 query 文本相同而丢失
episode provenance；搜索缓存也不得跨不同 index fingerprint 复用。

正式归档记录：Git commit、`git_dirty`、Python 版本、依赖版本、操作系统、模型、prompt
version、随机种子和完整配置。工作区不干净时允许 `--dry-run` 做适配检查，但拒绝标记为
formal run。

## 13. 错误处理

单个项目或 trace 的失败使用固定阶段和 reason：

```text
dataset_invalid
base_commit_missing
checkout_failed
project_too_small
project_deferred
index_failed
gold_empty
query_mining_failed
search_failed
route_fallback
```

失败记录保留输入 identity、stage、异常类型和简短错误，不能把异常全文或密钥写入产物。
项目失败时跳过其余 query；单条 query/route 失败时其余工作继续。

## 14. 测试和验收

自动化测试使用小型合成 trace、临时 Java 仓库和 stub LLM，不依赖下载完整数据集或真实
OpenAI 调用。至少覆盖：

- Open-SWE-Traces 字段、base commit 和 reference patch 解析；
- 测试文件、新增文件和非 Java 文件不进入主 gold；
- CodeSense skip rules 与项目预检统计一致；
- 三个规模门槛、分层边界和 Deferred 决策；
- 同一搜索意图连续动作合并，不同意图拆成多条 query；
- query prompt 不包含当前 tool output、未来 trace 或 patch；
- 不适合抽取的 shell/path 动作进入 generated 路径；
- Top-20 元素去重投影、Recall、PatchFilePrecision 和 MRR；
- 三层宏平均不受某个仓库 query 数量支配；
- route fallback 不混入正常 route 成绩；
- cache key 对项目提交、索引、prompt、model、route 和 attempt 敏感；
- 归档可以从 JSONL 中断处恢复而不重复已完成 attempt。

实现完成后执行：

```text
conda run -n codesearch ruff check .
conda run -n codesearch ruff format --check .
conda run -n codesearch pytest
```

首轮真实验收还要求：12 个大型 Java 项目样本表可追溯、目标 24 条 trace 成功产生 query；
如果候选不足，shortfall 有明确记录。三条 route 均生成 Top-20 结果和完整 provenance，且
汇总能够分别回答总体、仓库规模和 route 的检索表现。

## 15. 实施顺序

1. 建立通用 models、纯函数指标和聚合；
2. 实现 Open-SWE-Traces adapter 与 gold 分类；
3. 实现项目规模预检、分层采样和快照缓存；
4. 实现 prefix-safe query mining；
5. 实现三 route 搜索 harness、缓存和归档；
6. 建立首轮实验目录并先写问题与假设；
7. 运行小样本 dry-run，确认 provenance 与泄露边界；
8. 执行正式实验并只在运行后填写结果和结论。

## 16. 数据源

- Open-SWE-Traces：<https://huggingface.co/datasets/nvidia/Open-SWE-Traces>
