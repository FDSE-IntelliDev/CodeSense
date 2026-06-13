# Slide 01
（此页保留原 PPTX 内容，未做修改。）

# Slide 02
（此页保留原 PPTX 内容，未做修改。）

# Slide 03
（此页保留原 PPTX 内容，未做修改。）

# Slide 04
（此页保留原 PPTX 内容，未做修改。）

# Slide 05
（此页保留原 PPTX 内容，未做修改。）

# Slide 06
（此页保留原 PPTX 内容，未做修改。）

# Slide 07
（此页保留原 PPTX 内容，未做修改。）

# Slide 08
本页是第三章的过渡页。第三章将重点介绍 SemCon 的三类原子语义条件以及 SemQL 的逻辑组合机制。SemCon 是整个系统的基础概念单元：每条 SemCon 代表一个独立、可执行的搜索或过滤条件。三类条件按执行代价从低到高排列——Surface 覆盖文本匹配、Relation 覆盖结构约束、Intention 覆盖语义判别。SemQL 则负责将这些原子条件通过 AND / OR / NOT 组装为可执行的高层查询计划。

# Slide 09
本页用三栏卡片展示 SemCon 的三类原子条件。左侧 Surface Condition 覆盖字面匹配，执行成本最低；中间 Relation Condition 描述结构约束和拓扑关系，采用三级物理后端（Tier 1 Symbol Index &lt;1ms, Tier 2 LSP Call Graph BFS/DFS &lt;10ms, Tier 3 CodeQL）；右侧 Intention Condition 以二元判别建模语义意图，包含 intent_statement、aspect（functional/non_functional/domain）和 non_functional_type 字段，执行后端包括 Cluster Filter、Embedding Filter 和 LLM-as-a-Judge。三类条件按 Surface → Relation → Intention 的代价顺序组织，后续优化器会根据候选规模动态规划执行顺序。

# Slide 10
本页展示 SemQL 的逻辑组合机制。查询 "Find the login function but not logout" 被拆解为三个原子条件：c1 Surface include 匹配 login，c2 Relation include 限定 function 类型，c3 Intention exclude 排除 logout。三者通过 AND/NOT 组合为逻辑树：(c1 AND c2) AND NOT c3。SemCon 抽取由 LLM 完成，输出 semCon.json；SemQL 组织按 include/exclude 分组，输出 semQL.json。执行逻辑留给 Query Optimizer 动态规划。

# Slide 11
本页是第四章的过渡页。第四章展示 SemQL 2.0 的端到端方法流程：Query Compiler → Query Optimizer (RBO/CBO) → Executor → Bundle Generator → Grouped Reranker。核心设计思想是先用低成本条件收缩候选集，再把高成本语义判断用于少量候选。

# Slide 12
本页展示端到端流程的五个模块。Query Compiler 通过 LLM 抽取 SemCon 再组织为 SemQL。Query Optimizer 包含 RBO 四大规则（Intention-Last/Surface-First/Dangling Ban/NOT-Pushdown）和 CBO 代价分析器（选择性优先、动态截断）。Executor 分三级执行：Stage 1 Surface（exact search/inverted index/ngram/term emb, &lt;200ms, ~8,000 候选）、Stage 2 Relation（rule filter/LSP call graph BFS/DFS/CodeQL, &lt;10ms~s, ~400 候选）、Stage 3 Intention（Cluster Filter/Embedding Filter/LLM Judge, &lt;3s, ~15 最终候选）。Bundle Generator 通过调用边和类/文件关系将候选合并为 Context Bundles。Grouped Reranker 使用 BundleScore = mean(score_i) + α × synergy_bonus 进行协同评分。

# Slide 13
（此页保留原 PPTX 内容，未做修改。）

# Slide 14
（此页保留原 PPTX 内容，未做修改。）

# Slide 15
本页是第六章的过渡页。第六章围绕实验与评测展开，介绍评测指标体系（检索质量+执行效率）和三个 Benchmark 数据集：SWE-bench（天然 Agent 检索场景）、CodeSearchNet（标准语义搜索基准）、内部 Agent Benchmark（真实 Agent 任务数据集）。

# Slide 16
本页展示评测指标体系。检索质量六大指标：Recall@K（召回能力）、Precision@K（精度）、MRR（首个正确位置倒数均值）、nDCG@K（排序质量）、Bundle Relevance（Bundle 平均相关性）、Bundle Hit Rate（正确 Bundle Top-K 比率）。执行效率四大指标：P50/P99 延迟（Surface &lt;200ms, 含 Intention &lt;3s）、Intention 缓存命中率 &gt;60%、LLM API 调用 &lt;50次/查询、候选集截断 &lt;100（大型项目 &lt;0.2% 全库）。三个 Benchmark 数据集：SWE-bench、CodeSearchNet 和内部 Agent Benchmark。后续通过消融实验验证各阶段的贡献。

# Slide 17
本页对比 SemQL 2.0 与 Grep 和 Dense Search。在语义等价召回上，SemQL 2.0 通过 term_embed 膨胀+Intention 验证实现高召回高精度。在硬性排除上，exclude property+NOT-Pushdown 实现确定性硬过滤。在调用图约束上，graph_constraint+BFS/DFS 在 &lt;10ms 内完成结构过滤。在关联节点返回上，Bundle 协同评分将相关代码作为整体返回。在可解释性上，多维证据（surface+relation+intention）+LLM reasoning 给 Agent 提供完整推理依据。LLM 代价受控：前置过滤后候选集 &lt;100。

# Slide 18
（此页保留原 PPTX 内容，未做修改。）