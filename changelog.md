# CodeSearch Changelog

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
