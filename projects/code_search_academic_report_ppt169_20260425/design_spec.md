# code_search_academic_report - Design Spec

> This document is the human-readable design narrative — rationale, audience, style, color choices, content outline. It is read once by downstream roles for context.
>
> The machine-readable execution contract lives in `spec_lock.md` (short form of color / typography / icon / image decisions). Executor re-reads `spec_lock.md` before every SVG page to resist context-compression drift. Keep the two files in sync; if they diverge, `spec_lock.md` wins.

## I. Project Information

| Item | Value |
| ---- | ----- |
| **Project Name** | code_search_academic_report |
| **Canvas Format** | PPT 16:9 (1280x720) |
| **Page Count** | 12 |
| **Design Style** | General Consulting (Academic-Tech) |
| **Target Audience** | 代码智能 / 软件工程 / 信息检索方向研究者与工程师 |
| **Use Case** | 学术组会汇报、方法介绍、技术方案答辩 |
| **Created Date** | 2026-04-25 |

---

## II. Canvas Specification

| Property | Value |
| -------- | ----- |
| **Format** | PPT 16:9 |
| **Dimensions** | 1280x720 |
| **viewBox** | `0 0 1280 720` |
| **Margins** | left/right 64px, top 52px, bottom 48px |
| **Content Area** | x=64~1216, y=52~672 |

---

## III. Visual Theme

### Theme Style

- **Style**: General Consulting (Academic-Tech)
- **Theme**: Light theme
- **Tone**: 专业、结构化、可解释、技术导向

### Color Scheme

| Role | HEX | Purpose |
| ---- | --- | ------- |
| **Background** | `#F7FAFC` | 页面主背景 |
| **Secondary bg** | `#EAF1F8` | 卡片与分区底色 |
| **Primary** | `#0B1F3A` | 标题、主流程线、主结构 |
| **Accent** | `#1F6FEB` | 关键强调、流程节点、箭头 |
| **Secondary accent** | `#12B5CB` | 次强调、语义增强信息 |
| **Body text** | `#0F172A` | 正文 |
| **Secondary text** | `#334155` | 说明文字 |
| **Tertiary text** | `#64748B` | 页脚、注释 |
| **Border/divider** | `#CBD5E1` | 分割线、边框 |
| **Success** | `#16A34A` | 正向结果 |
| **Warning** | `#DC2626` | 风险与限制 |

### Gradient Scheme (if needed, using SVG syntax)

```xml
<linearGradient id="titleGradient" x1="0%" y1="0%" x2="100%" y2="0%">
  <stop offset="0%" stop-color="#0B1F3A"/>
  <stop offset="100%" stop-color="#1F6FEB"/>
</linearGradient>

<linearGradient id="accentFlow" x1="0%" y1="0%" x2="100%" y2="100%">
  <stop offset="0%" stop-color="#1F6FEB"/>
  <stop offset="100%" stop-color="#12B5CB"/>
</linearGradient>
```

---

## IV. Typography System

### Font Plan

**Typography direction**: modern CJK sans for technical presentation

| Role | Chinese | English | Fallback tail |
| ---- | ------- | ------- | ------------- |
| **Title** | `"Microsoft YaHei", "PingFang SC"` | `Arial` | `sans-serif` |
| **Body** | `"Microsoft YaHei", "PingFang SC"` | `Arial` | `sans-serif` |
| **Emphasis** | `"Microsoft YaHei", "PingFang SC"` | `Arial` | `sans-serif` |
| **Code** | — | `Consolas, "Courier New"` | `monospace` |

**Per-role font stacks**:

- Title: `"Microsoft YaHei", "PingFang SC", Arial, sans-serif`
- Body: `"Microsoft YaHei", "PingFang SC", Arial, sans-serif`
- Emphasis: `"Microsoft YaHei", "PingFang SC", Arial, sans-serif`
- Code: `Consolas, "Courier New", monospace`

### Font Size Hierarchy

**Baseline**: Body font size = 24px

| Purpose | Ratio to body | Example @ body=24 (relaxed) | Example @ body=18 (dense) | Weight |
| ------- | ------------- | --------------------------- | ------------------------- | ------ |
| Cover title (hero headline) | 2.5-5x | 60-120px | 45-90px | Bold |
| Chapter / section opener | 2-2.5x | 48-60px | 36-45px | Bold |
| Page title | 1.5-2x | 36-48px | 27-36px | Bold |
| Hero number (consulting KPIs) | 1.5-2x | 36-48px | 27-36px | Bold |
| Subtitle | 1.2-1.5x | 29-36px | 22-27px | SemiBold |
| **Body content** | **1x** | **24px** | **18px** | Regular |
| Annotation / caption | 0.7-0.85x | 17-20px | 13-15px | Regular |
| Page number / footnote | 0.5-0.65x | 12-16px | 9-12px | Regular |

---

## V. Layout Principles

### Page Structure

- **Header area**: 52px 高度，放置页标题与阶段标识
- **Content area**: 主内容区（y=120~630），按页面节奏选择 dense / anchor / breathing
- **Footer area**: 页码与简短注释（y=676 附近）

### Layout Pattern Library (combine or break as content demands)

| Pattern | Suitable Scenarios |
| ------- | ----------------- |
| **Single column centered** | 封面、总结页 |
| **Asymmetric split (3:7 / 2:8)** | 方法流程图 + 解释 |
| **Three/four column cards** | 模块并列说明 |
| **Top-bottom split** | 上流程下示例 |
| **Matrix grid (2×2)** | 方法对比、能力分解 |
| **Z-pattern / waterfall** | 检索流水线步骤展开 |
| **Center-radiating** | 查询 DSL 解析结构 |

### Spacing Specification

**Universal** (any container type):

| Element | Recommended Range | Current Project |
| ------- | ---------------- | --------------- |
| Safe margin from canvas edge | 40-60px | 52-64px |
| Content block gap | 24-40px | 28px |
| Icon-text gap | 8-16px | 10px |

**Card-based layouts**:

| Element | Recommended Range | Current Project |
| ------- | ---------------- | --------------- |
| Card gap | 20-32px | 24px |
| Card padding | 20-32px | 22px |
| Card border radius | 8-16px | 12px |
| Single-row card height | 530-600px | 540px |
| Double-row card height | 265-295px each | 274px |
| Three-column card width | 360-380px each | 368px |

**Non-card containers**:

- 行高控制在 1.45x 左右，保证投影阅读性
- breathing 页面优先使用分隔线与留白，不堆叠同质卡片
- 大流程图保持单一视觉主轴，减少跳读

---

## VI. Icon Usage Specification

### Source

- **Built-in icon library**: `templates/icons/chunk/`
- **Usage method**: Placeholder format `{{icon:chunk/icon-name}}`

### Recommended Icon List (fill as needed)

| Purpose | Icon Path | Page |
| ------- | --------- | ---- |
| query input | `{{icon:chunk/search}}` | Slide 03 |
| DSL parse | `{{icon:chunk/code}}` | Slide 04 |
| multi-search | `{{icon:chunk/layers}}` | Slide 05 |
| filtering | `{{icon:chunk/filter}}` | Slide 06 |
| single rerank | `{{icon:chunk/sparkles}}` | Slide 07 |
| group rerank | `{{icon:chunk/git-merge}}` | Slide 08 |
| output | `{{icon:chunk/target}}` | Slide 10 |

---

## VII. Visualization Reference List (if needed)

| Visualization Type | Reference Template | Used In |
| ------------------ | ------------------ | ------- |
| pipeline_flow | `templates/charts/process_flow.svg` | Slide 03 |
| structured_schema | `templates/charts/org_chart.svg` | Slide 04 |
| funnel_filter | `templates/charts/funnel_chart.svg` | Slide 06 |
| ranking_comparison | `templates/charts/grouped_bar_chart.svg` | Slide 07 |
| relation_graph | `templates/charts/mind_map.svg` | Slide 08 |

---

## VIII. Image Resource List (if needed)

本项目采用“无外部图片”策略，以矢量流程图与文本结构化表达为主，不使用照片/AI 生成图片。

---

## IX. Content Outline

### Part 1: 问题定义与总体方法

#### Slide 01 - Cover

- **Layout**: Single column centered
- **Title**: 面向 Agent Query 的高效精准 Code Search
- **Subtitle**: DSL 驱动检索与多阶段重排框架
- **Info**: CodeSearch Project / 2026

#### Slide 02 - 背景与挑战

- **Layout**: Asymmetric split (3:7)
- **Title**: 为什么传统代码检索难以满足 Agent Query
- **Content**:
  - 自然语言 query 语义复杂且约束隐式
  - 单路关键词匹配召回不足
  - 仅看局部代码元素易错失调用链相关性

#### Slide 03 - 方法总览

- **Layout**: Top-bottom split
- **Title**: End-to-End Pipeline
- **Visualization**: pipeline_flow (see VII)
- **Content**:
  - Query 输入
  - DSL 解析
  - 多路高召回检索
  - 规则+语义过滤
  - single/group rerank
  - 输出最终结果

### Part 2: DSL 与检索

#### Slide 04 - DSL-Driven Keyword Extraction

- **Layout**: Asymmetric split (4:6)
- **Title**: Step1: Query 结构化解析
- **Visualization**: structured_schema (see VII)
- **Content**:
  - Query parsing DSL 定义关键词/target/filters/exclude
  - 输入 query 映射为 structured query
  - 为后续检索与筛选提供统一语义接口

#### Slide 05 - 多路高召回检索

- **Layout**: Three-column cards
- **Title**: Step2: High-Recall Candidate Retrieval
- **Content**:
  - grep 式正则检索
  - 倒排索引检索
  - embedding 语义检索
  - 三路互补形成候选并集

### Part 3: 过滤与重排

#### Slide 06 - 候选过滤机制

- **Layout**: Top-bottom split
- **Title**: Step3: Rule + Semantic Filtering
- **Visualization**: funnel_filter (see VII)
- **Content**:
  - Rule: 用 DSL target/filter 删除不符元素
  - Semantic: LLM 校验候选语义一致性
  - 候选集从高召回收敛到高质量

#### Slide 07 - Single Reranking

- **Layout**: Matrix grid (2x2)
- **Title**: Step4.1: 单元素重排
- **Visualization**: ranking_comparison (see VII)
- **Content**:
  - LLM relevance scoring
  - token overlap scoring
  - code weight scoring（类型权重/调用距离）

#### Slide 08 - Group Reranking

- **Layout**: Asymmetric split (5:5)
- **Title**: Step4.2: 组级关系重排
- **Visualization**: relation_graph (see VII)
- **Content**:
  - 引入调用链/结构依赖关系
  - 组内共现增强弱相关元素得分
  - 案例：init_readahead -> malloc_disk

#### Slide 09 - Rerank 综合打分

- **Layout**: Three-column cards
- **Title**: 从局部相关到全局相关
- **Content**:
  - single 分数
  - group 增益分数
  - 最终融合排序

### Part 4: 案例与总结

#### Slide 10 - Query Case Study

- **Layout**: Asymmetric split (3:7)
- **Title**: 示例：disk 模块下 readahead 相关函数检索
- **Content**:
  - 输入 query 与 DSL 示例
  - 候选集逐步收敛过程
  - 最终结果解释

#### Slide 11 - 方法价值与可扩展性

- **Layout**: Matrix grid (2x2)
- **Title**: 方法优势总结
- **Content**:
  - Recall 提升
  - Precision 提升
  - 可解释性增强
  - 工程扩展（新 filter / 新 ranker）

#### Slide 12 - Conclusion

- **Layout**: Single column centered
- **Title**: Conclusion & Future Work
- **Content**:
  - 当前框架总结
  - 后续方向：在线反馈学习、跨仓库检索、端到端训练式重排

---

## X. Speaker Notes Requirements

Generate corresponding speaker note files for each page, saved to the `notes/` directory:

- **File naming**: `01_cover.md` ... `12_conclusion.md`
- **Content includes**: 讲解主线、关键术语解释、过渡语

---

## XI. Technical Constraints Reminder

### SVG Generation Must Follow:

1. viewBox: `0 0 1280 720`
2. Background uses `<rect>` elements
3. Text wrapping uses `<tspan>` (`<foreignObject>` FORBIDDEN)
4. Transparency uses `fill-opacity` / `stroke-opacity`; `rgba()` FORBIDDEN
5. FORBIDDEN: `mask`, `<style>`, `class`, `foreignObject`
6. FORBIDDEN: `textPath`, `animate*`, `script`
7. `marker-start` / `marker-end` conditionally allowed with compliant `<marker>`
8. `clipPath` only for `<image>` elements

### PPT Compatibility Rules:

- `<g opacity="...">` FORBIDDEN
- Inline styles only; external CSS and `@font-face` FORBIDDEN
- 字体与颜色严格服从 `spec_lock.md`
