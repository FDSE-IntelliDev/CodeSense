# SemQL 2.0: 面向代码智能体的条件化查询语言与执行引擎

## 1. 背景与动机

### 1.1 现有方案的局限

当前智能体在进行代码检索时，过度依赖传统的关键词匹配或单一的稠密向量索引。两种方案各有致命缺陷：

| 方案 | 问题 |
|---|---|
| Grep / 词法检索 | 无法处理语义等价的不同命名；召回受限于字面形式 |
| Dense Vector Search | 无法表达精确约束；结果是连续相似度打分，难以组合和解释 |
| 两者叠加 | 管道孤立，无法表达条件间的逻辑关系；执行顺序不可优化 |

更深层的问题在于：这两种范式都把代码检索建模为一维相似度排序，而现代 Agent 实际需要的是带约束的多维匹配。面向 Agent 的查询本质上更接近数据库查询，而不是搜索引擎排序。

### 1.2 目标

设计并实现下一代面向 Agent 的代码高级查询语言与执行引擎，核心目标是：

1. **可组合性**：支持多类型条件通过布尔逻辑组合，表达复杂意图。
2. **可优化性**：引入基于代价的查询优化器，自动规划最低代价的执行顺序。
3. **可解释性**：每个匹配结果携带多维度证据，便于 Agent 推理。
4. **上下文感知的组合召回**：超越单点代码块，以代码子图为单位进行协同重排。

---

## 2. 端到端流程总览

```
Natural Language Query
  │
  ▼
┌──────────────────────────────┐
│  Query Compiler              │  NL → SemCon → SemQL（LLM 驱动 SemCon 抽取 + Agent/人工组织 SemQL）
└──────────────────────────────┘
  │
  ▼
┌──────────────────────────────┐
│  Query Optimizer (RBO/CBO)   │  逻辑计划 → 物理执行计划（代价排序、选择性优先）
│                            │  RBO = Rule-Based Optimizer 规则优化器
│                            │  CBO = Cost-Based Optimizer 代价优化器
└──────────────────────────────┘
  │
  ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  Executor                                                                    │
│                                                                              │
│  ├── Stage 1: Surface Execution    ←  最快，exact search / inverted index / ngram / term emb
│  │                                                                           │
│  ├── Stage 2: Relation Execution   ←  次快，rule filter / LSP call graph / CodeQL
│  │                                                                           │
│  └── Stage 3: Intention Execution  ←  最慢，Cluster Filter / Embedding Filter / LLM Judge
└──────────────────────────────────────────────────────────────────────────────┘
  │
  ▼
┌──────────────────────────────┐
│  Bundle Generator            │  候选节点 → 代码子图（Context Bundles）
└──────────────────────────────┘
  │
  ▼
┌──────────────────────────────┐
│  Grouped Reranker            │  Bundle 协同评分与重排
└──────────────────────────────┘
  │
  ▼
Final Results（带证据的分层输出）
```

---

## 3. 查询编译器：NL → SemCon → SemQL

### 3.1 编译器职责

Query Compiler 接受自然语言 Query，产出结构化的 SemQL 查询计划。它分为两个阶段：

**Phase 1 — SemCon 抽取**：由大模型驱动，将一条 NL query 拆解为三类原子级语义条件：

- **Surface Condition**：词法/字面匹配条件（函数名、关键词、代码行、代码片段）
- **Intention Condition**：语义功能/业务职责条件
- **Relation Condition**：代码结构约束条件

实现文件：

```text
query_processing/llm_semCon_extractor.py
```

输出为 `semCon.json`，包含 `surface`、`intention`、`relation` 三个列表。

**Phase 2 — SemQL 组织**：将 SemCon 原子条件按 condition type 和 property 组织为 SemQL 结构。当前不在此阶段决定 AND/OR/NOT 执行逻辑，而由后续执行引擎动态调整。

实现文件：

```text
query_processing/semQL_composer.py
```

输出为 `semQL.json`，按 `include` / `exclude` 分组。

### 3.2 条件类型

三类条件按执行代价从低到高排列，反映了其在执行管道中的优先处理顺序：Surface → Relation → Intention。

---

## 4. 条件类型定义

### 4.1 Surface Condition（表层/词法特征）

**定位**：基于符号和浅层相似度的快速扫描。不依赖上下文理解，纯粹基于文本信号，可通过倒排索引或 BM25 在毫秒级响应。

**能力覆盖**：

| 子类型 | 描述 | 实现手段 |
|---|---|---|
| `code_element` | 函数名、类名、变量名、文件路径的精准匹配 | 符号索引 + FuzzyMatcher |
| `keyword` | 普通词语的关键词搜索 | 倒排索引 + N-gram |
| `ngram` | 子词、缩写、驼峰拆分的模糊匹配 | Abbreviation + Subsequence |
| `term_embed` | 基于词向量的同义词/近义词过滤 | Term-level Embedding（FastText + ICF + Intention） |
| `code_line` | 匹配代码行或代码片段 | 源码扫描 |

**JSON Schema**（来源：`DSL/surface_con.py`）：

```json
{
  "type": "surface",
  "property": "include | exclude",
  "keywords": ["<literal keyword/code text to match>"],
  "synonyms": ["<optional surface variants or synonyms>"],
  "match_kind": "code_element | code_snippet | code_line | unknown",
  "code_element_type": "<function | class | variable | file | enum | unknown>"
}
```

**对应的现有模块**：

| 匹配类型 | 模块 |
|---|---|
| `match_kind=code_element` | `search/exact_code_search.py`、`search/fuzzy_matcher.py` |
| `match_kind=code_line` | `search/exact_code_search.py` |
| `keywords/synonyms` | `search/full_term_matcher.py`、`search/invert_index_search.py` |

---

### 4.2 Relation Condition（关系/结构特征）

**定位**：描述代码的结构约束及元素间的拓扑关系。比 Surface 代价更高，因需遍历调用图或 AST 结构，但仍远低于大模型推理。

**三级物理后端（Unified Semantics, Tiered Execution）**：

| 层级 | 实现后端 | 延迟量级 | 适用约束 |
|---|---|---|---|
| Tier 1（最快） | 符号索引直查 | < 1ms | `code_element_type`、`file_path`、`container` |
| Tier 2（次快） | 内存调用图 BFS/DFS（基于 LSP 解析构建） | < 10ms | `graph_constraint`、`caller`、`callee` |
| Tier 3（精确） | 完整 CodeQL 执行（离线/缓存）| 秒级 | `code_ql` 字段；Tier 1/2 无法覆盖时 fallback |

**JSON Schema**（来源：`DSL/relation_con.py`）：

```json
{
  "type": "relation",
  "property": "include | exclude",
  "code_element_type": "<function | class | variable | file | enum | unknown>",
  "file_path": "<file path / directory / package path constraint, or null>",
  "container": "<class/module/package/container constraint, or null>",
  "graph_constraint": {
    "anchor": "<anchor symbol, 'main_entry', or 'api_route'>",
    "relation": "caller_of | callee_of | distance_leq",
    "value": "<integer hop count or symbol name>"
  },
  "caller": "<expected caller code element, or null>",
  "callee": "<expected callee code element, or null>",
  "code_ql": "<optional CodeQL query, or null>",
  "description": "<brief natural language explanation>"
}
```

**graph_constraint 说明**：

| 子字段 | 含义 | 示例 |
|---|---|---|
| `anchor` | 锚点符号 | `main_entry`（入口函数）、`api_route`（API 路由节点） |
| `relation` | 关系类型 | `caller_of`（被调用）、`callee_of`（调用）、`distance_leq`（跳数约束） |
| `value` | 约束值 | 整数跳数（如 2）或符号名（如 `login_handler`） |

示例："login entrypoint" 中 "entrypoint" 是结构约束词，映射为：

```json
{
  "graph_constraint": {
    "anchor": "api_route",
    "relation": "distance_leq",
    "value": 2
  }
}
```

即要求候选函数在调用链上离 API 路由节点不超过 2 跳。

**Tier 2 内存调用图后端**：

项目离线阶段通过 `code_parser.py` 基于 LSP 解析全库代码，产出 `symbols_index.json` 和调用关系。查询时在内存图中执行 BFS/DFS，支持以下原语：

```python
def graph_distance(target_symbol, anchor, max_hops) -> bool
def has_caller(target_symbol, caller_pattern) -> bool
def has_callee(target_symbol, callee_pattern) -> bool
```

支持增量更新：文件变更时仅重新解析受影响的边。

---

### 4.3 Intention Condition（意图/语义特征）

**定位**：表达代码的真实意图、业务逻辑或非功能性属性。这类属性无法从表层符号或结构约束中推断，必须依赖语义模型或大模型进行深层推理。由于代价最高，作为最后一个执行阶段的后置过滤器。

**关键设计决策：二元判别而非相似度打分**

将 Intention 条件统一建模为二元分类问题：

$$P(\text{match} \mid \text{code}, \text{intent}) \in [0, 1]$$

而非传统的余弦相似度。这一设计带来三个好处：

1. **逻辑代数化**：True/False 直接映射到 SemQL 中的 AND/OR/NOT 节点。
2. **模型可替换**：前期用 LLM Zero-shot，后期可蒸馏为小型 Cross-Encoder 判别模型。
3. **证据可解释**：Yes/No 的同时强制输出 reasoning，作为结果证据供 Agent 引用。

**JSON Schema**（来源：`DSL/intention_con.py`）：

```json
{
  "type": "intention",
  "property": "include | exclude",
  "intent": {
    "action": "<operation or behavior>",
    "object": "<entity/resource/domain object>"
  },
  "intent_statement": "<a declarative statement describing the required intent, answerable as Yes/No>",
  "aspect": "functional | non_functional | domain",
  "non_functional_type": "performance | security | reliability | maintainability | null",
  "keywords": ["<intention keyword or phrase>"],
  "description": "<brief natural language explanation>"
}
```

**aspect 分类**：

| aspect | 示例 | 说明 |
|---|---|---|
| `functional` | "handles user login authentication" | 代码的功能行为 |
| `non_functional` | "optimizes IO throughput via buffering" | 性能、安全等非功能属性 |
| `domain` | "belongs to payment reconciliation domain" | 业务领域归属 |

**对应的现有模块**：

| 阶段 | 模块 |
|---|---|
| 粗粒度语义过滤 | `filters/cluster_pipeline.py` |
| 细粒度 Term-level 过滤 | `filters/embedding_filter.py` |
| LLM 保底验证（后续） | `executor/intentional_executor.py` |

---

## 5. 查询优化器

### 5.1 优化目标

优化器将 SemQL 的逻辑计划转换为物理执行计划，目标是最小化总执行代价：

$$\text{Cost}_{total} = \sum_{i} \text{Cost}_i \times |R_i|$$

其中 $|R_i|$ 是第 $i$ 个执行阶段的候选集大小。

### 5.2 规则引擎

以下规则强制执行，不可被覆盖：

| 规则 | 描述 |
|---|---|
| R1: Intention-Last | Intention 条件永远在所有 Surface 和 Relation 条件之后执行 |
| R2: Surface-First | 至少一个 Surface 条件必须先于任何 Intention 条件执行 |
| R3: Dangling Ban | 不存在独立对全库运行的 Intention 条件；违规时自动注入全局 Surface 兜底 |
| R4: NOT-Pushdown | NOT 逻辑作用的条件尽量提前（在 Relation 阶段处理 exclude） |

### 5.3 代价分析器

在 RBO 规则约束内，CBO 进一步优化 Surface 与 Relation 条件的执行顺序：

1. **预估候选集大小**：通过符号索引统计，快速估算每个 Surface 条件的命中数量。
2. **选择性优先**：选择性最高（命中数最少）的 Surface 条件优先执行。
3. **动态截断**：如果进入 Intention 阶段前候选集大于阈值 T（建议初始值大型项目 1000），引擎触发 Cluster-based 粗筛进行额外削减，确保最终进入 Intention 阶段的候选集不超过 100。

### 5.4 物理执行计划示例

```
Logical Plan:
  AND(c1_surface, c2_relation, c3_intention)

Physical Plan (after RBO + CBO):
  Step 1: Execute c1_surface          →  |candidates| ≈ 8,000
  Step 2: Execute c2_relation         →  |candidates| ≈ 400
  Step 3: [if |candidates| > T]
          Execute cluster_filter      →  |candidates| ≈ 80
  Step 4: Execute c3_intention        →  final_results ≈ 15
```

---

## 6. 执行引擎

### 6.1 Surface Executor

基于现有模块执行词法匹配：

| 路径 | 模块 | 说明 |
|---|---|---|
| Exact Search | `search/exact_code_search.py` | code_element 精准匹配、code_line 扫描、fuzzy_match |
| Inverted Index Search | `search/full_term_matcher.py`、`search/invert_index_search.py` | keywords → subseqs → subtokens → symbols |
| Term Expansion | `embedding/embedding_main.py` | 用 FastText + ICF + Intention 对 subsequence 做过滤（阈值 0.4），fallback 到 top3 |

两条路径结果合并，形成高召回候选集，并按匹配类型保留证据字段。

### 6.2 Relation Executor

**Tier 1 — 符号索引后端**（`code_element_type`、`file_path`、`container`）：

直接查询预构建的 Symbol Index，O(log n) 响应，延迟 < 1ms。对应现有 `filters/cluster_pipeline.py` 之前的 rule-based filter 逻辑。

**Tier 2 — 内存调用图后端**（`graph_constraint`、`caller`、`callee`）：

基于 `code_parser.py` 离线阶段产出 `symbols_index.json` 中的调用关系，构建有向调用图。查询时执行 BFS/DFS，延迟 < 10ms。

**Tier 3 — CodeQL 后端**（`code_ql` 子类型）：

所有 Relation 条件均可自动翻译为等价 CodeQL 查询，用于精确性验证和复杂结构查询。离线执行并缓存结果。

**后端路由逻辑**：

```
code_element_type | file_path | container  →  Tier 1（Symbol Index，< 1ms）
graph_constraint | caller | callee         →  Tier 2（In-memory Call Graph，< 10ms）
code_ql                                    →  Tier 3（CodeQL，秒级，离线缓存）
Tier 1/2 失败或需形式化验证                →  fallback to Tier 3
```

### 6.3 Intention Executor

**当前已实现**：

- **Cluster Filter**（`filters/cluster_pipeline.py`）：SentenceTransformer 编码 query + 候选，AgglomerativeClustering 聚类，按 priority_1/2/3/4_discarded 分层。
- **Embedding Filter**（`filters/embedding_filter.py`）：从 SemQL 的 intention 条件抽取 query terms，与候选的 `signature`/`container`/`name` 做 term-level 相似度比对。

**后续规划 — LLM-as-a-Judge**：

- **执行形态**：对候选代码块构造 Judgment Prompt，调用大模型获取 `{ match, confidence, reasoning }`。
- **批处理与并发**：候选集按 batch 并发发送，充分利用 LLM API 并行吞吐量。
- **语义缓存**：利用向量数据库缓存 `(code_block_hash, intent_statement) → judgment_result` 映射。
- **模型降级链**：

```
Primary:   小型 Cross-Encoder（本地，低延迟，< 50ms）
Secondary: 中型 LLM（如 GPT-4o-mini，中等代价，< 1s）
Tertiary:  大型 LLM（如 GPT-4，高精度，按需启用）
```

---

## 7. 组合重排（Bundle Reranking）

### 7.1 动机

传统检索的评分单元是单个代码元素，但 Agent 实际需要的往往是一段具有内在逻辑关联的代码上下文。如果仅按单点分数排序，关联节点可能分散在结果列表中，Agent 需要自行拼凑，效率极低。

### 7.2 Bundle 构建

候选集中的节点通过以下关系形成 Bundle：

| 关系类型 | 权重 | 说明 |
|---|---|---|
| `direct_caller` | 1.0 | A 直接调用 B |
| `direct_callee` | 1.0 | A 被 B 直接调用 |
| `shared_data` | 0.6 | A 和 B 共享同一个全局变量或数据结构 |
| `same_class` | 0.7 | A 和 B 在同一个类中 |
| `same_file` | 0.4 | A 和 B 在同一个文件中 |

Bundle 生成算法：

1. 以 Intention Executor 输出的高分候选节点为图的节点集合。
2. 在内存调用图中查找候选节点间是否存在直接边或 2-hop 内的路径。
3. 将存在关联的节点合并为 Bundle；孤立节点保留为单节点 Bundle。
4. 对 Bundle 大小设置上限（建议不超过 5 个节点）。

### 7.3 协同评分算法

对每个 Bundle $B = \{v_1, v_2, \ldots, v_k\}$：

$$\text{BundleScore}(B) = \frac{1}{k} \sum_{i=1}^{k} \text{score}(v_i) + \alpha \cdot \sum_{(v_i, v_j) \in E_B} w_{ij} \cdot \min(\text{score}(v_i), \text{score}(v_j))$$

其中 $E_B$ 是 Bundle 内节点间的关系边，$w_{ij}$ 是对应关系权重，$\alpha$ 是协同激励超参数（建议初始值 0.3）。

---

## 8. 当前实现现状

### 8.1 已完成的模块

| 阶段 | 模块 | 状态 |
|---|---|---|
| 离线解析 | `code_parser.py`（LSP 解析 → symbols_index, call graph） | ✅ |
| 离线索引 | `ngram_split.py` + `invert_index.py`（N-gram + 倒排索引） | ✅ |
| 离线 Embedding | `embedding/icf_term_embedding.py`（FastText + ICF 共现通道） | ✅ |
| 离线 Embedding | `embedding/semantic_term_embedding.py`（SentenceTransformer 语义通道） | ✅ |
| 离线 Embedding | `embedding/hybrid_term_embedding.py`（双通道融合） | ✅ |
| 离线 Embedding | `embedding/pairwise_term_reranker.py`（CrossEncoder 判别重排） | ✅ |
| SemCon 抽取 | `query_processing/llm_semCon_extractor.py`（LLM 抽取 SemCon） | ✅ |
| SemQL 组织 | `query_processing/semQL_composer.py`（SemCon → SemQL，Agent 或人工组织） | ✅ |
| Surface Executor | `search/exact_code_search.py`（精准 code_element / code_line 搜索） | ✅ |
| Surface Executor | `search/full_term_matcher.py`（倒排索引 + ngram 匹配） | ✅ |
| Surface Executor | `search/fuzzy_matcher.py`（加权编辑距离模糊匹配） | ✅ |
| Relation Executor | Rule-based filtering（`code_element_type` / `file_path` / `container`） | ✅ |
| Intention Executor | `filters/cluster_pipeline.py`（粗粒度语义过滤） | ✅ |
| Intention Executor | `filters/embedding_filter.py`（细粒度 term-level 过滤） | ✅ |
| DSL Schema | `DSL/surface_con.py`、`DSL/intention_con.py`、`DSL/relation_con.py` | ✅ |

### 8.2 待实现的模块

| 阶段 | 模块 | 优先级 |
|---|---|---|
| Query Optimizer | `planner/rbo.py`（RBO 规则引擎） | 高 |
| Query Optimizer | `planner/cbo.py`（CBO 代价分析器） | 高 |
| Relation Executor | 内存调用图 BFS/DFS（graph_constraint 执行） | 高 |
| Relation Executor | CodeQL 后端集成（Tier 3） | 中 |
| Intention Executor | `executor/intentional_executor.py`（LLM-as-a-Judge） | 中 |
| Bundle Reranker | `reranker/bundle_generator.py`（Bundle 构建） | 中 |
| Bundle Reranker | `reranker/synergy_scorer.py`（协同评分） | 中 |

---

## 9. 评测体系

### 9.1 检索质量指标

| 指标 | 说明 |
|---|---|
| Recall@K | 前 K 个结果覆盖真实答案的比率 |
| Precision@K | 前 K 个结果中真实答案的比率 |
| MRR | 第一个正确结果所在位置的倒数均值 |
| nDCG@K | 考虑结果位置权重的排序质量指标 |
| Bundle Relevance | Bundle 内所有节点对 Query 的平均相关性 |
| Bundle Hit Rate | 正确 Bundle 出现在 Top-K 的比率 |

### 9.2 效率指标

| 指标 | 目标 |
|---|---|
| P50 / P99 端到端延迟 | Surface-only 查询 < 200ms；含 Intention < 3s |
| Intention 命中缓存率 | > 60%（重复查询场景） |
| LLM API 调用次数 / 查询 | 大型项目平均 < 50 次（依赖 Surface/Relation 前置过滤，候选集 < 100） |
| 候选集截断率 | Intention 阶段进入候选集 < 100 个 / 查询 |

### 9.3 Benchmark 数据集

- **SWE-bench**：从 GitHub Issue 到代码定位，天然的 Agent 代码检索场景。
- **CodeSearchNet**：标准代码语义搜索基准。
- **内部构建 Benchmark**：从真实 Agent 任务中收集 NL Query 与正确代码元素对，重点覆盖 Bundle 召回场景与非功能性 Query。

---

## 10. 开放研究问题

1. **Term Expansion 的噪声控制**：词向量膨胀容易引入噪声，如何自动学习最优膨胀深度与裁剪阈值？

2. **Intention 判别模型的冷启动**：如何从 LLM Zero-shot 的判别结果中构建高质量训练集，蒸馏训练轻量级 Cross-Encoder，在低代价下达到接近 LLM 的精度？

3. **动态 Bundle 边界**：Bundle 应覆盖多少节点？是否可以根据 Query 的意图类型动态调整 Bundle 粒度？

4. **调用图的增量维护**：大型仓库的调用图如何在代码频繁提交时高效增量更新？

5. **多仓库跨库检索**：当 Agent 需要跨越多个仓库检索时，如何在不构建全局超图的前提下实现联邦查询？

6. **SemQL 的 Agent-Friendly 语法**：Agent 是否可以直接编写 SemQL？如何设计一套对 LLM 友好（易于生成和验证）的文本语法？

---

## 11. 研发路径

### Phase 1 — 概念建模与 DSL 设计（当前）

- ✅ 定义三大条件类型的 JSON Schema（`Surface`、`Intention`、`Relation`）
- ✅ 实现 SemCon 抽取原型（LLM structured output）
- ✅ 实现 SemQL 组织结构

### Phase 2 — 查询优化器（后续）

- 实现 RBO 规则引擎：Intention-Last、Surface-First、Dangling-Ban、NOT-Pushdown
- 实现 CBO 代价估算：基于符号索引统计预估命中数量
- 实现动态截断策略

### Phase 3 — 执行引擎完善

- Relation Executor：内存调用图 BFS/DFS + CodeQL 接入
- Intention Executor：LLM-as-a-Judge + 语义缓存 + 模型降级链
- 端到端集成验证

### Phase 4 — Bundle 重排与评测

- Bundle Generator + Synergy Scorer 实现
- 端到端 Benchmark 评测（SWE-bench、CodeSearchNet、内部 Benchmark）
- 输出评测报告

---

## 12. 与传统方案的对比

| 检索能力 | Grep | Dense Vector Search | SemQL 2.0 |
|---|---|---|---|
| 召回名称不含关键词的代码 | ✗ | △ 排名不稳定 | ✓ term_embed 膨胀 + Intention 验证 |
| 硬性排除指定代码元素 | ✗ 需手动加 `-v` | ✗ 无法保证 | ✓ exclude property 硬过滤 |
| 限定调用图距离约束 | ✗ 无法表达 | ✗ 无法表达 | ✓ graph_constraint |
| 关联节点作为整体返回 | ✗ 各自独立 | ✗ 各自独立 | ✓ Bundle 协同评分 |
| 结果可解释性 | 行号 | 余弦相似度 | 多维证据 + LLM reasoning |
| 语义判别代价 | 0 | 0（离线索引） | 受控（前置过滤后候选集 < 100，大型项目 < 0.2% 全库） |
