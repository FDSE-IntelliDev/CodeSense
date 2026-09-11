# CodeSense Changelog

## 2026-09-11 — 结构化查询理解与通用结果目标

- planned 的首次 LLM 调用改为 Pydantic 定义的 strict JSON Schema，稳定返回语义 units、
  relations、targets 与 criterion；term 区分 literal、synonym、derived，并保留权重与理由。
- prompt 只携带按 document frequency 排序、过滤低频尾部的有界代表词表；模型允许提出
  词表外 canonical term，再由同一个查询级 `TermResolver` 为统计校验、估算和执行接地。
- 硬结果目标支持 file、type、class、function、field 等多 kind 有序并集；非文件目标直接
  过滤元素，包含 file 时才通过 `in_file` 合并所属文件。
- relation 可用 `$result` 明确绑定返回端点，例如“返回引用 PageRequest 的文件”先反向投影
  真实 `references` 边，再执行 file target；无对应边是有效空结果而不是猜测或编译失败。

## 2026-09-09 — 文件结果目标与项目内引用图

- 源文件成为真实的 `Element(kind="file")` 图节点，声明通过 `in_file` 物理归属边投影到
  文件；`target=("file",)` 作为跨 route 的硬输出契约，普通声明查询保持原结果类型。
- Java 单次扫描提取项目内 `references` / `imports`，并按最小可索引引用方建立带位置、
  置信度和来源的边；新增保留 Evidence 的一跳 `project()` 算子。
- codegen、planned 与 lexical 路由接入文件目标契约，新增 PageRequest 真实 Java 项目的
  图直查、references 脚本和 imports 脚本端到端验收。
- 直接路径/glob postings 与任意文件内容正则仍不在本次支持范围内。

## 2026-08-06 — finetune 轻量化 Demo

- 新增 `lightweight`、`full_force`、`warn_full` 三种 finetune profile；默认使用
  100～300MB 紧凑基础包和 2GB 内存预算。
- 项目语料改为扫描时写入临时 JSONL，训练紧凑 Word2Vec，并把模型保存到
  `.codesense/embedding/project.model`。
- expansion 同时比较基础空间与项目适配空间，同一映射保留较高分；搜索阶段仍只加载
  索引和 `expansion.json`。
- `full_force` 在 spawn 子进程提取向量；`warn_full` 需要显式设置
  `allow_unsafe_full`，避免意外占用 15～25GB 内存。

## 2026-07-30 — 仓库结构按 DEV-COOKBOOK 整理

只搬位置、改名、补配套设施，**没有改动任何函数内部逻辑**。

### 代码收进 `codesense/` 包

原来 12 个目录 + 5 个 `.py` 全平铺在仓库根目录，现在收进一个与项目同名的包。
路径映射（本文件下方的历史条目仍沿用旧路径，未回改）：

| 旧 | 新 |
|---|---|
| `definition.py` | `codesense/config.py` |
| `main.py` | `codesense/__main__.py` |
| `code_parser.py` / `ngram_split.py` / `invert_index.py` | `codesense/indexing/` |
| `init/` | `codesense/indexing/codegraph/` |
| `DSL/` | `codesense/dsl/` |
| `codeQL/` | `codesense/codeql/` |
| `executor/` | `codesense/executors/` |
| `query_processing/` | `codesense/query/` |
| `parsers/` `search/` `filters/` `embedding/` `expansion/` `tokenizer/` `utils/` | `codesense/<同名>/` |
| `query_processing/run_*.py`、`search/run_regex_search.py` | `scripts/` |

共重写 153 处 import，全部 178 个包内导入目标已静态校验可解析。

### 配置与密钥

- 新增 `configs/default.yaml`；`codesense/config.py` 提供 `frozen` 的 `Config`
  与 `load_config()`，YAML 字段拼错/缺失/多写立刻报错。
- **移除了 `definition.py` 里明文写死的 dashscope API key**，改为从环境变量
  `CODESENSE_API_KEY` 读取，新增 `.env.example`。
  ⚠️ 该 key 仍在 git 历史里（commit `652a37f`），**必须去控制台吊销重发**。
- 旧的模块级常量（`PROJECT_OUTPUT_DIR` 等 40 处调用点）通过 PEP 562 的模块级
  `__getattr__` 保留为惰性属性，属过渡措施，见 ARCHITECTURE.md 的待办。

### 顺手修掉的既有问题

- `main.py:173` 把 `parse_args` 的参数写死成固定列表，命令行传什么都没用；
  `code_parser.py:174` 同样。两处都改成正常读 argv。
- `expansion/detect_abbr.py` 顶部 `from codesense.config import ABBR_RESULT_DIR`
  引用了一个**从来不存在**的常量，该模块此前根本 import 不进来；
  现已作为正式配置项 `output.abbr_result_dir` 补上。
- `expansion/detect_abbr.py` 和 `init/code_db.py` 用的是 Python 2 风格的隐式
  相对导入，收进包后必然失效，已改为绝对导入。
- 清掉 7 个文件里的 `/Users/huangzhuochen/...`、`/home/fdse/hzc/...`
  等机器专属绝对路径，以及 `edge_builder.py` 里一处写死路径的调试分支。
- `detect_abbr.py` 模块顶层的 `print(cpu_count())` 已移除。

### 版本库瘦身

`git rm --cached` 了 `output/`（30M 产物）和 `slides/`（34M 答辩 PPT 与素材，
由原 `projects/`、`*.pptx`、`中期答辩ppt*_files/` 归拢而来）。
文件都还在磁盘上，旧提交里也仍在（本次未重写历史）。
跟踪体积 65M → 2.1M。

### 新增配套设施

- `pyproject.toml`：依赖按真实 import 反查重列（原 `requirements.txt` 里的
  `javalang` / `scipy` / `flask` / `thinc` 全仓库无人 import，已移除），
  含 ruff 与 pytest 配置；`requirements.txt` 删除。
- `tests/`：70 个单元测试（config / SemQL 字段抽取 / 计划模型 / 产物读写 /
  代码元素类型），另有 3 个标 `slow` 的离线索引集成测试和一个
  `tests/fixtures/mini_project/` 夹具。
- `evaluation/`、`experiments/`、`data/`、`scripts/`：目录与 README 约定就位。
- 文档：新增 `ARCHITECTURE.md`、`CONTRIBUTING.md`、`docs/README.md`、
  `docs/decisions/0001-semcon-three-condition-split.md`；
  根目录 5 份 md 收进 `docs/`，生成的 html 收进 `docs/html/`；
  `changelog.md` → `CHANGELOG.md`。

### 已知遗留

- 在线 pipeline 目前只有 Intention Executor 是打开的，Surface 与 Relation
  两步在 `codesense/__main__.py` 里被注释掉——这是原有的调试状态，本次原样保留。
- ruff 对老模块挂了逐条列出的规则号豁免（约 1900 条积压），新代码受全套规则约束。

---

## 2026-07-09 — v0-current：三类 SemCon 串行执行流程

### 当前流程

当前 CodeSearch 的在线检索流程以 SemCon/SemQL 为中心，将输入 query 拆解为三类条件：

- `surfaceCon`：关键词、符号名、代码片段、同义词、ngram 等表层匹配条件。
- `relationCon`：类型、文件、容器、caller/callee、调用图角色等结构关系条件。
- `intentionCon`：功能语义、业务意图、非功能属性等需要深层语义判断的条件。

现有执行链路大致为：

```text
Natural Language Query
  -> SemCon Extraction
  -> SemQL Composition
  -> Surface Executor
  -> Relation Executor
  -> Intention Executor
  -> Final Results
```

### 当前执行语义

1. `Surface Executor` 先使用 surface 条件做候选召回。
   - 主要依赖倒排索引、ngram、缩写/子词匹配、符号名匹配等低成本检索方式。
   - include surface 条件产生初始候选集。
   - exclude surface 条件从初始候选集中移除不需要的元素。

2. `Relation Executor` 在 surface 候选集上继续过滤。
   - relation 条件不会独立生成完整候选空间，而是基于 surface 结果做结构约束过滤。
   - include relation 条件通常与当前候选集做交集。
   - exclude relation 条件从当前候选集中删除命中的元素。

3. `Intention Executor` 放在最后执行。
   - intention 条件代价最高，可能调用 LLM 或语义判别模型。
   - 将它放在最后可以让输入 candidate set 尽量小，从而控制 LLM 调用成本和端到端延迟。

### 当前流程的合理性

当前设计对 `intentionCon` 是合理的：先用低成本的 surface/relation 条件缩小候选集，再让 LLM 或语义模型处理少量候选，可以显著降低成本。

当前流程也便于实现和解释：

- Stage 1 做高召回。
- Stage 2 做结构过滤。
- Stage 3 做高成本语义判别。

### 当前流程的问题

当前 `surfaceCon -> relationCon` 的串行分离过于刚性。它默认一个代码元素必须先被 surface 直接召回，后续 relation 只能在这个候选集合内部过滤。

这会遗漏一种常见的代码搜索场景：query 中的多个关键词概念可能分布在同一段局部调用链或代码子图中，而不是集中出现在同一个代码元素上。

例如 query 表达的是“优化磁盘 I/O 性能”，可以得到三个概念组：

```text
K1 = {disk, swap}
K2 = {I/O, read, write}
K3 = {performance, buffer, async, sync}
```

组内关键词是 OR 关系：

```text
R1 = search(disk OR swap)
R2 = search(I/O OR read OR write)
R3 = search(performance OR buffer OR async OR sync)
```

但组间不应该只是普通交集：

```text
R = R1 intersect R2 intersect R3
```

因为某个代码元素 A 可能只直接命中 `K1`，但它的 caller/callee 或若干跳邻居 B 直接命中 `K2`。如果 A 和 B 位于同一条调用链或局部代码关系图中，并且距离足够近，那么 A 也应该被视为与 `K2` 有关。

普通交集会丢掉这类候选，导致端到端召回不足。

## 2026-07-09 — vNext proposal：图感知 AND(n) 检索逻辑

### 修改动机

新版本需要引入一种专门面向代码搜索的“与逻辑”，暂定为：

```text
AND(n)
```

其中 `n` 表示代码关系图中的最大 hop count。

`AND(n)` 的目标是让多个关键词概念组在局部代码图中共同满足，而不是强制同一个代码元素直接命中所有概念组。

### AND(n) 的语义

对于候选代码元素 A 和关键词组 Ki：

- 如果 A 自身直接命中 Ki，则认为 A 满足 Ki。
- 如果 A 自身没有直接命中 Ki，但在 hop count <= n 的代码关系邻域内，可以找到某个代码元素 B 直接命中 Ki，则也认为 A 满足 Ki。
- 当 A 对所有关键词组都满足时，A 被保留。

当 `n = 0` 时：

```text
AND(0) == 普通集合交集
```

因此 `AND(n)` 是普通 AND 的图感知扩展。

### 形式化描述

给定关键词组：

```text
K1, K2, ..., Km
```

每个关键词组先执行 OR 搜索：

```text
Ri = search(terms in Ki)
```

基础版本可以使用统一 hop count 对每个结果集做图邻域扩展：

```text
Expand(Ri, n) = { x | x in Ri or distance_graph(x, y) <= n for some y in Ri }
```

最终候选集为：

```text
R = Expand(R1, n) intersect Expand(R2, n) intersect ... intersect Expand(Rm, n)
```

但更细粒度的版本应该支持 group pair 级别的 hop count。原因是不同关键词组之间的语义耦合强度不同：

- `disk` 与 `I/O` 在“优化磁盘 I/O 性能”这个 query 中是强相关关系，应使用更小 hop count，避免引入过远的弱相关代码。
- `disk` 与 `performance` 的关系相对更间接，可以允许稍大的 hop count，让 buffer、async、sync 等性能实现细节通过局部调用链被召回。
- `I/O` 与 `performance` 也可能是中等或强相关关系，具体取决于 query 上下文。

因此最终执行时，判断候选 A 是否满足关键词组 Ki，不再只看一个全局 `n`，而是根据 A 已经覆盖的其他 group Kj，使用 pairwise hop count：

```text
hop(Ki, Kj) = pairwise_hop_count(Ki, Kj) or default_hop_count
```

候选 A 可以通过与某个命中 Ki 的元素 B 相连来补齐 Ki，但这个连接距离必须满足对应 group pair 的 hop 限制。

### 示例

query：

```text
Optimize disk I/O performance
```

关键词组：

```text
K1 = {disk, swap}
K2 = {I/O, read, write}
K3 = {performance, buffer, async, sync}
```

假设：

- A 直接命中 `disk`，因此 A 属于 `R1`。
- B 直接命中 `read/write`，因此 B 属于 `R2`。
- A 和 B 在调用图上距离为 2。
- `hop(K1, K2) = 2`。
- `hop(K1, K3) = 4`。
- `hop(K2, K3) = 3`。

则：

- A 虽然不直接属于 `R2`，但 A 在 `hop(K1, K2)` 允许的 2 跳内能到达 B，因此 A 可以通过 B 补齐 `K2`。
- B 虽然不直接属于 `R1`，但 B 在 `hop(K1, K2)` 允许的 2 跳内能到达 A，因此 B 可以通过 A 补齐 `K1`。
- 如果 A 或 B 还能通过自身或邻居满足 `K3`，则它们可以进入最终候选集。

### 建议的新流程

新版本不再简单地让 `Surface Executor` 和 `Relation Executor` 完全串行分离，而是在 Intention 之前新增一个图感知候选合并阶段：

```text
Natural Language Query
  -> SemCon Extraction
  -> SemQL Composition
  -> Keyword Group Expansion
  -> Graph-Aware AND(n) Candidate Merge
  -> Explicit Relation Filter
  -> Intention Executor
  -> Final Results
```

其中：

- `Keyword Group Expansion` 负责把 query 中的核心概念拆成关键词组，并为每个概念生成上下文相关扩展词。
- `Graph-Aware AND(n) Candidate Merge` 负责执行组内 OR、组间 AND(n)。
- `Explicit Relation Filter` 继续处理明确的结构约束，例如指定 caller/callee、entry point、leaf、file/container/type 等。
- `Intention Executor` 仍然保留在最后，控制 LLM 成本。

### 需要修改的数据结构

现有 surface condition 是扁平关键词结构。新版本建议增加 `keyword_groups` 和 `group_logic`。其中 `property` 应该支持 group 级配置：顶层 `property` 只作为兼容旧 schema 的默认值，每个 `keyword_group` 可以单独声明 `include` 或 `exclude`。这样才能表达 `k1 AND_HOP k2 AND NOT_HOP k3` 这类查询。

`group_logic` 不应只支持一个全局 `hop_count`，还应拆成多个逻辑算子。正向 include groups 使用 `and_hop`，排除 groups 使用独立的 `not_hop`。`and_hop` 内只声明 include group 之间的 pairwise hop count，不应混入 exclude group。

```json
{
  "type": "surface",
  "property": "include",
  "keyword_groups": [
    {
      "group_id": "k1",
      "property": "include",
      "concept": "disk",
      "terms": ["disk", "swap"]
    },
    {
      "group_id": "k2",
      "property": "include",
      "concept": "I/O",
      "terms": ["I/O", "read", "write"]
    },
    {
      "group_id": "k3",
      "property": "exclude",
      "concept": "performance",
      "terms": ["performance", "buffer", "async", "sync"]
    }
  ],
  "group_logic": [
    {
      "op": "and_hop",
      "groups": ["k1", "k2"],
      "default_hop_count": 3,
      "graph_scope": ["call"],
      "pairwise_hop_counts": [
        {
          "groups": ["k1", "k2"],
          "hop_count": 2,
          "reason": "disk and I/O are strongly coupled in this query"
        }
      ]
    },
    {
      "op": "not_hop",
      "groups": ["k3"],
      "default_hop_count": 1,
      "graph_scope": ["call"],
      "reason": "exclude performance tuning symbols and their close call-chain neighbors"
    }
  ]
}
```

执行时规则：

- group 自身存在 `property` 时，以 group 级 `property` 为准。
- group 未声明 `property` 时，继承 surface condition 顶层 `property`。
- `and_hop` 只引用 include groups，执行组内 OR、组间图感知 AND。
- `not_hop` 只引用 exclude groups，从正向候选集中删除自身或 hop 邻域内命中 exclude group 的候选。
- `and_hop.pairwise_hop_counts` 只描述 `and_hop.groups` 内部的 group pair。
- 如果 include group pair 声明了 `pairwise_hop_counts`，使用该 pair 的 `hop_count`。
- 如果 include group pair 没有声明 pair 级规则，使用该 `and_hop` 算子的 `default_hop_count`。
- 如果 `and_hop.default_hop_count = 0` 且没有 pair 级覆盖，则该 `and_hop` 退化为普通 AND。
- pair 是无向的，`["k1", "k2"]` 与 `["k2", "k1"]` 表达同一个约束。

### include / exclude 解析语义

对于自然语言查询：

```text
Find disk I/O related code but not performance tuning code
```

可以解析为：

```text
include: K1 = {disk, swap}
include: K2 = {I/O, read, write}
exclude: K3 = {performance, buffer, async, sync}
```

逻辑表达为：

```text
AND_HOP(K1, K2) AND NOT_HOP(K3)
```

执行步骤：

1. 对每个 group 内部执行 OR 搜索。
2. 对 include groups 执行 pairwise `AND_HOP` 合并，得到正向候选集。
3. 对 exclude groups 执行 `NOT_HOP` 删除：
   - 如果候选自身命中 exclude group，删除。
   - 如果候选在 `exclude_hop_count` 或默认 hop 范围内连接到 exclude group 的命中元素，删除。
4. 将剩余结果交给显式 relation filter 和 intention executor。

为了避免 exclude 过度删除，建议使用独立的 `not_hop` 算子配置排除范围：

```json
[
  {
    "op": "and_hop",
    "groups": ["k1", "k2"],
    "default_hop_count": 3,
    "graph_scope": ["call"]
  },
  {
    "op": "not_hop",
    "groups": ["k3"],
    "default_hop_count": 1,
    "graph_scope": ["call"]
  }
]
```

默认建议：

- include 的 hop count 可以稍大，用于提高召回。
- exclude 的 hop count 应更保守，避免因为远距离弱相关节点误删正确结果。
- 当 query 明确表达强排除关系，例如“not logout”或“exclude test code”，可以将对应 exclude group 的 hop count 设置为 `0` 或 `1`。

### 需要新增或调整的模块

1. 新增 `GraphAwareSearchExecutor` 或 `AndHopExecutor`。
   - 输入 SemQL 中的 `keyword_groups`。
   - 对每个 group 独立执行 surface 搜索。
   - 调用代码关系图做 `Expand(Ri, n)`。
   - 对扩展后的 group 结果做交集。
   - 输出最终候选集和覆盖证据。

2. 扩展 `RelationGraphStore`。
   - 新增通用邻域查询接口。
   - 支持从一组 symbol_id 出发，在调用图中查找 `n` 跳内邻居。
   - `graph_scope` 只支持 `call` 和 `import`；同文件关系在 import/file scope 下按 `hop_count = 0` 处理。

3. 调整 SemQL 生成逻辑。
   - LLM 抽取 SemCon 时需要区分“概念组”和“组内扩展词”。
   - SemQL Composer 需要保留 `keyword_groups`，不能把所有词完全拍平成一个关键词列表。

4. 保留现有 Relation Executor。
   - 新的 AND(n) 不是替代所有 relation 条件。
   - 它主要解决 surface 概念之间的图邻域满足问题。
   - 明确的结构过滤仍然由 Relation Executor 处理。

### 结果证据与打分

新版本需要记录候选为什么被保留：

```json
{
  "symbol_id": "123",
  "coverage": {
    "k1": {
      "match_type": "direct",
      "matched_term": "disk",
      "distance": 0
    },
    "k2": {
      "match_type": "graph_neighbor",
      "matched_term": "read",
      "neighbor_symbol_id": "456",
      "distance": 2
    }
  }
}
```

打分应区分直接命中和图传播命中：

```text
group_score = direct_score                         if direct match
group_score = direct_score_of_neighbor * decay^d   if graph-neighbor match
```

其中：

- `d` 是图距离。
- `decay` 是距离衰减系数，例如 `0.7`。
- 直接命中应优先于远距离传播命中。

最终排序可以先采用保守公式：

```text
final_score =
  average(group_score)
  + direct_match_bonus
  + coverage_bonus
  + relation_proximity_bonus
```

### 实现优先级

1. 实现 `AND(0)`，确保它与普通交集行为一致。
2. 实现基于调用图的 `AND(n)`，先支持 `graph_scope = ["call"]`。
3. 增加 coverage evidence，记录每个候选满足各关键词组的方式。
4. 增加距离衰减打分。
5. 补充 `graph_scope = ["import"]`，并将同文件关系作为 `hop_count = 0` 的特殊情况处理。
6. 在端到端评测中比较普通 AND 与 `AND(n)` 对 Recall@K、MRR、nDCG@K、Bundle Hit Rate 的影响。

### 预期收益

该修改可以提升复杂 query 的召回质量，尤其是以下场景：

- 多个关键词概念分布在局部调用链中。
- query 描述的是一段协作逻辑，而不是单个函数名。
- 目标代码元素自身命名不完整，但其 caller/callee 暴露了关键语义。
- Agent 需要的是相关代码上下文，而不是孤立的单点代码块。

从论文角度看，`AND(n)` 可以作为 SemQL 2.0 的图感知布尔算子，说明本项目不是简单叠加 lexical search、dense search 和 relation filter，而是在代码图上重新定义了适合 Agent-oriented Code Search 的组合检索语义。

## 2026-07-14 — Planner 模块解耦 SemCon 解析与查询执行

### 修改动机

原有 `SemQLComposer` 直接把三类 SemCon 合并成一个全局 `semQL.json`。这种方式在流程较小时比较直接，但随着 surface、relation、intention 的 schema 和执行语义分别演进，会出现以下问题：

- 全局 Composer 需要理解三类条件的字段、默认值和执行细节，职责持续膨胀。
- Executor 仍需从全局嵌套结构中寻找自己关心的字段，规划逻辑与执行逻辑混在一起。
- 任意一种 SemCon schema 发生变化，都可能影响 Composer、公共解析工具和其他 Executor。
- 合并后的 SemQL 难以单独阅读、测试和复用，也不便于判断某个领域计划是否正确。

因此将 `semQL_composer` 改造成查询规划入口，并进一步拆分为三个领域 Planner。Planner 位于 SemCon 抽取和 Executor 之间，只负责把原始条件规范化为简洁、稳定、可执行的领域计划，不负责访问索引、代码图或调用模型执行检索。

### 目标结构

```text
Natural Language Query
  -> SemCon Extraction
  -> QueryPlanner
       -> SurfacePlanner   -> surface_semql.json
       -> RelationPlanner  -> relation_semql.json
       -> IntentionPlanner -> intention_semql.json
  -> 对应领域 Executor
  -> Final Results
```

`QueryPlanner` 只负责编排三个 Planner，并输出 `query_plan.json` 清单。每个 Planner 只读取对应类型的 SemCon：

- `SurfacePlanner`：解析关键词、同义词、include/exclude、匹配类型和代码元素类型，生成词法召回与集合合并计划。
- `RelationPlanner`：解析 caller、callee、role、file/container、CodeQL 和其他结构约束，生成关系过滤计划。
- `IntentionPlanner`：解析功能需求和查询语义，只根据原始 query 与 intention 条件构造 `semantic_text`、terms 和 include/exclude 需求，生成高成本语义判断计划。

三类领域计划之间不复制对方的条件。这样可以保持每个 SemQL 文件简洁易读，并让字段解析器与默认值收敛在所属 Planner 中。

### 设计边界

Planner 与 Executor 的职责边界如下：

- Planner 做 schema 解析、字段规范化、默认值补齐和逻辑计划生成。
- Executor 消费已经规范化的领域计划，负责索引查询、集合运算、代码图遍历、向量过滤和 LLM 判断。
- Planner 不读取 `symbols_index.json`、`codegraph.sqlite` 或 embedding 模型，也不直接返回代码候选。
- Executor 不再长期承担 SemCon schema 兼容与字段猜测逻辑。

这一边界的目的不是增加一层简单转发，而是建立稳定的执行契约：上游 SemCon schema 变化主要由对应 Planner 吸收，下游 Executor 只围绕明确的计划模型实现执行语义。

### 当前落地状态

当前已经新增以下结构：

```text
query_processing/
  plan_models.py
  planners/
    base.py
    query_planner.py
    surface_planner.py
    relation_planner.py
    intention_planner.py
```

主流程目前采用迁移期双写策略：

- 新链路生成 `query_plan.json`、`surface_semql.json`、`relation_semql.json` 和 `intention_semql.json`。
- 旧链路继续生成 `semQL.json`，现有 Surface、Relation、Intention Executor 暂时仍消费这个兼容文件。
- `semQL_composer.py` 保留为兼容入口，内部委托给新的 Planner，避免一次性修改所有调用方。

因此当前状态是“Planner 已落地、Executor 输入迁移尚未完成”，不是已经完全移除旧的统一 SemQL。

### Surface schema 接入状态

Planner 改造最初先覆盖旧的扁平 SurfaceCon；在实际 SemCon 抽取已经切换到新版 schema 后，`SurfacePlanner` 也已接入 `keyword_groups` 与 `group_logic`：

- 从每个 keyword group 分别读取 `keywords` 和 `synonyms`，组内构造 OR term expression。
- 保留 group 级 `group_id`、`property` 和 `reason`，不把概念组语义只拍平成一个词表。
- 规范化 `graph_scope` 和 `pairwise_hop_counts`，并过滤无效 group 引用与负 hop count。
- 旧的顶层 `keywords/synonyms/property` 输入会转换成一个 legacy keyword group，不再与新版 group 逻辑共用 condition 级 property 推断。

这次接入只负责生成 group-aware surface plan。Graph-aware AND(n)、pairwise hop 的实际图遍历、`NOT_HOP` 和 coverage evidence 仍属于 Surface–Relation 协作执行区，而不是 Planner 本身。新版 surface 字段的变化也没有要求 `RelationPlanner`、`IntentionPlanner` 理解这些字段，符合领域解耦目标。

### 后续迁移顺序

1. 让 Surface Executor 直接消费 `surface_semql.json`，移除其中对全局 `semQL.json` 的字段解析。
2. 让 Relation Executor 直接消费 `relation_semql.json`，保持 caller/callee/role 等结构语义不变。
3. 让 Intention Executor 直接消费 `intention_semql.json`，统一 Cluster、Term Embedding 与 LLM Judge 的查询输入。
4. 在 Executor 迁移稳定后移除双写逻辑和旧 `semQL.json` 兼容层。
5. 让 Surface Executor 使用 `keyword_groups` 与 `group_logic` 执行 Graph-aware AND(n)，并补充 coverage evidence。

### 预期收益

- SemQL 从一个全局混合结构变为三个领域执行契约，更短、更易读。
- schema 变化的影响范围被限制在对应 Planner 和 plan model 内。
- Executor 专注检索和过滤，不再承担输入协议解析。
- 每个 Planner 可以独立进行单元测试，错误更容易定位到抽取、规划或执行阶段。
- 为后续 surface 与 relation 的协作执行保留空间，同时不把三类条件重新耦合进一个庞大 Composer。

## 2026-07-15 — SurfacePlanner 分层布尔语义与跨 Condition 合并

### 修正原因

新版 SurfaceCon 的 `property` 位于 keyword group，而不是 condition。一个 condition 可以同时包含多个 include groups 和 exclude groups，因此不能再把整个 condition 推断成 include 或 exclude。旧实现中的 `_resolve_clause_property()` 会把混合 condition 错误地整体归入 include，造成 exclude group 被当作正向召回条件。

### 四层执行语义

Surface plan 改为显式表达四个层级：

1. **Term 层**：同一个 keyword group 内的 keywords 与 synonyms 执行 OR。
2. **Group 层**：同一个 condition 内的 include groups 执行 Graph-aware `AND(n)`；单 group 退化为 identity。
3. **Condition 层**：exclude groups 先通过 OR 构造负向集合，再从该 condition 的正向结果中 subtract。
4. **跨 Condition 层**：具有相同兼容键的 condition 结果做 intersection，不同兼容键的结果做 union。

兼容键定义为：

```text
non-code-element: match_kind
code-element:     (match_kind, normalized code_element_types)
```

因此两个 `code_element + function` condition 会做交集；`function` 与 `method` 会沿用现有类型规范化规则进入同一兼容组；`code_element + class`、`code_snippet` 等不同兼容组与前述结果做并集。

### 新的 Condition Plan

每个 surface condition 最多生成两个 clause：

```text
surface_i_include
  group 内 OR
  include groups 间 AND_HOP

surface_i_exclude
  group 内 OR
  exclude groups 间 OR，形成负向集合

condition_result
  include_result - exclude_result
```

例如：

```text
k1 include = login OR auth
k2 include = user OR account
k3 exclude = logout OR signout
```

生成语义：

```text
AND_HOP(k1, k2) - OR(k3)
```

即先让 login/auth 与 user/account 两个概念组在允许的代码图距离内共同满足，再移除 logout/signout 命中的结果。

### Hop 默认值

`group_logic` 新增 `default_hop_count`，供没有 pairwise override 的 include group pair 使用。Planner 继续支持已有 SemCon：字段缺失或非法时使用保守默认值 `0`，即退化为普通严格交集；有效的 `pairwise_hop_counts` 优先于默认值。

### 兼容与边界

- `_resolve_clause_property()` 已删除，property 始终从 group 读取。
- 旧扁平 SurfaceCon 转换为单个 legacy group，仍支持 condition 级 property。
- 只有 include group 可以进入 `group_logic`，exclude group 引用会在规划阶段过滤。
- 只有 exclude group 的 condition 会标记为 `exclude_only`，由顶层 condition expression 记录，供 Executor 在已有正向候选上应用。
- 本次完成的是 plan 语义和输入契约；真正的 AND(n) 图遍历与 coverage evidence 仍由后续 Surface Executor 实现。

## 2026-07-15 — Planner 搜索规划主链路

在线查询现在按 `Query → SemCon → QueryPlanner → Domain Planner → Logical Plan → Executor` 组织。`QueryPlanner` 将三类条件交给 `SurfacePlanner`、`RelationPlanner`、`IntentionPlanner`，分别生成独立且可测试的执行契约。

- Surface plan：group 内 terms 做 OR，include groups 做 AND(n)，exclude 做 subtract；兼容的 condition 取交集，不同 match/type 组取并集。
- Relation plan：规范化 caller、callee、role、file/container 等结构约束。
- Intention plan：统一聚类、项目 Term Embedding 与 LLM Judge 所需的语义输入。
- 当前仍双写旧 `semQL.json` 供 Executor 兼容；后续 Executor 将直接消费领域计划。
- 下一阶段在 Logical Plan 与 Executor 之间加入 cost-aware Optimizer，根据候选规模、索引成本和模型成本生成 Physical Plan：优先执行倒排索引、类型等低成本高选择性过滤，将图扩展、Embedding、聚类和 LLM Judge 等重步骤尽量后置。

实际 login/user 样例当前生成 `surface_0_include = AND_HOP(k1, k2)`、`exclude_clause = null`、`result_expression = identity(surface_0_include)`；文档中的 exclude subtract 与跨 condition 合并均明确作为扩展示例，不冒充该次实际输出。

## 2026-07-16 — Surface Executor 直接执行领域计划

Surface Executor 已改为直接消费 `surface_semql.json`，不再解析旧的统一 SemQL。执行器严格按 Term OR、Group AND(n)、Condition subtract、跨 Condition intersect/union 四层计划执行，并继续输出兼容的 symbol 列表。

- 现有 ngram、缩写扩展、项目 embedding 子词过滤继续由 `FullTermMatcher` 复用；类型过滤直接使用 Planner 规范化后的 `code_element_types`。
- `graph_scope=["call"]` 在 Surface 阶段按无向调用链距离解释：A 调用 B 与 B 调用 A 均视为一跳，caller/callee 方向留给 Relation Executor。
- 新增 `surface_group_search_results.json`，保存 term OR 与 condition 类型过滤后的逐 group 直接命中；该产物位于 clause 的 identity / OR / AND(n) 之前，便于对照分析各概念组的原始召回。
- 新增 `surface_evidence_hop_0.json`，按 condition/group 记录查询 `term`、实际 `matched_term`、direct/graph_neighbor、距离和邻居 symbol。
- `code_snippet`、`code_line` 与非空 `code_text` 暂不执行，当前返回明确 warning，并保留后续专用搜索 Executor 的 TODO。

## 2026-07-20 — CodeQL 数据库构建对比链路

新增 `codeQL/`，以 CodeQL database 和项目级批量查询替代逐 symbol 的 Java LSP
请求，并复用 `init.code_db.CodeDatabase` 输出同 schema 的
`codegraph.codeql.sqlite`。当前 Java 查询覆盖文件、符号、import、调用边、类型
继承/实现和方法 override；转换阶段通过 stable key 哈希映射完成线性时间关联，
同时输出逐阶段耗时，便于后续和 LSP 链路比较覆盖率与构建成本。

## 2026-07-21 — Relation Executor 直接执行领域计划

Relation Executor 已改为直接消费 `relation_semql.json`，不再从统一 SemQL 中反向
解析 property、caller、callee 和 role。Planner 输出 clause 内 AND、include clause
INTERSECT、exclude clause UNION 后 subtract 的显式集合计划。

- `file_path` 先做低成本候选收缩，graph role、caller、callee 在缩小后的集合上依次执行。
- caller/callee 复用 `codegraph.sqlite` 优先、LSP fallback 的既有查询内核，并在一次执行中复用图数据库连接与 implementation relation 缓存。
- 调用方未传入 `graph_store` 时先初始化连接，随后仍统一调用 `filter_candidates_with_store()`；不再维护第二套“自行连接并过滤”的包装函数。
- `file_path` 显式区分源文件与目录/package：文件使用精确或路径后缀匹配，目录使用路径段/目录前缀匹配。
- caller/callee 的 file 部分始终按“函数所在文件名”解释；只有进入 LSP fallback 时才解析成绝对路径。
- caller/callee 不再携带 condition 级 hop count，调用深度统一由 Relation Executor 的 `layer` 控制。
- graph role 的 include 过滤保留非函数候选；exclude 只返回真正具有目标图角色的函数，避免误删其他代码元素。
- Relation Executor 的 `graph_role` 过滤测试已通过：单元测试覆盖 `entry_point` 命中及非函数候选在 include/exclude 下的保留差异；真实项目 login 样例将 82 个 Surface 候选过滤为 24 个 entry-point 候选，执行过程无 warning。
- 当前 RelationCon 未启用的 container/code element type 不再写入 relation plan。
- `code_ql` 暂时只保留计划与未执行 warning；code_ql-only exclude 不会误删全部候选。

## 2026-07-23 — Intention Executor 混合语义决策与灰区 LLM

Intention 链路已改为直接消费 `intention_semql.json`。`IntentionPlanner` 从 include /
exclude IntentionCon 的 action、object 和 keywords 分别构造 `include_terms`、
`exclude_terms`，同时生成 Cluster、Term Embedding、动态分布和 LLM Judge 的物理
执行策略；Executor 只按计划执行，不再解析旧统一 SemQL 或自行决定阈值。

执行过程采用 `Cluster → Term Embedding → 三路决策 → Gray-only LLM`：

- Cluster 只有在候选数达到计划门槛时运行；cluster 数量不超过 2 或分差过小时不
  丢弃任何簇。正常情况下低分簇进入 rescue pool，而不是在候选级 Embedding 之前
  不可逆删除，因此 `include` 高但所在簇低的单个候选仍有机会被救回。
- Term Embedding 分别计算 include 平均覆盖分 `I` 和 exclude 任一命中的最高分
  `E`，再结合归一化 Cluster 分数 `C` 得到排序分数 `S`。`E>=0.60` 直接丢弃，
  `I>=0.55 && E<0.35` 直接保留，`I<0.20` 且无 Cluster 支持冲突时直接丢弃。
- 冲突采用有方向的语义：强 Cluster 支持但候选级 include 很弱，或 include 较高
  且 exclude 落入 `[0.35,0.60)`，都会进入 `gray_conflict`；低 Cluster 不反向
  否定高 include，因为候选级 Embedding 是更细粒度信号。
- 绝对规则未决的候选只有在原始候选数、未决候选数和 `Q90(S)-Q10(S)` 稳健跨度
  均达到门槛时才使用动态分布。Q80 以上可在满足绝对软下限后自适应保留，Q40
  以下只有同时满足绝对弱相关条件才丢弃，其余进入 high/middle/low 灰区；同分
  容差避免百分位边界产生不稳定硬过滤。
- LLM Judge 只接收各类灰区候选，优先处理 conflict 和 high gray，每批最多 5 个
  代码元素；`uncertain` 或调用失败按计划默认 fail-open 保留。明确正向不重复调用
  LLM，明确负向也不会进入高成本阶段。

执行额外保存 Cluster rescue、Embedding gray zone、各阶段 discarded 结果和
`intention_execution_report.json`，用于分析每个候选经过绝对规则、动态分布与
LLM 后的完整去向。
