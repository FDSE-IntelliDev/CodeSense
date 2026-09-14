# Open-SWE-Traces 语义代码检索 Benchmark 设计

> 状态：设计已确认，待实现
>
> 日期：2026-09-14
>
> 范围：从 resolved 的 Open-SWE-Traces Java 轨迹生成“一条轨迹一条语义 query”，并以 reference patch 中修改的既有生产 Java 代码位置作为 ground truth

## 1. 背景与问题

当前 query mining 会把一条 trace 中的每个 grep、find、符号查询分别改写为自然语言 query。
这种数据可以还原 Agent 的搜索过程，但大量 query 本质上仍是直接查找：例如“查找引用
`PageRequest` 的 Java 文件”“定位 `isPoolLifo` 的调用”“找到 `LoadBalance` 的实现类”。普通
文本倒排索引或代码图上的引用、继承关系已经足以解决它们，无法有效验证 CodeSense 的项目
词表接地、语义扩展、向量过滤和组合算子。

新的 benchmark 把评估单元从“一个搜索动作”提升为“一个已解决的 issue 轨迹”。LLM 根据
完整 issue 描述以及筛选后的搜索相关 trace，抽象出一个需要理解行为、职责、影响或故障机制
才能回答的语义 query；程序再从隐藏的 reference patch 中确定性地附加正确答案。每条 trace
最终只产生一个 `query + answer`。

本设计认可的目标 query 示例是：

```text
Find the logic that can leave navigation state inconsistent when switching between
cursor-based and page-based access.
```

它描述了待定位代码的行为和失效模式，却没有给出文件、类、方法或可直接匹配的结构关系。

## 2. 目标与非目标

### 2.1 目标

- 只使用 `resolved == 1` 的 Open-SWE-Traces Java 轨迹；
- 一条有效 trace 只调用一次 LLM，并输出一条英语语义 query；
- LLM 输入包含完整原始 issue 描述和搜索相关 trace 局部窗口；
- query 描述行为、职责、状态变化、副作用、性能影响或失败机制；
- reference patch 不进入 prompt，只在程序侧构造 ground truth；
- 主 ground truth 是 patch 修改的既有生产 Java 文件及可确定的函数；
- query mining 产物可直接由搜索评测脚本和结果 viewer 消费；
- 完整记录原始 trace、选中的事件、prompt、模型和抽取原因，便于人工审计。

### 2.2 非目标

- 当前阶段不比较 grep、文本搜索或代码图谱等 baseline；
- 不把一条 trace 拆成多条独立 query；
- 不复现 Agent 的每一步搜索答案；
- 不让 LLM 生成或判断 ground truth；
- 不评估 reference patch 的具体修改内容或 Issue 修复质量；
- 不把 patch 新增文件、删除文件或测试文件纳入主 ground truth；
- 不做第二阶段 query 排序、筛选或人工精修；
- 不因为 query 中出现答案提示而清洗原始 issue。

## 3. 数据边界与可信来源

一个输入 case 使用四类信息，但用途严格分离：

| 信息 | 来源 | 是否进入 LLM prompt | 用途 |
|---|---|---:|---|
| 原始 issue | 第一条 `role=user` 的 issue description | 是 | 提供问题、需求和语义背景 |
| 搜索相关 trace | `assistant` / `tool` 事件局部窗口 | 是 | 提供 Agent 调查过的方向和项目上下文 |
| reference patch | `metadata.reference_patch.patch` | 否 | 确定性构造主 ground truth |
| 最终 assistant 回答 | trace 最后一条 assistant 事件 | 否，默认只归档 | 人工校验或补充说明，不参与主 gold |

原始 issue 保持完整，包括 `<issue_description>` 中可能出现的
`New interfaces introduced:`、类名、方法名和位置。这里有意不做“答案泄漏清洗”：benchmark
要测试的是从真实 issue 生成语义化搜索问题，而不是评估盲目猜测答案。语义约束由 prompt 和
输出校验保证。

reference patch 是隐藏答案源。它不能出现在 prompt、LLM 返回值修复提示或模型可见的错误
信息中，避免模型把 patch 路径和符号原样复制为 query。

## 4. 总体流程

```text
resolved Java trace
        |
        +--> 完整原始 issue --------------------+
        |                                      |
        +--> 搜索事件检测 --> 局部事件窗口 ------+--> LLM --> 一条语义 query
        |
        +--> reference patch --> 确定性 gold 提取 -------> answer
                                                        |
                           provenance + source trace ----+
                                                        v
                                              PreparedQuery JSONL
                                                        |
                                                        v
                                           CodeSense Top-20 评测
```

具体顺序如下：

1. adapter 丢弃非 Java 或 `resolved != 1` 的记录；
2. adapter 读取完整 issue、事件和 reference patch；
3. 程序先从 patch 提取主 ground truth；没有合法答案时直接跳过，不调用 LLM；
4. `query_mining` 识别搜索事件，并为每个命中保留前一条、当前和后一条事件；
5. 对事件去重并保持原 trace 顺序，只保留 `assistant` 和 `tool`；
6. 将完整 issue、筛选后的事件和 few-shot 约束组成 prompt；
7. LLM 一次返回一条 query 和简短的语义性说明；
8. 程序校验 query，不合格则把 trace 标记为 invalid；
9. 程序将 query 与隐藏 patch gold 合并成一条扁平记录。

## 5. 搜索相关 trace 的构造

搜索事件继续由确定性规则识别，包括显式搜索工具，以及嵌入 JSON、Python 字面量或引号中的
`rg`、`grep`、`find` 命令。`system` 和 `user` 事件不作为 trace context；用户输入已经通过
独立的 issue 字段提供。

对每个搜索事件位置 `i`，上下文保留：

```text
events[i - 1], events[i], events[i + 1]
```

合并多个窗口时按原始 event index 去重和排序。这样既保留搜索动作本身及紧邻的意图和工具
结果，又避免把完整长 trace 输入模型。prompt 同时列出确定性的 search-event indices，便于
模型理解哪些事件是搜索锚点，但不要求模型逐条输出结果。

如果 trace 中没有搜索事件，则当前 case 记为 invalid。最终 assistant 回答是否显式包含文件
和函数不再作为有效性的前置条件，因为主答案来自 reference patch。

## 6. 语义 query 生成契约

### 6.1 Prompt 输入

prompt 只包含：

1. prompt version；
2. 完整原始 issue；
3. 搜索事件索引；
4. 合并后的 assistant/tool 局部窗口；
5. 语义 query 定义、few-shot 示例和 JSON 输出格式。

不包含 reference patch、patch-derived 文件或函数、model patch，也不把最终 assistant 回答
单独突出为答案来源。

### 6.2 正向示例

prompt 使用以下 few-shot 教模型抽象问题行为，而不是复述搜索命令：

```text
Issue/search evidence:
Navigation can switch between cursor-based and page-based access, after which later
navigation may use inconsistent state.

Good semantic query:
Find the logic that can leave navigation state inconsistent when switching between
cursor-based and page-based access.

Why it is good:
The query describes a state transition and failure mode. It does not reveal a file,
class, method, shell command, or exact symbol relation.
```

再提供一个与 CodeSense 接地能力直接相关的例子：

```text
Good semantic query:
Find functions whose behavior can affect disk performance.

Why it is good:
Relevant code may use terms such as I/O, buffering, flushing, persistence,
synchronization, or storage without containing the words "disk performance".
```

### 6.3 反向示例

以下 query 属于可由直接词法或图关系完成的查找，prompt 明确标记为 bad：

```text
Find Java files that reference PageRequest.
List Java files containing getDoubleEvaluation.
Find implementations of LoadBalance.
Locate calls to isPoolLifo.
Search for RetryUtils.java.
```

不能简单禁止单词 `find`，因为目标 query 本身可以使用 `Find the logic ...`。应禁止的是“明确
符号或路径 + 直接结构关系”的组合，例如 files containing、references、implementations of、
calls to、class named、method named。

### 6.4 输出格式

LLM 只返回一个 JSON 对象：

```json
{
  "status": "valid",
  "query": "Find the logic that can leave navigation state inconsistent when switching between cursor-based and page-based access.",
  "reason": "It asks for a behavioral failure mechanism without exposing code identifiers."
}
```

无法形成语义 query 时返回：

```json
{"status": "invalid trace"}
```

LLM 不返回文件、函数、逐搜索动作答案或 final answer。`reason` 只用于审计，不参与检索和
评分。

## 7. Reference patch ground truth

### 7.1 文件提取

adapter 从 unified diff 的每个 `diff --git` 文件段读取 old/new path，并应用以下规则：

- 仅保留 `.java`；
- 仅保留 patch 前已经存在且 patch 后仍存在的文件；
- old path 或 new path 为 `/dev/null` 时视为新增或删除文件并排除；
- 排除测试、示例、生成目录和构建目录；
- 使用 repository-relative POSIX path；
- 同一文件只保留一次。

测试路径至少覆盖 `/test/`、`/tests/`、`src/test/` 以及测试命名规则；生产文件判断应与
CodeSense 的 Java 文件发现规则保持一致。新增生产文件和测试文件可保留在 provenance 中供
诊断，但不进入 `answer`。

### 7.2 函数提取

对每个合法生产 Java 文件，从该文件 diff 中确定性提取被修改函数：

1. 优先解析 hunk header 的 trailing context 中出现的方法或构造函数声明；
2. 同时扫描 hunk 的上下文行、删除行和新增行中的 Java 方法/构造函数声明；
3. 只保存规范化后的函数名，不保存参数列表、返回类型或类名前缀；
4. 去重并保持首次出现顺序；
5. 类声明、字段初始化、import、注释和无法确认的控制结构不当作函数。

patch 文本有时无法可靠给出所属函数，例如 hunk header 只有类名，或者改动发生在字段和静态
初始化块中。这种情况下仍保留文件级 gold，并令 `functions` 为空；不得猜测函数名，也不得
借助 LLM 补全答案。

首版使用轻量的 deterministic patch parser，不要求为了函数抽取 checkout 和完整解析仓库。
后续若需要提高函数 gold 覆盖率，可以在项目已 checkout 后增加 Java parser 映射，但不能
改变同一版本 benchmark 的 gold 语义。

### 7.3 Case 有效性

下列任一条件成立时，case 不进入 query 生成：

- 不是 Java；
- `resolved != 1`；
- reference patch 缺失或不可解析；
- patch 中没有被修改的既有生产 Java 文件；
- trace 中没有可识别的搜索事件。

函数列表为空不会使 case 失效，因为文件 Recall/Precision 仍可评估。函数指标只对存在函数
gold 的 case 计算。

## 8. 输出数据模型

新的 query mining 产物采用一条 trace 一条扁平记录，不再嵌套多个 `searches`：

```json
{
  "query_id": "trajectory-id",
  "repo": "jakartaee/data",
  "instance_id": "instance-id",
  "trajectory_id": "trajectory-id",
  "issue_statement": "full original issue text",
  "query": "Find the logic that can leave navigation state inconsistent when switching between cursor-based and page-based access.",
  "answer": [
    {
      "file": "api/src/main/java/jakarta/data/page/PageRequest.java",
      "functions": ["ofPage", "afterCursor", "beforeCursor"]
    }
  ],
  "source_event_indices": [7, 8, 9],
  "strategy": "semantic-generated",
  "source_events": [],
  "provenance": {
    "prompt_version": "semantic-query-v1",
    "prompt": "...",
    "model": "...",
    "query_reason": "...",
    "search_event_indices": [8]
  }
}
```

字段约束：

- `query_id` 继续使用稳定的 trajectory identity；
- `query` 是唯一会传给 `Project.search()` 的文本；
- `answer` 是 patch-derived ground truth，至少包含一个文件；
- `source_event_indices` 是真正放进 prompt 的去重事件索引；
- `source_events` 保存原始 trace，供静态页面展示和复核；
- `provenance` 保存模型生成过程，不混入评分字段。

旧的 `SearchQuery[]`、每个搜索动作的 `answers` 和 `final_answer` 不再属于新 schema。旧 JSONL
不原地覆盖，新产物使用独立文件名，例如 `codesense-semantic-query.jsonl`。

## 9. 程序侧 query 校验

解析 LLM JSON 后执行轻量、可解释的校验：

- `status == "valid"`；
- `query` 和 `reason` 都是非空字符串；
- query 是单条英语自然语言文本，不含代码块、换行 shell 脚本或绝对路径；
- 不以 `rg`、`grep`、`find` 等命令开头；
- 不出现明显的 `.java` 文件路径；
- 不包含从隐藏 gold 得到的精确文件 stem 或函数名；
- 不符合直接查找模式，如 files containing、references、implementations of、calls to、
  class named、method named；
- query 必须包含行为或问题导向的表达；该项主要由 prompt 和 `reason` 审计，不引入复杂的
  通用语义分类器。

校验失败时记录明确原因并跳过该 case。当前阶段不二次调用 LLM 修复，也不添加 baseline
查询兜底，保证每个有效样本只发生一次生成调用。

## 10. 模块改造范围

### `evaluation/trace_adapters/open_swe_traces.py`

- 保持 `resolved == 1` 过滤；
- 读取完整 issue；
- 读取并解析 `metadata.reference_patch.patch`；
- 输出 production/ignored patch locations 或等价的适配器字段。

### `evaluation/models.py`

- 将 `PreparedQuery` 改为一条顶层 `query` 和 `answer`；
- 增加 `issue_statement`、`source_event_indices`；
- 移除新流程不再使用的嵌套 `searches` 和 LLM `final_answer`。

### `evaluation/query_mining.py`

- 保留 search-event 检测和局部窗口构造；
- `build_prompt()` 加入完整 issue 和 few-shot；
- 一条 trace 只调用一次 generator；
- 解析单 query JSON，执行语义性校验；
- 将 query 与 adapter 提供的 patch gold 合并。

### `scripts/mine_trace_queries.py`

- 保持当前硬编码调试参数形式；
- prompt version 更新为 `semantic-query-v1`；
- 输出独立的 semantic query JSONL；
- 逐 case 记录 invalid 原因和汇总数量。

### `scripts/evaluation.py`

- 每条记录只读取一次 `query` 和一组 `answer`；
- 每个 route 对该 query 运行一次 Top-20 搜索；
- 保持现有文件/函数 Precision、Recall、耗时和 route 记录方式；
- 有函数 gold 才计算函数级指标。

### Viewer

- `evaluation/query_viewer.py` 展示完整 issue、原始 trace、被选中的事件、唯一 query 和 patch
  answer；
- `evaluation/live_results.py` 从顶层 query/answer 渲染一张 case 卡片，不再遍历 nested
  searches；
- viewer 只展示现有数据，不重新推导答案或指标。

## 11. 错误处理与可观测性

每个跳过的 case 使用稳定 reason，例如：

```text
not_java
not_resolved
missing_reference_patch
invalid_reference_patch
no_production_java_gold
no_search_events
generator_failed
invalid_model_json
non_semantic_query
query_leaks_gold_identifier
```

汇总至少记录读取、adapter 通过、gold 有效、调用 LLM、生成有效和各类跳过数量。生成成功记录
保存 prompt、模型、prompt version、query reason、search hints 和实际 source event indices，
从而可以回溯“模型为什么得到这条 query”。日志和产物不得保存 API key。

## 12. 测试策略

实现阶段按以下层次补充测试：

1. adapter：只接收 strict `resolved == 1`，保留完整 issue，并正确分类修改、增加、删除、测试
   和生产 Java 文件；
2. patch parser：从 hunk header 和声明行提取函数，无法确认时返回空函数列表；
3. context：每个 search hint 包含前一条、当前和后一条事件，多窗口去重排序，排除 system/user；
4. prompt：包含 issue、正反 few-shot 和筛选后的 trace，不包含 reference patch；
5. parser：接受单 query JSON，拒绝旧的 nested searches、无效 JSON 和 invalid trace；
6. validator：接受已确认的 navigation-state 示例，拒绝 shell/path、直接符号查找和 gold 标识符
   泄漏；
7. mining：一个 case 只调用 generator 一次并只输出一个 `PreparedQuery`；
8. evaluation：顶层 query/answer 运行一次搜索并保持文件/函数指标语义；
9. static/live viewer：正确展示新 schema，HTML 内容保持转义；
10. 集成 fixture：从一条 resolved trace 生成一条 semantic query JSONL，并能被评测脚本读取。

提交前运行仓库要求的：

```text
ruff check .
ruff format --check .
pytest
```

## 13. 验收标准

首版实现完成时应满足：

- 同一 trace 不再产生多个 query；
- LLM prompt 可见完整 issue 和搜索相关局部 trace，但不可见 reference patch；
- 生成结果采用 `query + answer` 扁平 schema；
- `answer` 仅来自 reference patch 中修改的既有生产 Java 文件；
- 已确认的 navigation-state query 通过校验；
- “references PageRequest”“implementations of LoadBalance”等直接查找 query 被拒绝；
- evaluator 和两个 viewer 均能读取新 schema；
- 旧 query JSONL 不被静默覆盖；
- 单元测试及仓库规定检查通过，或对已有且与本次无关的失败做明确区分。

