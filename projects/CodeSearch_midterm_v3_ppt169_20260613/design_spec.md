# CodeSearch_midterm_v3 - Design Spec

> Human-readable design narrative. Machine-readable execution contract: `spec_lock.md`.

## I. Project Information

| Item | Value |
| ---- | ----- |
| **Project Name** | CodeSearch_midterm_v3 |
| **Canvas Format** | PPT 16:9 (1280×720) |
| **Page Count** | 18 |
| **Design Style** | General Consulting (Academic Defense) |
| **Target Audience** | 硕士中期答辩评委（软件工程/代码智能方向） |
| **Use Case** | 硕士中期答辩汇报 |
| **Created Date** | 2026-06-13 |

## II. Canvas Specification

| Property | Value |
| -------- | ----- |
| **Format** | PPT 16:9 |
| **Dimensions** | 1280x720 |
| **viewBox** | `0 0 1280 720` |
| **Margins** | left/right 64px, top 52px, bottom 48px |
| **Content Area** | x=64~1216, y=52~672 |

## III. Visual Theme

### Theme Style
- **Style**: General Consulting (Academic-Tech)
- **Theme**: Light theme
- **Tone**: 专业、结构化、学术答辩

### Color Scheme

| Role | HEX | Purpose |
| ---- | --- | ------- |
| **Background** | `#F7FAFC` | 页面主背景 |
| **Secondary bg** | `#EAF1F8` | 卡片与分区底色 |
| **Primary** | `#0B1F3A` | 标题、主流程线、主结构 |
| **Accent** | `#1F6FEB` | 关键强调、流程节点、箭头 |
| **Secondary accent** | `#12B5CB` | 次强调、语义增强信息 |
| **Body text** | `#0F172A` | 正文 |
| **Secondary text** | `#334155` | 副文本 |
| **Tertiary text** | `#64748B` | 注释、编号 |
| **Border/divider** | `#CBD5E1` | 卡片边框、分隔线 |
| **Success** | `#16A34A` | 成功/已完成标记 |
| **Warning** | `#DC2626` | 问题/待完成标记 |
| **White** | `#FFFFFF` | 卡片底色 |

## IV. Typography System

**Typography direction**: modern CJK sans, academic defense

| Role | Chinese | English | Fallback tail |
| ---- | ------- | ------- | ------------- |
| **Title** | "Microsoft YaHei", "PingFang SC" | Arial | sans-serif |
| **Body** | "Microsoft YaHei", "PingFang SC" | Arial | sans-serif |
| **Emphasis** | "Microsoft YaHei", "PingFang SC" | Arial | sans-serif |
| **Code** | — | Consolas, "Courier New" | monospace |

**Per-role font stacks**:
- Title: `"Microsoft YaHei", "PingFang SC", Arial, sans-serif`
- Body: `"Microsoft YaHei", "PingFang SC", Arial, sans-serif`
- Emphasis: same as Body
- Code: `Consolas, "Courier New", monospace`

**Baseline**: Body = 24px

| Purpose | Ratio to body | Typical px |
| ------- | ------------- | ---------- |
| Cover title | 2.5-5x | 60-120px |
| Chapter opener title | 2-2.5x | 48-60px |
| Page title | 1.5-2x | 36-48px |
| Subtitle | 1.2-1.5x | 29-36px |
| Body content | 1x | 24px |
| Annotation | 0.7-0.85x | 17-20px |
| Page number | 0.5-0.65x | 12-16px |

## V. Layout Principles

- **Header area**: 52px from top, page title + chapter marker
- **Content area**: y=116~672
- **Footer area**: y=672~720, page number right-aligned

## VI. Icon Usage Specification

**Library**: chunk
**Inventory**: search, code, layers, filter, sparkles, git-merge, target, arrow-right, database, checklist, workflow, schema, cpu, server, bar-chart, zap, shield, refresh, trash

## VII. Visualization Reference List

No data charts needed — this is a methods + results presentation.

## VIII. Image Resource List

| Filename | Dimensions | Ratio | Purpose | Type | Layout pattern | Acquire Via | Status | Reference | text_policy | page_role |
| -------- | --------- | ----- | ------- | --- | -------------- | ----------- | ------ | --------- | ----------- | --------- |
| pipeline_flow_bg.png | 1280x720 | 16:9 | Pipeline flow diagram background for Ch4 method overview | Diagram | #44 background image + native network/architecture diagram | ai | Pending | A clean structured vertical pipeline flow diagram showing NL Query → Compiler → Optimizer → Executor stages → Bundle → Results, with arrows connecting stages, professional dark-blue technical style | embedded | hero_page |
| schema_bg.png | 1280x720 | 16:9 | Code schema / JSON structure visual for Ch3 SemCon schema page | Diagram | #44 background image + native network/architecture diagram | ai | Pending | A structured code schema visualization with JSON-like layered blocks representing three condition types (surface/intention/relation), clean technical aesthetic | embedded | local |

### AI Image Strategy
- **Image Rendering**: `vector-illustration`
- **Image Palette**: `cool-corporate`

## IX. Content Outline

### Part 1: 研究背景与问题定义 (Slides 3-5) — KEEP AS-IS

### Part 2: 研究目标与总体方案 (Slides 6-7) — KEEP AS-IS

### Part 3: SemCon 与 SemQL — 核心条件与逻辑组合 (Slides 8-10) — REDESIGN

#### Slide 08 - Chapter Opener: SemCon 与 SemQL

- **Layout**: Chapter divider — large chapter number "3" + title + subtitle
- **Title**: SemCon 与 SemQL
- **Subtitle**: 从原子条件到逻辑组合 — 构建可执行的查询计划
- **Core message**: SemCon 定义三类原子条件，SemQL 用 AND/OR/NOT 组装为可执行查询计划。

#### Slide 09 - SemCon: 三类原子条件

- **Layout**: Three-column cards
- **Title**: SemCon：三类原子语义条件
- **Core message**: Surface（低成本召回）、Relation（结构约束收敛）、Intention（高成本后置判别）构成三级条件体系，按执行代价从低到高排列。
- **Content**:
  - Card 1 — Surface Condition (表层/词法): keywords, synonyms, match_kind, code_element_type; 负责文本精准与模糊匹配，最快执行
  - Card 2 — Relation Condition (关系/结构): code_element_type, file_path, container, graph_constraint, caller, callee, code_ql; 表达调用图和结构约束，中等成本
  - Card 3 — Intention Condition (意图/语义): intent(action+object), intent_statement, aspect, non_functional_type; 二元判别建模 P(match|code,intent)，最高成本后置

#### Slide 10 - SemQL: 条件逻辑组合

- **Layout**: Left-right split (4:6) — left shows SemCon extraction, right shows SemQL composition
- **Title**: SemQL：从原子条件到逻辑组合查询计划
- **Core message**: SemQL 将 SemCon 原子条件通过 AND / OR / NOT 组合为高层查询计划，实现复杂意图的精确表达。
- **Content**:
  - Example query: "Find the login function but not logout"
  - c1: surface, include, keywords=["login"], match_kind=code_element (匹配 login)
  - c2: relation, include, code_element_type=function (限定函数)
  - c3: intention, exclude, keywords=["logout"] (排除 logout)
  - Logic: (c1 AND c2) AND NOT c3

### Part 4: 方法流程 — 端到端系统架构 (Slides 11-12) — REDESIGN

#### Slide 11 - Chapter Opener: 方法流程

- **Layout**: Chapter divider
- **Title**: 方法流程
- **Subtitle**: 从自然语言到带证据代码子图的端到端执行架构
- **Core message**: SemQL 2.0 的完整执行链路涵盖编译器、优化器、执行引擎和组合重排四大阶段。

#### Slide 12 - 端到端流程总览

- **Layout**: Vertical pipeline flow with 5 modules, each with icon + description
- **Title**: 端到端流程：NL → SemCon → SemQL → 分层执行 → Bundle 输出
- **Core message**: 五阶段流水线按照 Surface → Relation → Intention 的代价递增顺序执行，确保高成本语义判断始终在小候选集上运行。
- **Content**:
  - Module 1: Query Compiler — LLM 抽取 SemCon + Agent/人工组织 SemQL
  - Module 2: Query Optimizer (RBO/CBO) — RBO 规则引擎 (Intention-Last, Surface-First, Dangling Ban, NOT-Pushdown) + CBO 代价分析器 (选择性优先, 动态截断)
  - Module 3: Executor (三级)
    - Stage 1: Surface Execution — exact search / inverted index / ngram / term emb (< 200ms)
    - Stage 2: Relation Execution — rule filter / LSP call graph BFS/DFS / CodeQL (< 10ms~s)
    - Stage 3: Intention Execution — Cluster Filter / Embedding Filter / LLM Judge (< 3s)
  - Module 4: Bundle Generator — 候选节点通过调用边/类/文件关系合并为代码子图 (Context Bundles)
  - Module 5: Grouped Reranker — 协同评分: BundleScore = mean(score_i) + α × synergy_bonus

### Part 5: 系统实现与实验设计 (Slides 13-14) — KEEP AS-IS (minor fixes)

### Part 6: 实验与评测 (Slides 15-17) — REDESIGN

#### Slide 15 - Chapter Opener: 实验与评测

- **Layout**: Chapter divider
- **Title**: 实验与评测
- **Subtitle**: 检索质量、执行效率与 Benchmarks 的系统化验证
- **Core message**: 结合检索质量指标、效率指标和 Benchmark 数据集全面评估 SemQL 2.0。

#### Slide 16 - 评测体系设计

- **Layout**: Two-column layout (left: quality metrics, right: efficiency metrics) + benchmark list at bottom
- **Title**: 评测指标体系
- **Core message**: 从检索质量与执行效率两个维度建立全面评测体系，覆盖 Recall、Precision、MRR、nDCG、Bundle 质量与端到端延迟。
- **Content**:
  - Left column — 检索质量: Recall@K, Precision@K, MRR, nDCG@K, Bundle Relevance, Bundle Hit Rate
  - Right column — 执行效率: P50/P99 延迟 (Surface < 200ms, Intention < 3s), Intention 缓存命中率 > 60%, LLM API 调用 < 50次/查询, 候选集截断 < 100
  - Bottom — Benchmark 数据集: SWE-bench, CodeSearchNet, 内部 Agent 任务 Benchmark

#### Slide 17 - 方案对比

- **Layout**: Full-width comparison table
- **Title**: 与传统方案对比
- **Core message**: SemQL 2.0 在语义召回、硬排除、图约束、关联返回和可解释性上相对 Grep 与 Dense Search 形成系统性优势。
- **Content**: 5-row comparison table (same as existing P17 but updated):
  - 语义等价召回: Grep(弱) / Dense(中) / SemQL 2.0(term_embed + Intention 验证)
  - 硬性排除约束: 弱 / 弱 / exclude property 硬过滤
  - 调用图距离约束: 无 / 无 / graph_constraint
  - 关联节点整体返回: 无 / 无 / Bundle 协同评分
  - 结果可解释性: 行号 / 相似度 / 多维证据 + reasoning

### Part End: 感谢页 (Slide 18) — KEEP AS-IS

## X. Speaker Notes Requirements

Speaker notes in `notes/` — one per page. Slides that are kept as-is retain their existing notes. Redesigned slides get new notes with detailed speaking points.

## XI. Technical Constraints

- viewBox: `0 0 1280 720`
- Background: `<rect>` elements
- Text: `<tspan>` for wrapping, no `<foreignObject>`
- No `rgba()`, use `fill-opacity`/`stroke-opacity`
- No `<style>`, `class`, `foreignObject`, `textPath`, `animate*`, `script`
- All text as raw Unicode
- Chunk icon library only
- `<g opacity>` forbidden