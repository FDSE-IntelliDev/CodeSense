# Code Search Pipeline (SemCon → SemQL → Execution)

## 1. 背景与目标

本流程面向代码智能体（Agent）的代码搜索场景。核心目标是：

- 在大规模代码库中实现高召回（High Recall）
- 通过条件化查询、精准匹配、词法检索、语义过滤与结构化过滤提升精度（High Precision）
- 保留每个阶段的证据字段，保证结果可解释

当前查询理解分为两层：

> **SemCon 是原子级语义条件，SemQL 是这些条件经过逻辑组合后形成的高层查询计划。**

也就是说：

```text
Natural Language Query
  ↓
SemCon Extraction
  - 抽取 surface / intention / relation 等原子条件
  ↓
SemQL Composition
  - 使用 AND / OR / NOT 组合多个 SemCon
  - 形成可执行的高层查询计划
  ↓
Search / Filter / Ranking Execution
```

---

## 2. SemCon：原子级条件层

SemCon（Semantic Condition）用于表达一个独立、可执行的搜索或过滤条件。

一个 query 可以被拆成多个 SemCon，例如：

- 词法条件：需要匹配 `login`
- 语义条件：候选需要负责用户登录认证
- 结构条件：目标代码元素需要是函数或方法
- 负向条件：候选不能是 logout / registration / password reset

当前主要有三类 SemCon：

| Condition Type | 作用 | 主要执行模块 |
|---|---|---|
| `surface` | 字面量、关键词、代码元素名、代码行、代码片段匹配 | exact search、ngram、倒排索引 |
| `intention` | 语义功能、业务职责、领域含义 | cluster filter、embedding filter |
| `relation` | 代码结构约束，如目标类型、CodeQL 结构查询 | rule filter、CodeQL、call graph |

`exclude` / negative 逻辑可以通过 condition 的 `property=exclude` 以及 SemQL 的 NOT 组合表达。

---

## 3. Surface Condition Schema

来源文件：

```text
codesense/dsl/surface_con.py
```

Schema：

```python
surface_condition = {
    "type": "surface",
    "property": "<include|exclude>",
    "keywords": [
        "<literal keyword/code text to match; can be code element name, code line, code snippet, or normal keyword>"
    ],
    "synonyms": [
        "<optional surface variants or synonyms; use empty list if not needed>"
    ],
    "match_kind": "<code_element|code_snippet|code_line|unknown>",
    "code_element_type": "<when kind is code_element, choose from codesense/parsers/code_element_types.py common types>"
}
```

### 作用

`surface` condition 覆盖所有基于字面文本的搜索条件：

- 普通 keyword / synonym 搜索
- ngram / abbreviation / subtoken 搜索
- exact-like code element 搜索
- code_line / code_snippet 搜索

因此，原先的 `exact_code` 可以理解为 surface condition 的一种强匹配形式。

---

## 4. Intention Condition Schema

来源文件：

```text
codesense/dsl/intention_con.py
```

Schema：

```python
intention_condition = {
    "type": "intention",
    "property": "<include|exclude>",
    "intent": {
        "action": "<operation or behavior; may include synonyms or behavior variants if useful>",
        "object": "<entity/resource/domain object; may include synonyms or related entities if useful>"
    },
    "intent_statement": "<a declarative statement describing the required intent, answerable as Yes/No>",
    "aspect": "functional | non_functional | domain",
    "non_functional_type": "performance | security | reliability | maintainability | null",
    "keywords": [
        "<intention keyword or phrase describing required behavior/domain meaning>"
    ],
    "description": "<brief natural language explanation of this intention condition>"
}
```

### 作用

`intention` condition 描述目标代码元素需要满足的功能、业务职责、领域含义。

其中 `intent_statement` 建模为二元判别问题（Yes/No），方便后续 LLM-as-a-Judge 或 Cross-Encoder 做精确判定，而非模糊的余弦相似度排序。

`aspect` 分为三类：

| aspect | 说明 |
|---|---|
| `functional` | 代码的功能行为，如 handles user login authentication |
| `non_functional` | 性能、安全等非功能属性，如 optimizes IO throughput |
| `domain` | 业务领域归属，如 belongs to payment reconciliation domain |

`non_functional_type` 仅在 `aspect=non_functional` 时有效，取值为 `performance | security | reliability | maintainability | null`。

这些条件会进入：

- Cluster Filter：粗粒度语义过滤
- Embedding Filter：细粒度 term-level 语义过滤
- LLM-as-a-Judge：大模型保底验证

---

## 5. Relation Condition Schema

来源文件：

```text
codesense/dsl/relation_con.py
```

Schema：

```python
relation_condition = {
    "type": "relation",
    "property": "<include|exclude; code elements satisfying the following relation conditions will be included in or excluded from final results>",
    "code_element_type": "<target code element type; choose from common types: function|class|variable|file|enum|unknown>",
    "file_path": "<file path / directory / package path constraint, or None>",
    "container": "<class/module/package/container constraint, or None>",
    "graph_constraint": {
        "anchor": "<anchor symbol, 'main_entry', or 'api_route'>",
        "relation": "caller_of | callee_of | distance_leq",
        "value": "<integer hop count or symbol name>"
    },
    "caller": "<expected caller code element, or None>",
    "callee": "<expected callee code element, or None>",
    "code_ql": "<optional executable CodeQL query. Use this for relation constraints not covered by the explicit fields above, such as annotations/decorators/attributes, signature details, modifiers, entry-point detection, inheritance, framework-specific handlers, or other language-specific structures. Use None if not needed>",
    "description": "<brief natural language explanation of this relation condition>"
}
```

### 作用

`relation` condition 用来描述代码结构约束。它只把当前比较稳定、可以直接通过静态索引 / 规则处理的结构条件放成显式字段，例如：

- 目标代码元素类型：`code_element_type`
- 文件路径 / 目录 / package 约束：`file_path`
- 所属 class / module / package / container 约束：`container`
- 调用关系约束：`caller` / `callee`

新增 `graph_constraint` 用于表达调用图上的关系约束（距离、调用者、被调用者），后续由内存调用图（基于 LSP 解析构建的 NetworkX 有向图）通过 BFS/DFS 进行延迟 < 10ms 的毫秒级查询：

| 子字段 | 说明 |
|---|---|
| `anchor` | 锚点符号，如 main_entry、api_route |
| `relation` | `caller_of`（被调用）、`callee_of`（调用）、`distance_leq`（跳数约束） |
| `value` | 整数跳数或符号名 |

更复杂或强语言相关、框架相关的结构条件不再作为固定字段展开，而是交给 `code_ql` 表达，例如：

- annotation / decorator / attribute
- signature details
- modifier
- entry-point detection
- inheritance
- framework-specific handler

`property` 表示满足这些结构条件的代码元素应该进入 include 集合还是 exclude 集合。最终由 SemQL 的 AND / OR / NOT 逻辑决定这些集合如何组合。

`code_element_type` 来自：

```text
codesense/parsers/code_element_types.py
```

中的 common 类型。

---

## 6. SemQL：条件逻辑组合层

SemQL 不再只是固定字段集合，而是：

```text
SemCon 条件节点 + AND / OR / NOT 逻辑组合
```

可以理解为一个高层查询计划。

### 示例

用户 query：

```text
Find the login function but not logout
```

可以抽出：

```text
c1: surface condition，匹配 login
c2: relation condition，目标是 function/method
c3: intention/surface exclude condition，排除 logout
```

SemQL 组合逻辑为：

```text
(c1 AND c2) AND NOT c3
```

也可以表达为：

```json
{
  "conditions": [
    {"type": "surface", "property": "include", "keywords": ["login"], "match_kind": "code_element", "code_element_type": "function"},
    {"type": "relation", "property": "include", "code_element_type": "function", "code_ql": ""},
    {"type": "intention", "property": "exclude", "keywords": ["logout"], "description": "Candidate should not be about logout."}
  ],
  "logic": {
    "op": "and",
    "children": [
      "c1",
      "c2",
      {"op": "not", "children": ["c3"]}
    ]
  }
}
```

> SemCon 负责表达"单个条件是什么"，SemQL 负责表达"这些条件如何组合"。

---

## 7. SemCon 到执行字段的映射

虽然查询理解升级为 SemCon/SemQL 两层，但后续搜索执行仍可复用现有模块。

| SemCon | 映射到现有执行阶段 |
|---|---|
| `surface.match_kind=code_element` | `codesense/search/exact_code_search.py` |
| `surface.match_kind=code_line` | `codesense/search/exact_code_search.py` |
| `surface.keywords/synonyms` | `codesense/search/full_term_matcher.py`, `codesense/search/invert_index_search.py` |
| `intention.intent` | `codesense/filters/embedding_filter.py` |
| `intention.keywords` | `codesense/filters/cluster_pipeline.py`, `codesense/filters/embedding_filter.py` |
| `intention.intent_statement` | `codesense/executors/intention_executor.py`（后续 LLM-as-a-Judge 阶段） |
| `relation.code_element_type` | rule-based filter / target filter |
| `relation.file_path` | rule-based filter / path filter |
| `relation.container` | rule-based filter / container filter |
| `relation.graph_constraint` | memory call-graph BFS/DFS（Tier 2 后端） |
| `relation.code_ql` | CodeQL executor（Tier 3 后端） |
| `property=exclude` | negative filtering / NOT logic |

---

## 8. 当前 End-to-End Pipeline

```text
Natural Language Query
  ↓
Step 1. SemCon Extraction
  - surface conditions
  - intention conditions
  - relation conditions
  ↓
Step 2. SemQL Composition
  - AND / OR / NOT
  - 将 SemCon 组合为高层查询计划
  ↓
Step 3. Search / Candidate Generation
  - exact-like surface search
  - ngram / inverted index surface search
  - 合并多路搜索结果，形成高召回候选集合
  ↓
Step 4. Rule-based Filtering
  - target / code_element_type
  - exclude / negative conditions
  ↓
Step 5. Cluster Filter
  - 粗粒度语义过滤
  ↓
Step 6. Embedding Filter
  - 细粒度 term-level 过滤
  ↓
Step 7. Priority 分层输出
```

---

## 9. Search / Candidate Generation

搜索阶段负责把 SemCon/SemQL 中可用于召回的条件转成候选代码元素集合。

当前包含两条主要路径：

### 9.1 精准/强词法搜索路径

对应模块：

```text
codesense/search/exact_code_search.py
```

处理：

- code element name
- file path
- code line
- code snippet（后续）

### 9.2 普通词法 / 倒排搜索路径

对应模块：

```text
codesense/search/full_term_matcher.py
codesense/search/invert_index_search.py
```

处理：

- keywords
- synonyms
- ngram
- abbreviation
- subtoken

两条路径的结果合并，形成高召回候选集。

---

## 10. Exact Code Search

对应模块：

```text
codesense/search/exact_code_search.py
```

当前支持：

- `code_element`
  - 在 `symbols_index.json` 中匹配函数名、类名、变量名、文件等结构化元素
- `code_line`
  - 在 symbol 的源码范围内逐行扫描，命中后返回所属 symbol 和具体行号

### code_element 搜索

匹配字段：

- `name`
- `signature`
- `file`（当 code_element_type=file）

### file 路径匹配

文件匹配采用路径后缀规则：

```text
query: c.py
matches: a/b/c.py, a/d/c.py

query: b/c.py
matches: a/b/c.py, d/b/c.py

query: a/b/c.py
does not match: a/d/c.py
```

即：query path 必须是 symbol file path 的完整后缀。

### code_line 搜索

基于已有 `symbols_index.json` 中的 symbol range 扫描源码行：

```text
symbol.file + symbol.range.start_line/end_line
```

命中后返回：

- 所属 symbol
- 命中文件
- 命中行号
- 命中行内容

### fuzzy_match

由：

```text
codesense/search/fuzzy_matcher.py
```

提供。

当前策略：

1. 先做完全匹配
2. 再做 token 数量差过滤
3. 再做 Jaccard 相似度过滤
4. 最后做 token-level 加权编辑距离

---

## 11. Surface / Inverted Index Search

对应模块：

```text
codesense/search/full_term_matcher.py
codesense/search/invert_index_search.py
```

`full_term_matcher.py` 会：

1. 从 surface condition / SemQL keywords 中取词
2. 生成 ordered subterms
3. 调用 abbreviation/subsequence 逻辑
4. 用 embedding `score_pair` 对生成的 subsequence 做过滤
5. 通过 invert index 解析为 subtokens

其中 subsequence 过滤规则：

- `final_score >= 0.4` 的保留
- 如果全部低于 0.4，则 fallback 到 top3
- 原始 keyword 始终保留，避免过度过滤导致召回为空

---

## 12. Rule-based Filtering

规则过滤主要消费：

- relation condition 中的 `code_element_type`
- property=exclude 的条件

典型规则：

- `code_element_type=function` 时优先保留 function/method
- exclude 中出现 `logout`、`registration` 时降低或过滤负向候选

该阶段用于硬约束过滤，不做深度语义判断。

---

## 13. Cluster Filter（粗粒度语义过滤）

对应模块：

```text
codesense/filters/cluster_pipeline.py
```

作用：

- 将候选代码元素构造成 feature text
- 使用 sentence-transformer 编码 query 和候选
- 对候选进行聚类
- 根据 query 与 cluster centroid 的相似度，将候选分层

当前输出 priority：

```json
{
  "tiers": {
    "priority_1": [],
    "priority_2": [],
    "priority_3": [],
    "priority_4_discarded": []
  }
}
```

Cluster Filter 是粗粒度语义过滤。

---

## 14. Embedding Filter（细粒度语义过滤）

对应模块：

```text
codesense/filters/embedding_filter.py
```

Embedding Filter 只处理 cluster 保留下来的候选：

```text
priority_1 + priority_2 + priority_3
```

### Query terms

从结构化语义条件中抽取：

- intention.intent.action
- intention.intent.object
- intention.keywords
- surface.keywords（必要时）

`raw_query` 只作为 fallback。

### Candidate terms

从 symbol 的稳定结构化字段中抽取：

- `signature`
- `container`
- `name`

不使用：

- `type`
- `doc`
- `code body`

### Scoring

对每个 query term：

```text
best_score = max(score_pair_by_average_vector(query_term, candidate_term))
```

然后：

```text
embedding_score = average(best_score_per_query_term)
```

---

## 15. Priority 输出

最终推荐输出结构：

```json
{
  "kept": [],
  "discarded": [],
  "tiers": {
    "priority_1": [],
    "priority_2": [],
    "priority_3": [],
    "priority_4_discarded": []
  },
  "stats": {}
}
```

每个结果尽量保留来源解释：

- surface / exact match evidence
- cluster score / tier / rank
- embedding score / evidence
- final score

---

## 16. 当前推荐流程总结

```text
1. SemCon 抽取：得到原子条件
2. SemQL 组合：用 AND / OR / NOT 形成高层查询计划
3. Search / Candidate Generation：执行 surface/exact 条件，形成候选集合
4. Rule Filtering：执行 relation / exclude 条件
5. Cluster Filter：粗粒度 intention 条件过滤
6. Embedding Filter：细粒度 intention 条件过滤
7. Priority 输出：保留证据并分层返回
```

核心思想：

- **SemCon**：描述单个条件是什么
- **SemQL**：描述条件如何组合
- **Search Pipeline**：执行这些条件并合并结果