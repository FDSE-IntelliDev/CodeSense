# Open-SWE-Traces 搜索过程监督 Benchmark 设计

> 状态：设计已确认，待实现
>
> 日期：2026-09-23
>
> 范围：从 resolved 的 Open-SWE-Traces Java 轨迹中提取真正帮助 Agent 定位代码的搜索过程，生成语义 query，并把搜索结果划分为强标注 gold 与弱标注 candidate
>
> 替代方案：本设计替代 `2026-09-14-semantic-trace-benchmark-design.md` 中“一条 trace 一条 query，并以 reference patch 作为 ground truth”的方案；旧文档保留为历史记录

## 1. 背景与问题

上一版 benchmark 让 LLM 根据 issue 和搜索相关 trace 生成一条语义 query，再把 reference patch
修改的生产 Java 文件和函数作为答案。该方案能够避免简单复述 grep 命令，但 patch 表示的是 Issue
最终修复位置，不一定等于代码搜索工具在调查过程中应当返回的位置。要求一次搜索直接找到最终修复文件，
会把“代码定位能力”和“完整 Issue 修复推理能力”混在一起。

新方案把评估单元调整为 **一次真正推动后续调查的搜索 episode**：程序先从搜索工具输出中解析候选
代码位置，再检查这些位置是否在后续轨迹中被 Agent 打开、继续搜索、引用或修改。后续确实使用过的
位置成为强标注 gold；同次搜索返回但没有观察到后续使用的位置保留为弱标注 candidate。LLM 只负责
把当前搜索意图改写成语义 query，不负责生成或判断答案。

目标 query 可以包含精确类名、方法名或技术词作为锚点，但必须同时描述行为、职责、状态变化、故障、
性能影响或副作用。例如：

```text
Find the pool-selection logic where isPoolLifo changes which reusable connection
is selected and can therefore alter request ordering.
```

它比“Locate calls to isPoolLifo”更适合评估 CodeSense 的项目词表接地、语义扩展、向量过滤和组合
算子。

## 2. 目标与非目标

### 2.1 目标

- 只使用 `resolved == 1` 的 Open-SWE-Traces Java 轨迹；
- 一条 trace 可以产生多条 query，但每条 query 必须对应一个独立、可审计的搜索 episode；
- 从工具调用及其返回中确定性解析答案候选，不让 LLM 猜答案；
- 只把后续确实使用过的搜索结果作为强标注 gold；
- 保留同次搜索的其他结果作为弱标注 candidate，供未来采用不同权重；
- LLM 根据原始 issue、当前搜索前的推理和当前搜索动作生成一条英语语义 query；
- 允许 query 使用搜索动作中的精确关键词或代码标识符，但拒绝纯名称、引用、调用或实现关系查找；
- 输出继续采用一行一个 query 的 JSONL，供现有 evaluator 和 viewer 演进使用；
- 当前主评测使用 `recall@20` 和 `observed_precision@20`，明确其标签不是完整相关性判断。

### 2.2 非目标

- 不再使用 reference patch 作为本 benchmark 的主 ground truth；
- 不要求一次搜索直接找到 Issue 的最终修改位置；
- 不把未被后续使用的搜索结果直接判定为错误结果；
- 不把测试文件纳入主 gold 或 candidate；
- 不让 LLM 查看当前搜索结果、后续 trace、最终回答或 reference patch；
- 不在首版运行精确名称、BM25、代码图或其他 baseline 作为第二层难度门禁；
- 不在首版给 candidate 命中设定部分分数；
- 不做 embedding 相似度去重、人工筛选或多轮 LLM 修复；
- 不改变 CodeSense 本身的索引构建和搜索逻辑。

## 3. 方案选择

讨论过三种答案构造方式：

1. **Reference patch gold**：答案稳定，但过于接近最终修复结果，已放弃；
2. **LLM 判断哪些搜索结果有用**：实现简单，但答案不可复现并可能泄漏，已放弃；
3. **确定性后续使用证据 + LLM 语义改写**：答案可复现，query 保持语义性，采用本方案。

LLM 和确定性程序的职责严格分离：

```text
工具结果 + 后续 trace --确定性规则--> gold / candidate

issue + 前置推理 + 当前搜索动作 --LLM--> semantic query
```

## 4. 数据边界与可信来源

| 信息 | 是否进入 LLM prompt | 用途 |
|---|---:|---|
| 完整原始 issue | 是 | 提供问题背景和语义目标 |
| 当前搜索前最近的 assistant 推理 | 是 | 提供本次调查意图 |
| 当前搜索动作及其精确关键词 | 是 | 提供搜索锚点 |
| 当前搜索工具结果 | 否 | 确定性解析候选答案 |
| 当前搜索之后的 trace | 否 | 确定后续使用证据 |
| 最终 assistant 回答 | 否 | 仅保留供人工查看 |
| reference patch | 否 | 可保留为辅助元数据，但不参与本 benchmark |

首版 prompt context 使用当前搜索动作所在 assistant 事件，以及它之前最近一条包含非空自然语言推理
的 assistant 事件。system、user 和 tool output 不进入该上下文。原始 issue 通过独立字段完整提供。
这样既保留当前搜索动机，又避免把长 trace 或答案信息传给模型。

## 5. 总体流程

```text
resolved Java trace
        |
        v
识别并关联 SearchEpisode
        |
        v
解析当前工具结果 --> 原始候选集合
        |
        v
扫描后续事件 --> UsageEvidence
        |
        +--> 有证据的位置 --> answer (gold)
        |
        +--> 其余结果 ------> candidate_answers
        |
        v
issue + 前置推理 + 当前动作 --> LLM --> semantic query
        |
        v
第一层语义门禁 --> 一条 episode 输出一行 JSONL
```

处理顺序必须先构造答案并确认 episode 合格，再调用 LLM，避免为没有监督信号的 episode 消耗模型
调用。一个 episode 失败只跳过该 episode，不丢弃同一 trace 中的其他 episode。

## 6. 搜索 episode 切分

内部对象建议为：

```python
SearchEpisode(
    anchor_event: int,
    action_event: TraceEvent,
    context_events: tuple[TraceEvent, ...],
    result_events: tuple[TraceEvent, ...],
    raw_action: str,
    candidates: tuple[CandidateLocation, ...],
)
```

### 6.1 搜索动作识别

以下动作可以成为 episode anchor：

- 显式 `code_search`、`search_code`、`symbol_search`、`grep`、`rg`、`find` 等工具；
- `execute_bash` 等通用工具中嵌入的 `grep`、`rg` 或 `find` 命令；
- 工具参数以 JSON 字符串、Python literal 或多层引号包装时，递归解析已知 command 字段。

shell 参数只使用 `shlex` 等方式解析，绝不执行 trace 中的命令。非搜索工具不产生 episode，但可以在
后续成为使用证据。

### 6.2 工具结果关联

1. 有 `tool_call_id` 时，按 ID 精确关联调用和结果；
2. 没有 ID 时，将搜索 assistant 事件之后、下一个 assistant 或 user 事件之前的连续 tool 事件
   绑定到当前调用；
3. 一个 assistant 事件含多个调用且结果有 ID 时，每个搜索调用独立形成 episode；
4. 多调用且缺少 ID、无法确定结果归属时，记录 `ambiguous_tool_result` 并跳过，不猜测。

## 7. 候选答案解析

候选位置只从当前 episode 的工具结果中解析：

```python
CandidateLocation(
    file: str,
    functions: tuple[str, ...],
    line: int | None,
    raw_result: str,
)
```

支持文件列表、`path.java:line:text`、结构化 symbol 字段等常见形式。路径统一转换为仓库相对
POSIX 路径，并去掉 workspace/repository 前缀、`./`、`a/`、`b/` 和 `:line` 后缀。

匹配顺序为：

1. 完整规范化路径精确匹配；
2. 只有 suffix 唯一时才使用路径后缀；
3. 只有 basename 唯一时才使用文件名；
4. 仍有歧义则跳过该位置。

测试、样例、构建产物、生成代码和 `/dev/null` 不进入候选集合。首版不对候选数量设置硬门槛；
原始候选数量记录到 provenance，供后续分析宽泛搜索。

### 7.1 函数提取

- `find` 和普通文件列表只能产生文件级候选；
- grep 命中调用语句时默认只记录文件，不把搜索关键词当成函数答案；
- 只有结构化 symbol 字段、明确的方法声明，或后续读取范围能够映射到方法时才记录函数；
- 函数名移除类名前缀、参数签名和返回类型；
- 无法确认函数时保留文件，并令 `functions` 为空。

## 8. UsageEvidence 与 gold 构造

```python
UsageEvidence(
    location: CodeLocation,
    event_index: int,
    kind: Literal["opened", "searched", "edited", "referenced"],
)
```

从当前 episode 结果结束后扫描 trace：

- `opened`：后续工具明确打开或读取该文件；
- `searched`：后续搜索以该文件或函数为范围或关键词；
- `edited`：后续工具修改该文件；
- `referenced`：assistant 推理明确出现完整路径，或唯一的文件与函数组合。

`opened`、`searched` 和 `edited` 可以独立使候选成为 gold。纯 assistant 文本只有包含完整路径或唯一
文件加函数时才能提供证据。工具输出本身不是使用行为，不能单独成为 UsageEvidence。

函数进入 gold 还需满足以下任一条件：后续明确引用/搜索该函数，或者后续读取范围能够映射到该函数。
否则只保留文件级 gold。

如果多个历史 episode 返回同一位置，后续使用默认归属给最近一次返回该位置的 episode，避免一份
证据重复奖励多条 query。

最终集合定义为：

```text
answer = candidates 中具有 UsageEvidence 的位置
candidate_answers = candidates - answer
```

两者互斥。`candidate_answers` 是弱监督、未确认相关的搜索候选，不是负例。

## 9. Query 生成契约

每个具有至少一个 gold 的 episode 单独调用一次 LLM。prompt 必须说明：精确关键词可以作为 anchor，
但最终 query 必须加入语义约束。

LLM 返回：

```json
{
  "status": "valid",
  "query": "Find the pool-selection logic where isPoolLifo changes which reusable connection is selected and can therefore alter request ordering.",
  "reason": "The exact pool-policy term is anchored to its behavioral effect.",
  "anchor_terms": ["isPoolLifo"],
  "semantic_constraints": [
    "changes reusable connection selection",
    "alters request ordering"
  ]
}
```

无法从 issue 和当前 episode 支持语义 query 时返回：

```json
{"status": "invalid trace"}
```

正向 few-shot 继续使用 navigation-state 和 disk-performance 示例。反向示例包括 files containing、
references、implementations of、calls to 和按名称定位类/方法。

## 10. 第一层语义门禁

首版只执行轻量、可解释的第一层门禁：

- `status == "valid"`；
- `query`、`reason`、`anchor_terms` 和 `semantic_constraints` 类型正确；
- query 是单条英文自然语言，不含代码块、shell 命令或 `.java` 文件路径；
- `semantic_constraints` 至少包含一项；
- query 不能只有包含、引用、实现、调用或按名称查找等直接检索意图；
- 精确类名、方法名和技术词允许出现，只要同时存在行为、状态、故障、性能、职责或副作用约束。

旧的 `query_leaks_gold_identifier` 限制应删除，因为正常语义 query 可以使用当前搜索动作中已经出现的
精确标识符。文件路径和当前搜索结果仍不可泄漏。

不运行 BM25、精确名称检索或图搜索来判断 query 难度，也不二次调用 LLM 修复失败结果。

## 11. 去重

在 LLM 调用前按以下签名对同一 trace 的 episode 去重：

```python
(
    normalized_raw_action,
    sorted(answer),
    sorted(candidate_answers),
)
```

签名完全相同则保留靠后的 episode，因为它拥有更多前置调查上下文；其余记录
`duplicate_episode`。首版不做向量相似度或模糊 query 去重。

## 12. 输出数据模型

一条有效 episode 输出一行：

```json
{
  "query_id": "trajectory-id:12",
  "repo": "owner/repo",
  "instance_id": "instance-id",
  "trajectory_id": "trajectory-id",
  "issue_statement": "...",
  "query": "...",
  "answer": [
    {
      "file": "src/main/java/example/Pool.java",
      "functions": ["selectConnection"]
    }
  ],
  "candidate_answers": [
    {
      "file": "src/main/java/example/PoolConfig.java",
      "functions": []
    }
  ],
  "usage_evidence": [
    {
      "file": "src/main/java/example/Pool.java",
      "functions": ["selectConnection"],
      "event_index": 18,
      "kind": "opened"
    }
  ],
  "anchor_terms": ["isPoolLifo"],
  "semantic_constraints": ["connection selection", "request ordering"],
  "source_event_indices": [11, 12],
  "result_event_indices": [13],
  "strategy": "trace-search-generated",
  "source_events": [],
  "provenance": {
    "prompt_version": "trace-search-v2",
    "prompt": "...",
    "model": "...",
    "raw_action": "...",
    "query_reason": "...",
    "episode_candidate_count": 2
  }
}
```

字段语义：

- `answer` 是强标注 gold，至少包含一个生产 Java 文件；
- `candidate_answers` 只包含非 gold 候选；
- `usage_evidence` 解释每个 gold 的确定性来源；
- `source_event_indices` 是实际进入 LLM prompt 的 trace 事件；
- `result_event_indices` 用于答案解析，但不进入 prompt；
- `source_events` 可以保存完整 trace 供 viewer 展示，不代表完整 trace 被发送给 LLM；
- `query_id` 使用 `trajectory_id:anchor_event`，在同一输入上稳定可复现。

`TraceCase.answer` 中旧的 patch-derived 位置可以暂时保留为辅助元数据，降低无关重构成本，但新的
mining 流程不得用它做前置门禁或 query 的 `answer`。

## 13. 评测语义

首版搜索仍采用 Top-20。强标注指标为：

- `recall@20`；
- `observed_precision@20`；
- `MRR`；
- `first_gold_rank`。

名称 `observed_precision` 强调当前只知道哪些结果被 Agent 实际使用，不能证明其他结果不相关。
评测明细同时把每个命中标记为：

```text
gold_hit
candidate_hit
unlabeled_hit
```

当前只有 `gold_hit` 计入正确结果。`candidate_hit` 不计分，也不被解释为确定错误。未来可以在不重新
挖掘 trace 的前提下，为三类命中设置不同权重。汇总同时记录 `episode_candidate_count` 和
`gold_count`，用于识别标签稀疏或原搜索过宽的样本。

## 14. 模块改造范围

### `evaluation/models.py`

- 增加 `CandidateLocation`、`UsageEvidence` 或等价内部模型；
- 扩展 `PreparedQuery` 的 candidate、evidence、anchor、constraint 和事件索引字段；
- 保持对象 JSON 可序列化。

### `evaluation/trace_adapters/open_swe_traces.py`

- 继续过滤 Java 和 strict `resolved == 1`；
- 保留完整 issue、事件和工具调用关联信息；
- 不再以 reference patch 是否可解析决定 case 是否有效；
- 多工具调用不能静默只保留第一项。

### `evaluation/query_mining.py`

- 识别并切分 SearchEpisode；
- 解析工具结果候选；
- 扫描后续事件并构造 UsageEvidence；
- 划分 gold/candidate 并去重；
- 为每个合格 episode 构造 prompt、调用一次 LLM并执行第一层门禁。

首版仍可保持在当前模块内实现，不为每个步骤过度拆分文件；只有当模块职责明显失控时再提取独立
helper。

### `scripts/mine_trace_queries.py`

- 保持当前手动填写常量的调试入口；
- 一个 case 接收零到多个 mining outcome；
- `DRY_RUN` 为每个合格 episode 输出独立 prompt row；
- prompt version 更新为 `trace-search-v2`；
- 统计 trace、episode、LLM 调用、有效 query 和跳过原因。

### `scripts/evaluation.py`

- 搜索逻辑保持不变；
- 继续用 `answer` 计算强标注指标；
- 将 precision 展示名调整为 `observed_precision`；
- 读取 `candidate_answers`，给结果添加 gold/candidate/unlabeled 分类；
- 首版不实现 candidate 的部分得分。

### Viewer

- query viewer 展示原始 trace、episode anchor、结果事件、usage evidence、query、gold 和 candidate；
- evaluation viewer 区分 gold hit、candidate hit 和 unlabeled hit；
- viewer 只增强可读性，不在前端重新推导答案。

## 15. 错误处理与统计

稳定跳过原因包括：

```text
no_search_result
ambiguous_tool_result
no_parseable_location
no_usage_evidence
duplicate_episode
generator_failed
invalid_model_json
direct_lookup_query
missing_semantic_constraint
query_leaks_result
```

脚本最终输出至少包含：

```json
{
  "processed_traces": 100,
  "search_episodes": 430,
  "eligible_episodes": 86,
  "written_queries": 73,
  "skipped": {}
}
```

首版复用控制台 JSON 汇总，不新增数据库或实验管理服务。LLM 调用失败只影响当前 episode；日志和
产物不得保存 API key。

## 16. 测试策略

1. adapter：只接收 resolved Java trace，并保留多工具调用信息；
2. episode：覆盖 tool-call ID、位置回退、多调用歧义和嵌套 shell 参数；
3. candidate parser：覆盖文件列表、grep 行、结构化 symbol、路径规范化、测试文件排除和歧义路径；
4. usage evidence：覆盖 opened/searched/edited/referenced、工具输出不计证据和最近 producer 归属；
5. function gold：只有明确函数证据才输出函数，否则退化为文件级；
6. prompt：包含 issue、前置推理、当前动作和精确关键词，不包含结果、后续事件、patch 或最终回答；
7. validator：允许“anchor + semantic constraint”，拒绝 shell、路径、纯引用/调用/实现查询；
8. mining：一个 trace 可输出多条 query，单 episode 失败不影响其他 episode；
9. serialization：gold 与 candidate 互斥，事件索引和 evidence 可回溯；
10. evaluator：计算 recall/observed precision，并正确标记三类 hit；
11. viewer：正确展示新 schema，并保持 HTML 转义；
12. 集成 fixture：从 resolved trace 产生至少一条可被 evaluator 读取的 JSONL。

提交实现前运行：

```text
ruff check .
ruff format --check .
pytest
```

## 17. 验收标准

- 同一 trace 可以产生零到多条、每条对应一个搜索 episode 的 query；
- LLM 不可见当前结果、后续使用证据、最终回答和 reference patch；
- query 允许精确 identifier，但必须包含可审计的 semantic constraint；
- `answer` 完全来自当前搜索候选与后续使用证据的交集；
- 未被后续使用的搜索结果保留为互斥的 `candidate_answers`；
- 没有 gold 的 episode 不调用 LLM；
- 测试文件不进入 gold/candidate；
- evaluator 明确使用 `observed_precision`，不把 candidate 宣称为负例；
- 不增加第二层 baseline 难度检查；
- evaluator 的 CodeSense 搜索流程本身不因 benchmark schema 改变；
- viewer 能从事件、evidence、query 到答案完整回溯一个 case；
- 旧 JSONL 不被静默覆盖；
- 仓库规定检查通过，或对已有且与本次无关的失败做明确区分。
