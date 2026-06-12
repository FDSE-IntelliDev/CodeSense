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
  - 抽取 lexical / semantic / structural 等原子条件
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
| `lexical` | 字面量、关键词、代码元素名、代码行、代码片段匹配 | exact search、ngram、倒排索引 |
| `semantic` | 语义功能、业务职责、领域含义 | cluster filter、embedding filter |
| `structural` | 代码结构约束，如目标类型、CodeQL 结构查询 | rule filter、CodeQL、call graph |

`exclude` / negative 逻辑可以通过 condition 的 `property=exclude` 以及 SemQL 的 NOT 组合表达。

---

## 3. Lexical Condition Schema

来源文件：

```text
DSL/lexical_con.py
```

Schema：

```python
lexical_condition = {
    "type": "lexical",
    "property": "<include|exclude>",
    "keywords": [
        "<literal keyword/code text to match; can be code element name, code line, code snippet, or normal keyword>"
    ],
    "synonyms": [
        "<optional lexical variants or synonyms; use empty list if not needed>"
    ],
    "match_kind": "<code_element|code_snippet|code_line|unknown>",
    "code_element_type": "<when kind is code_element, choose from parsers/code_element_types.py common types>"
}
```

### 作用

`lexical` condition 覆盖所有基于字面文本的搜索条件：

- 普通 keyword / synonym 搜索
- ngram / abbreviation / subtoken 搜索
- exact-like code element 搜索
- code_line / code_snippet 搜索

因此，原先的 `exact_code` 可以理解为 lexical condition 的一种强匹配形式。

---

## 4. Semantic Condition Schema

来源文件：

```text
DSL/semantic_con.py
```

Schema：

```python
semantic_condition = {
    "type": "semantic",
    "property": "<include|exclude>",
    "intent": {
        "action": "<operation or behavior; may include synonyms or behavior variants if useful>",
        "object": "<entity/resource/domain object; may include synonyms or related entities if useful>"
    },
    "keywords": [
        "<semantic keyword or phrase describing required behavior/domain meaning>"
    ],
    "semantic_labels": [
        "<optional high-level semantic labels, e.g. user authentication, token refresh>"
    ],
    "description": "<brief natural language explanation of this semantic condition>"
}
```

### 作用

`semantic` condition 描述目标代码元素需要满足的功能、职责、业务含义。

例如：

```text
handles user login authentication
validates access token
refreshes role permission cache
```

这些条件会进入：

- Cluster Filter：粗粒度语义过滤
- Embedding Filter：细粒度 term-level 语义过滤
- 后续可选的大模型保底验证

---

## 5. Structural Condition Schema

来源文件：

```text
DSL/structural_con.py
```

Schema：

```python
structural_condition = {
    "type": "structural",
    "property": "<include|exclude>",
    "code_element_type": "<target code element type; choose from common types: function|class|variable|file|enum|unknown>",
    "code_ql": "<optional CodeQL query if a suitable structural query can be generated; otherwise empty string>",
    "description": "<brief natural language explanation of this structural condition>"
}
```

### 作用

`structural` condition 用来描述代码结构约束，例如：

- 目标是函数 / 类 / 变量 / 文件
- 目标是入口函数
- 目标属于某类容器或路径
- 可以用 CodeQL 表达的结构查询

`code_element_type` 来自：

```text
parsers/code_element_types.py
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
c1: lexical condition，匹配 login
c2: structural condition，目标是 function/method
c3: semantic/lexical exclude condition，排除 logout
```

SemQL 组合逻辑为：

```text
(c1 AND c2) AND NOT c3
```

也可以表达为：

```json
{
  "conditions": [
    {"type": "lexical", "property": "include", "keywords": ["login"], "match_kind": "code_element", "code_element_type": "function"},
    {"type": "structural", "property": "include", "code_element_type": "function", "code_ql": ""},
    {"type": "semantic", "property": "exclude", "keywords": ["logout"], "description": "Candidate should not be about logout."}
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

> SemCon 负责表达“单个条件是什么”，SemQL 负责表达“这些条件如何组合”。

---

## 7. SemCon 到执行字段的映射

虽然查询理解升级为 SemCon/SemQL 两层，但后续搜索执行仍可复用现有模块。

| SemCon | 映射到现有执行阶段 |
|---|---|
| `lexical.match_kind=code_element` | `search/exact_code_search.py` |
| `lexical.match_kind=code_line` | `search/exact_code_search.py` |
| `lexical.keywords/synonyms` | `search/full_term_matcher.py`, `search/invert_index_search.py` |
| `semantic.intent` | `filters/embedding_filter.py` |
| `semantic.keywords` | `filters/cluster_pipeline.py`, `filters/embedding_filter.py` |
| `semantic.semantic_labels` | `filters/cluster_pipeline.py`, `filters/embedding_filter.py` |
| `structural.code_element_type` | rule-based filter / target filter |
| `structural.code_ql` | CodeQL executor（后续） |
| `property=exclude` | negative filtering / NOT logic |

---

## 8. 当前 End-to-End Pipeline

```text
Natural Language Query
  ↓
Step 1. SemCon Extraction
  - lexical conditions
  - semantic conditions
  - structural conditions
  ↓
Step 2. SemQL Composition
  - AND / OR / NOT
  - 将 SemCon 组合为高层查询计划
  ↓
Step 3. Search / Candidate Generation
  - exact-like lexical search
  - ngram / inverted index lexical search
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
search/exact_code_search.py
```

处理：

- code element name
- file path
- code line
- code snippet（后续）

### 9.2 普通词法 / 倒排搜索路径

对应模块：

```text
search/full_term_matcher.py
search/invert_index_search.py
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
search/exact_code_search.py
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
search/fuzzy_matcher.py
```

提供。

当前策略：

1. 先做完全匹配
2. 再做 token 数量差过滤
3. 再做 Jaccard 相似度过滤
4. 最后做 token-level 加权编辑距离

---

## 11. Lexical / Inverted Index Search

对应模块：

```text
search/full_term_matcher.py
search/invert_index_search.py
```

`full_term_matcher.py` 会：

1. 从 lexical condition / SemQL keywords 中取词
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

- structural condition 中的 `code_element_type`
- property=exclude 的条件

典型规则：

- `code_element_type=function` 时优先保留 function/method
- exclude 中出现 `logout`、`registration` 时降低或过滤负向候选

该阶段用于硬约束过滤，不做深度语义判断。

---

## 13. Cluster Filter（粗粒度语义过滤）

对应模块：

```text
filters/cluster_pipeline.py
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
filters/embedding_filter.py
```

Embedding Filter 只处理 cluster 保留下来的候选：

```text
priority_1 + priority_2 + priority_3
```

### Query terms

从结构化语义条件中抽取：

- semantic.intent.action
- semantic.intent.object
- semantic.keywords
- lexical.keywords（必要时）

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

- lexical / exact match evidence
- cluster score / tier / rank
- embedding score / evidence
- final score

---

## 16. 当前推荐流程总结

```text
1. SemCon 抽取：得到原子条件
2. SemQL 组合：用 AND / OR / NOT 形成高层查询计划
3. Search / Candidate Generation：执行 lexical/exact 条件，形成候选集合
4. Rule Filtering：执行 structural / exclude 条件
5. Cluster Filter：粗粒度 semantic 条件过滤
6. Embedding Filter：细粒度 semantic 条件过滤
7. Priority 输出：保留证据并分层返回
```

核心思想：

- **SemCon**：描述单个条件是什么
- **SemQL**：描述条件如何组合
- **Search Pipeline**：执行这些条件并合并结果
