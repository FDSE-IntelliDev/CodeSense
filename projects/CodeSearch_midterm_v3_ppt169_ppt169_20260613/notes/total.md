# Slide 01
开场页。汇报题目：SemQL 2.0 面向代码智能体的条件化查询语言与执行引擎。核心目标是服务代码智能体在大型代码库中实现可靠检索。

# Slide 02
目录页。本次汇报分为六章：先说明问题背景，再给出目标和总体方案，随后展开 SemCon 与 SemQL 的核心方法，接着介绍方法流程（端到端架构），然后是实现现状与研发计划，最后是实验与评测设计，以总结与展望收尾。

# Slide 03
第一章过渡页。从一维相似度检索转向带约束的多维代码查询——这是本次研究最核心的问题定义转变。

# Slide 04
三类局限展开：Grep 快但依赖字面形式，无法处理语义等价但命名不同的代码；Dense Search 能表达语义相似但缺少硬约束，连续分数难以组合解释；二者叠加后仍缺少统一逻辑和执行优化。核心问题定义为：Agent 实际需要表达动作、对象、结构关系、排除条件与成本约束的多维匹配。

# Slide 05
本页强调研究范式变化：Agent 的查询更接近数据库查询而不是简单搜索，因此需要把自然语言编译成可执行的查询计划。SemQL 2.0 的定位是统一多种检索能力，而非替换某一个模型。四个维度对比：复杂意图表达（Surface/Relation/Intention 条件组合）、硬性约束与排除（include/exclude 与 NOT-Pushdown）、执行成本控制（RBO + CBO 自动规划）、上下文组织（Context Bundle 与协同重排）。

# Slide 06
第二章过渡页。研究目标是构建面向代码智能体的条件化查询语言与可优化执行引擎。

# Slide 07
四项目标：可组合性解决复杂条件表达（Surface/Relation/Intention 通过布尔逻辑组合）；可优化性控制高成本语义判断（规则+代价优化平衡候选规模与判断成本）；可解释性服务 Agent 后续推理（多维证据）；上下文感知通过 Bundle 返回相关代码子图。最终目标：高召回、高精度、低成本、可解释的 Agent-Oriented Code Search。

# Slide 08
第三章过渡页。SemCon 是原子级语义条件，每条 SemCon 代表一个独立可执行的搜索或过滤条件。三类条件按执行代价从低到高排列：Surface 覆盖文本匹配、Relation 覆盖结构约束、Intention 覆盖语义判别。SemQL 负责将这些原子条件通过 AND/OR/NOT 组装为可执行的高层查询计划。

# Slide 09
三栏卡片展示三类 SemCon。左侧 Surface Condition 覆盖字面匹配，关键字段包括 type/ property/keywords/synonyms/match_kind/code_element_type，执行后端包含 exact search、inverted index、ngram、fuzzy matcher 和 term embedding，执行成本最低。中间 Relation Condition 描述结构约束，关键字段包括 code_element_type/file_path/container/graph_constraint/caller/callee/code_ql，采用三级物理后端（Tier 1 Symbol Index &lt;1ms, Tier 2 LSP Call Graph BFS/DFS &lt;10ms, Tier 3 CodeQL 秒级）。右侧 Intention Condition 以二元判别建模语义意图，包含 intent_statement、aspect（functional/non_functional/domain）和 non_functional_type 字段，执行后端包括 Cluster Filter、Embedding Filter 和 LLM-as-a-Judge。三类条件按 Surface、Relation、Intention 的代价顺序组织，后续优化器会根据候选规模动态规划执行顺序。

# Slide 10
展示 SemQL 的逻辑组合机制。查询 "Find the login function but not logout" 被拆解为三个原子条件：c1 Surface include 匹配 login，c2 Relation include 限定 function 类型，c3 Intention exclude 排除 logout。三者通过 AND/NOT 组合为逻辑树：(c1 AND c2) AND NOT c3。SemCon 抽取由 LLM 完成，输出 semCon.json；SemQL 组织按 include/exclude 分组，输出 semQL.json。关键设计原则：编译阶段不固化执行顺序，AND/OR/NOT 由后续 Optimizer 动态规划，执行逻辑随候选规模自适应调整。

# Slide 11
第四章过渡页。展示 SemQL 2.0 的端到端方法流程：Query Compiler、Query Optimizer (RBO/CBO)、Executor、Bundle Generator、Grouped Reranker。核心设计思想：先用低成本条件收缩候选集，再把高成本语义判断用于少量候选。

# Slide 12
展示端到端流程的五个模块。Query Compiler 通过 LLM 抽取 SemCon 再组织为 SemQL。Query Optimizer 包含 RBO 四大规则（Intention-Last/Surface-First/Dangling Ban/NOT-Pushdown）和 CBO 代价分析器（选择性优先、动态截断）。Executor 分三级执行：Stage 1 Surface（exact search/inverted index/ngram/term emb, &lt;200ms, 约8,000候 选）、Stage 2 Relation（rule filter/LSP call graph BFS/DFS/CodeQL, &lt;10ms至秒级, 约400候 选）、Stage 3 Intention（Cluster Filter/Embedding Filter/LLM Judge, &lt;3s, 约15最终候 选）。Bundle Generator 通过调用边和类/文件关系将候选合并为 Context Bundles。Grouped Reranker 使用 BundleScore = mean(score_i) + alpha × synergy_bonus 进行协同评分。

# Slide 13
第五章过渡页。从原型模块到可验证评测体系，介绍当前实现进展与后续研发计划。

# Slide 14
已完成模块包括离线解析与符号索引、N-gram 拆分+倒排索引、FastText+ICF 共现 Embedding、SentenceTransformer 语义 Embedding、hybrid/pairwise 双通道融合、SemCon 抽取与 SemQL 组织、Surface/Relation/Intention Executor。待实现包括 RBO/CBO 查询优化器、内存调用图 BFS/DFS、CodeQL 后端集成、LLM-as-a-Judge、Bundle Generator+Synergy Scorer 和 Benchmark 评测。研发路径分四个 Phase：概念建模与 DSL 设计（当前）、查询优化器（近期）、执行引擎完善（中期）、Bundle 重排与 Benchmark（后期）。

# Slide 15
第六章过渡页。围绕实验与评测展开，介绍评测指标体系（检索质量+执行效率）和三个 Benchmark 数据集。

# Slide 16
展示评测指标体系。检索质量六大指标：Recall@K（召回能力）、Precision@K（精度）、MRR（首个正确位置倒数均值）、nDCG@K（排序质量）、Bundle Relevance（Bundle 平均相关性）、Bundle Hit Rate（正确 Bundle Top-K 比率）。执行效率四大指标：P50/P99 延迟（Surface &lt;200ms, 含 Intention &lt;3s）、Intention 缓存命中率 &gt;60%、LLM API 调用 &lt;50次/查询、候选集截断 &lt;100（大型项目 &lt;0.2% 全库）。三个 Benchmark：SWE-bench（天然 Agent 检索场景）、CodeSearchNet（标准语义搜索基准）、内部 Agent Benchmark（真实 Agent 任务数据集）。后续通过消融实验验证各阶段的贡献。

# Slide 17
对比 SemQL 2.0 与 Grep 和 Dense Search。在语义等价召回上，通过 term_embed 膨胀+Intention 验证实现高召回高精度。在硬性排除上，exclude property+NOT-Pushdown 实现确定性硬过滤。在调用图约束上，graph_constraint+BFS/DFS 在 &lt;10ms 内完成结构过滤。在关联节点返回上，Bundle 协同评分将相关代码作为整体返回。在可解释性上，多维证据（surface+relation+intention）+LLM reasoning 给 Agent 提供完整推理依据。LLM 代价受控：前置过滤后候选集 &lt;100。

# Slide 18
感谢页。后续将完成查询优化器、执行引擎、Bundle 重排和系统化评测，整理为毕业论文核心证据。
