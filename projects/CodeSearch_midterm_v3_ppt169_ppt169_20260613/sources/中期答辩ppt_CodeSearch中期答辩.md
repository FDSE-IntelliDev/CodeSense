# 中期答辩ppt_CodeSearch中期答辩

- Source: `中期答辩ppt_CodeSearch中期答辩.pptx`
- Total slides: 18

## Slide 1

![Slide 1 Image 1](中期答辩ppt_CodeSearch中期答辩_files/image1.jpeg)

![Slide 1 Image 2](中期答辩ppt_CodeSearch中期答辩_files/image4.png)

面向编码智能体的语义化代码检索方法研究

硕士中期答辩
题目：SemQL 2.0：面向代码智能体的条件化查询语言与执行引擎
学生姓名：黄卓谌
指导教师：彭鑫
答辩日期：2026年6月

汇报人：黄卓谌

指导老师：彭鑫

| 1

### Speaker Notes

本页用于开场，强调本次汇报的核心对象已从旧版代码检索 pipeline 升级为 SemQL 2.0：条件化查询语言与执行引擎。可简要说明项目目标是服务代码智能体在大型代码库中的可靠检索。

## Slide 2

CONTENTS

目录

研究背景与问题定义

1

研究目标与总体方案

2

SemQL 2.0 核心方法

3

优化器、执行引擎与组合重排

4

实现现状、评测设计与研发计划

5

总结与展望

6

| 2

### Speaker Notes

目录保持六个部分：先说明问题背景，再给出目标和总体方案，随后展开 SemQL 2.0 的编译、条件类型、优化执行、组合重排，最后汇报实现现状、评测设计和后续计划。

## Slide 3

1

研究背景与问题定义

1

研究背景与问题定义

Research Background and Problem Definition

从一维相似度检索转向带约束的多维代码查询

| 3

## Slide 4

研究背景：Agent 代码检索需要多维约束表达

- Grep / 词法检索
- 依赖字面命名，难以处理语义等价但命名不同的代码元素；召回受限于关键词形式。

- Dense Vector Search
- 能够捕获语义相似，但难以表达精确约束；连续相似度分数不利于组合与解释。

- 简单叠加方案
- 各检索管道相对孤立，缺少条件间逻辑关系；执行顺序无法根据代价自动优化。

- 核心问题定义
- 现有范式通常将代码检索建模为一维相似度排序，而 Agent 实际需要表达动作、对象、结构关系、排除条件与成本约束的多维匹配。

- 表达复杂
- 自然语言 Query 含有隐含业务意图与工程约束。

- 执行可控
- 高成本语义判断需要在小候选集上执行。

- 结果可解释
- Agent 需要知道命中原因与相关上下文。

| 4

### Speaker Notes

本页可按三类局限展开：Grep 快但依赖字面形式；Dense Search 能表达语义相似但缺少硬约束；二者叠加后仍缺少统一逻辑和执行优化。引出“带约束的多维匹配”这个研究问题。

## Slide 5

研究动机：面向 Agent 的查询更接近数据库查询

本研究将自然语言检索请求从“相似度排序问题”重新建模为“条件化查询计划执行问题”。

能力需求

传统搜索

SemQL 2.0 目标

复杂意图表达

关键词或向量相似度

Surface / Relation / Intention 条件组合

硬性约束与排除

依赖人工筛选

include / exclude 与 NOT-Pushdown

执行成本控制

固定流程或逐候选判断

RBO + CBO 自动规划执行顺序

上下文组织

单元素独立返回

Context Bundle 与协同重排

研究判断：只有把 Query 编译为可优化、可执行、可解释的结构化计划，才能支撑大型代码库中的 Agent 检索。

| 5

### Speaker Notes

这里强调研究范式变化：Agent 的查询更接近数据库查询而不是简单搜索，因此需要把自然语言编译成可执行的查询计划。可说明 SemQL 2.0 的定位是统一多种检索能力，而不是替换某一个模型。

## Slide 6

2

研究目标与总体方案

2

研究内容与目标

Research Content and Objectives

构建条件化查询语言与可优化执行引擎

| 6

### Speaker Notes

第二部分说明研究目标：构建一个面向代码智能体的条件化查询语言和执行引擎。过渡到后续四项能力：组合、优化、解释和上下文感知。

## Slide 7

研究内容与目标：SemQL 2.0 的四项能力

设计并实现面向代码智能体的高级查询语言与执行引擎，使自然语言 Query 能够被结构化、优化、执行并解释。

- 可组合性
- 支持 Surface、Relation、Intention 等多类型条件通过布尔逻辑组合，表达复杂检索意图。

- 可优化性
- 引入规则优化与代价优化，在候选集规模和语义判断成本之间取得平衡。

- 可解释性
- 每个匹配结果携带词法、结构、语义等多维证据，便于 Agent 继续推理。

- 上下文感知
- 从单点代码块扩展到代码子图和 Context Bundle，提高 Top-K 可用性。

最终目标：形成高召回、高精度、低成本、可解释的 Agent-Oriented Code Search 方法。

| 7

### Speaker Notes

四项目标对应 report.md 的核心目标。可组合性解决复杂条件表达；可优化性控制高成本语义判断；可解释性服务 Agent 后续推理；上下文感知通过 Bundle 返回相关代码子图。

## Slide 8

3

SemQL 2.0 核心方法

3

技术路线与系统方法

Technical Route and System Method

从自然语言到条件化查询计划

| 8

### Speaker Notes

第三部分进入核心方法，说明 SemQL 2.0 不是单个 JSON 字段 schema，而是从 NL Query 到 SemCon、SemQL、执行计划和最终结果的完整方法框架。

## Slide 9

总体技术路线：NL → SemCon → SemQL → 分层执行 → Bundle 输出

- Natural Language Query
- Agent 任务描述

- Query Compiler
- NL → SemCon → SemQL

- Query Optimizer
- RBO / CBO 生成物理计划

- Staged Execution
- Surface → Relation → Intention

- Bundle Reranking
- 代码子图协同评分

- Final Results
- 带证据的分层输出

- exact search / inverted index / ngram / term embedding
- < 200ms：快速高召回

- Evidence
- 保留匹配类型、命中条件、过滤理由与评分来源

Stage 1 Surface Execution

- symbol index / LSP call graph / CodeQL fallback
- < 10ms 到秒级：结构约束

- Evidence
- 保留匹配类型、命中条件、过滤理由与评分来源

Stage 2 Relation Execution

- cluster filter / embedding filter / LLM judge
- < 3s：高成本语义判别

- Evidence
- 保留匹配类型、命中条件、过滤理由与评分来源

Stage 3 Intention Execution

核心思想：先用低成本条件收缩候选集，再把高成本语义判断用于少量候选，最后以代码子图形式返回上下文。

| 9

### Speaker Notes

本页是全流程主图。Query Compiler 负责 NL 到 SemCon 再到 SemQL；Optimizer 将逻辑计划转为物理计划；执行阶段按 Surface、Relation、Intention 分层；最后 Bundle Reranker 以代码子图为单位重排并输出证据。

## Slide 10

查询编译器：将自然语言拆解为可执行条件

Phase 1：SemCon 抽取

Phase 2：SemQL 组织

- LLM 驱动的原子条件抽取
- 将一条 NL Query 拆解为 Surface、Relation、Intention 三类条件；输出 semCon.json，避免直接进入不可控的全库语义判断。

- SemQL 作为查询计划载体
- 按照 include / exclude 分组，将 SemCon 原子条件组织为结构化查询；不在编译阶段固化执行顺序。

- 条件类型与执行代价绑定
- 三类条件按 Surface → Relation → Intention 的代价顺序组织，为后续优化器提供可排序的逻辑计划。

- 执行逻辑由引擎动态调整
- AND / OR / NOT 等逻辑由优化器结合规则和代价进行规划，使 Query 保持可组合、可优化和可解释。

query_processing/llm_semCon_extractor.py

semCon.json

query_processing/semQL_composer.py

| 10

### Speaker Notes

SemCon 抽取由 LLM 完成，拆出 Surface、Relation、Intention 三类原子条件。SemQL 组织阶段按 include/exclude 分组，但不提前固化执行顺序，原因是执行顺序应由优化器根据代价和候选规模动态决定。

## Slide 11

4

条件类型、优化器与执行引擎

阶段成果与实验进展

4

Progress and Current Results

以代价可控的方式执行多维查询条件

| 11

### Speaker Notes

第四部分展开条件类型、优化器和执行引擎。这里可以提醒评委：三类条件不仅是语义分类，也对应不同物理执行后端和不同成本。

## Slide 12

三类条件定义：从表层信号到深层意图

- Surface Condition
- 表层/词法特征
code_element、keyword、ngram、term_embed、code_line；适合倒排索引、BM25、精确匹配和词向量扩展。

- Relation Condition
- 关系/结构特征
code_element_type、file_path、container、caller、callee、graph_constraint、code_ql；表达调用图和结构约束。

- Intention Condition
- 意图/语义特征
intent、intent_statement、aspect、non_functional_type；以二元判别建模 P(match | code, intent)。

最低成本，优先召回

中等成本，约束收敛

最高成本，后置验证

执行顺序不是简单固定流水线，而是在 Intention-Last、Surface-First 等规则约束下，由优化器结合候选规模动态规划。

| 12

### Speaker Notes

Surface 条件覆盖函数名、关键词、ngram、代码行和 term embedding，适合快速召回；Relation 条件覆盖容器、文件、调用者、被调用者和 graph_constraint；Intention 条件用于功能、领域和非功能语义，采用二元判别而非单纯相似度。

## Slide 13

5

系统实现与实验设计

研究计划与风险控制

5

Research Plan and Risk Control

从原型模块到可验证评测体系

| 13

### Speaker Notes

第五部分汇报系统实现和实验设计。过渡语可以说明：方法设计之后，需要回答两个工程问题，一是哪些模块已经实现，二是如何证明每个模块确实提升检索质量或降低成本。

## Slide 14

查询优化器与执行引擎：规则约束 + 代价估计

- RBO：不可覆盖的执行规则
- R1 Intention-Last：语义条件最后执行
R2 Surface-First：至少一个表层条件先执行
R3 Dangling Ban：禁止全库独立 Intention 判断
R4 NOT-Pushdown：排除逻辑尽量前推

- CBO：候选规模驱动的代价优化
- 通过符号索引统计预估命中数量，优先执行选择性最高的 Surface/Relation 条件；候选过大时触发 Cluster Filter 动态截断。

Step 1

Step 2

Step 3

Step 4

- c1_surface
- 候选集 ≈ 8,000

- c2_relation
- 候选集 ≈ 400

- cluster_filter
- 候选集 ≈ 80

- c3_intention
- 候选集 ≈ 15

代价目标：Cost_total = Σ Cost_i × |R_i|，即把高成本语义判别限制在足够小的候选集上。

| 14

### Speaker Notes

优化器部分可以重点解释四条 RBO 规则：Intention-Last、Surface-First、Dangling Ban、NOT-Pushdown。CBO 在规则约束下基于候选规模估计选择性；候选过大时触发 Cluster Filter 截断，使进入 Intention 阶段的候选保持在较小规模。

## Slide 15

6

组合重排、评测与研发路径

总结与展望

6

Summary and Outlook

面向最终论文实验的系统化验证

| 15

### Speaker Notes

第六部分面向论文后续工作：组合重排、评测和研发路径。Bundle 思想是把单个代码元素扩展成有结构关系的小上下文，减少 Agent 自行拼接代码上下文的成本。

## Slide 16

实现现状与后续研发路径

- 已完成模块
- 离线解析与符号索引
N-gram + 倒排索引
FastText + ICF + 语义 Embedding
SemCon 抽取与 SemQL 组织
Surface Executor 与基础过滤器

- 待实现模块
- RBO / CBO 查询优化器
内存调用图 BFS/DFS
CodeQL 后端集成
LLM-as-a-Judge
Bundle Generator + Synergy Scorer

- 评测体系
- Recall@K / Precision@K / MRR / nDCG@K
Bundle Relevance / Bundle Hit Rate
P50 / P99 延迟
LLM 调用次数与缓存命中率

Phase 1

Phase 2

Phase 3

Phase 4

概念建模与 DSL 设计

查询优化器

执行引擎完善

Bundle 重排与 Benchmark

当前

近期

中期

后期

Benchmark：SWE-bench、CodeSearchNet 与内部 Agent 任务数据集；通过消融实验验证各执行阶段的贡献。

| 16

### Speaker Notes

已完成部分包括离线解析、符号索引、N-gram 倒排、FastText+ICF、语义 embedding、SemCon 抽取、SemQL 组织和基础执行模块。待实现包括 RBO/CBO、调用图 BFS/DFS、CodeQL、LLM Judge 和 Bundle Scorer。评测指标覆盖检索质量、Bundle 质量和效率成本。

## Slide 17

方案对比与主要参考依据

SemQL 2.0 的优势不在于替代某一种检索模型，而在于将多类检索能力组织成可执行、可优化、可解释的查询系统。

检索能力

Grep

Dense Search

SemQL 2.0

语义等价召回

弱

中

term_embed + Intention 验证

硬性排除约束

弱

弱

exclude property 硬过滤

调用图距离约束

无

无

graph_constraint

关联节点整体返回

无

无

Bundle 协同评分

结果可解释性

行号

相似度

多维证据 + reasoning

- 主要参考依据
- [1] Gu et al., Deep Code Search, ICSE 2018. [2] Feng et al., CodeBERT, EMNLP 2020.
[3] SWE-bench / CodeSearchNet benchmark materials. [4] CodeSearch project implementation modules and report.md.

| 17

### Speaker Notes

对比页用于收束贡献：SemQL 2.0 在语义召回、硬过滤、调用图约束、关联节点返回和解释性上相对 Grep 与 Dense Search 形成系统优势。参考依据包括 Deep Code Search、CodeBERT、SWE-bench、CodeSearchNet 以及本项目实现材料。

## Slide 18

感谢聆听

敬请各位老师批评指正！

后续将围绕查询优化器、执行引擎、Bundle 重排与系统化评测继续推进。

汇报人：黄卓谌

指导老师：彭鑫

| 18

### Speaker Notes

结尾强调后续将完成查询优化器、执行引擎、Bundle 重排和系统化评测，并将这些实验结果整理为毕业论文的核心证据。
