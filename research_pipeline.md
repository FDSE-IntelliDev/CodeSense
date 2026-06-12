# Code Search for Agent: SemQL 2.0 研发研究计划 (Solution Proposal)

## 1. 背景与动机

### 1.1 现有方案的局限

当前智能体（Agent）在进行代码检索时，过度依赖传统的关键词匹配（Grep/Lexical Search）或单一的稠密向量索引（Dense Retrieval）。两种方案各有致命缺陷：

| 方案 | 问题 |
|---|---|
| Grep / 词法检索 | 无法处理语义等价的不同命名；召回受限于字面形式 |
| Dense Vector Search | 无法表达精确约束；结果是连续相似度打分，难以组合和解释 |
| 两者叠加 | 管道孤立，无法表达条件间的逻辑关系；执行顺序不可优化 |

更深层的问题在于：这两种范式都把代码检索建模为**一维相似度排序（Ranking）**，而现代 Agent 实际需要的是**带约束的多维匹配（Constrained Retrieval）**。面向 Agent 的查询本质上更接近数据库查询，而不是搜索引擎排序。

### 1.2 目标

设计并实现下一代面向 Agent 的代码高级查询语言与执行引擎 **SemQL 2.0**，核心目标是：

1. **可组合性**：支持多类型条件（Typed Conditions）通过布尔逻辑（AND / OR / NOT）组合，表达复杂意图。
2. **可优化性**：引入基于代价的查询优化器（CBO），自动规划最低代价的执行顺序。
3. **可解释性**：每个匹配结果携带多维度证据（来源条件、匹配类型、分数），便于 Agent 推理。
4. **上下文感知的组合召回**：超越单点代码块，以代码子图（Bundle）为单位进行协同重排，满足 Agent 对完整调用链或上下文的需求。

### 1.3 端到端流程总览

```
Natural Language Query
  │
  ▼
┌──────────────────────────────┐
│  Query Compiler              │  NL → SemQL 逻辑表达式（大模型驱动）
└──────────────────────────────┘
  │
  ▼
┌──────────────────────────────┐
│  Query Optimizer (CBO/RBO)   │  逻辑计划 → 物理执行计划（代价排序）
└──────────────────────────────┘
  │
  ├─── Stage 1: Surface Execution     ←  最快，倒排/BM25/N-gram/词向量
  │
  ├─── Stage 2: Relational Execution  ←  次快，调用图/AST结构/CodeQL
  │
  └─── Stage 3: Intentional Execution ←  最慢，LLM-as-a-Judge（后置过滤）
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

## 2. 条件类型（Typed Conditions）

三类条件按执行代价从低到高排列，反映了其在执行管道中的优先处理顺序：Surface → Relational → Intentional。

### 2.1 Surface Condition（表层特征）

**定位**：基于符号和浅层相似度的快速扫描。不依赖上下文理解，纯粹基于文本信号，可通过倒排索引或 BM25 在毫秒级响应。

**能力覆盖**：

| 子类型 | 描述 | 实现手段 |
|---|---|---|
| `exact` | 函数名、类名、变量名、文件路径的精准匹配 | 符号索引（Symbol Index）|
| `keyword` | 普通词语的关键词搜索 | 倒排索引 + BM25 |
| `ngram` | 子词、缩写、驼峰拆分的模糊匹配 | N-gram 索引 |
| `term_embed` | 基于词向量的同义词/近义词膨胀与浅层语义匹配 | Term-level Embedding（非上下文嵌入）|
| `code_line` | 匹配一段代码行或代码片段 | 源码扫描 |

**词语膨胀机制（Term Expansion）**：

当用户 Query 包含 "io buffer" 时，Surface 检索会通过词向量膨胀生成扩展词集合：

```
"buffer"  →  buf, BufferedWriter, ByteBuffer, ring_buffer
"io"      →  read, write, fread, fwrite, stream, fd
```

膨胀后的词集整体进入倒排索引并行查询，召回率可以显著高于 Grep。

**JSON Schema**：

```json
{
  "type": "surface",
  "subtype": "exact | keyword | ngram | term_embed | code_line",
  "keywords": ["<primary keyword>"],
  "expanded_terms": ["<auto-expanded synonyms/ngrams>"],
  "match_target": "name | signature | file | code_line | any",
  "element_type": "function | class | variable | file | any"
}
```

---

### 2.2 Relational Condition（关系/结构特征）

**定位**：描述代码的结构约束及元素间的拓扑关系。比 Surface 代价更高，因需遍历调用图或 AST 结构，但仍远低于大模型推理。

**统一语义，分层实现（Unified Semantics, Tiered Execution）**：

所有 Relational 条件在逻辑上都可以用 CodeQL（或 tree-sitter query、SCIP/LSIF 图查询等类似引擎）精确表达。CodeQL 是 Relational 条件的**语义基准（Semantic Ground Truth）**——任何一个 Relational 子类型，都对应一段等价的 CodeQL 查询片段。

但出于交互响应速度的考量，物理执行层采用三级后端，对高频子类型使用轻量化替代实现：

| 层级 | 实现后端 | 延迟量级 | 适用子类型 |
|---|---|---|---|
| Tier 1（最快） | 符号索引直查（Symbol Index） | < 1ms | `type_constraint`、`in_module` |
| Tier 2（次快） | 内存调用图 BFS/DFS | < 10ms | `graph_distance`、`has_caller`、`has_callee` |
| Tier 3（精确） | 完整 CodeQL 执行（离线/缓存）| 秒级 | `codeql` 子类型；Tier 1/2 无法覆盖时 fallback |

这与数据库查询优化器的原理一致：同一个逻辑谓词可由索引扫描、Hash Join 或全表扫描等多种物理实现承载，优化器根据代价选择最优路径。

**能力覆盖**：

| 子类型 | 语义描述 | 等价 CodeQL 概念 | 默认物理后端 |
|---|---|---|---|
| `type_constraint` | 目标代码元素的 AST 类型约束 | `f instanceof Function` | Tier 1 |
| `graph_distance` | 调用图上目标节点到锚点的跳数约束 | `callgraph().distance(src,dst) <= n` | Tier 2 |
| `has_caller` | 目标被某函数直接或间接调用 | `f2.calls*(f1)` | Tier 2 |
| `has_callee` | 目标直接或间接调用某函数 | `f1.calls*(f2)` | Tier 2 |
| `in_module` | 目标位于某模块/包/命名空间 | `f.getFile().getRelativePath().matches(...)` | Tier 1 |
| `codeql` | 任意 CodeQL 可表达的结构约束 | 原生 CodeQL 查询 | Tier 3 |

**图距离约束说明**：

"login entrypoint" 中 "entrypoint" 是结构约束词，应映射为：

```
graph_distance(target, main_entry) < 3
```

即要求候选函数在调用链上离主入口不超过 3 跳。

**JSON Schema**：

```json
{
  "type": "relational",
  "subtype": "type_constraint | graph_distance | has_caller | has_callee | in_module | codeql",
  "element_type": "function | class | variable | file | any",
  "graph_constraint": {
    "anchor": "<anchor symbol, 'main_entry', or 'api_route'>",
    "relation": "caller_of | callee_of | distance_leq",
    "value": "<integer hop count or symbol name>"
  },
  "codeql_query": "<optional CodeQL query string>",
  "description": "<brief explanation>"
}
```

---

### 2.3 Intentional Condition（意图特征）

**定位**：表达代码的真实意图、业务逻辑或非功能性属性（如 performance、security、idempotency）。这类属性无法从表层符号或结构约束中推断，必须依赖大模型或专门的判别模型进行深层语义推理。由于代价最高，**必须作为最后一个执行阶段的后置过滤器**，且不能独立对全库扫描。

**关键设计决策：二元判别（Yes/No）而非相似度打分**

将 Intentional 条件统一建模为二元分类问题：

$$P(\text{match} \mid \text{code}, \text{intent}) \in [0, 1]$$

而非传统的余弦相似度。这一设计带来三个好处：

1. **逻辑代数化**：True/False 直接映射到 SemQL 中的 `AND/OR/NOT` 节点，查询表达式可以清晰地进行逻辑推理。
2. **模型可替换**：前期用生成式大模型（LLM Zero-shot），后期可蒸馏为小型 Cross-Encoder 判别模型，大幅降低延迟与成本。
3. **证据可解释**：Yes/No 的同时强制输出 reasoning，作为结果证据供 Agent 引用。

**判别协议（Judgment Protocol）**：

每个 Intentional 条件对应一个结构化 Prompt，强制模型返回：

```json
{
  "match": true,
  "confidence": 0.92,
  "reasoning": "The function manages a write-back buffer for disk I/O and contains explicit flush/eviction logic tied to performance thresholds."
}
```

**JSON Schema**：

```json
{
  "type": "intentional",
  "intent_statement": "<a declarative statement describing the required intent, answerable as Yes/No>",
  "aspect": "functional | non_functional | domain",
  "non_functional_type": "performance | security | reliability | maintainability | null",
  "keywords": ["<supporting keywords to hint the judge model>"],
  "description": "<brief natural language explanation>"
}
```

**aspect 分类说明**：

| aspect | 示例 | 说明 |
|---|---|---|
| `functional` | "handles user login authentication" | 代码的功能行为 |
| `non_functional` | "optimizes IO throughput via buffering" | 性能、安全等非功能属性 |
| `domain` | "belongs to payment reconciliation domain" | 业务领域归属 |

---

## 3. SemQL 2.0：条件逻辑组合层

SemQL 将多个 Typed Conditions 通过 AND / OR / NOT 组合为一棵查询逻辑树（Query Logic Tree）。

### 3.1 语法结构

```
SemQL        ::= LogicNode | ConditionRef
LogicNode    ::= { "op": "and" | "or" | "not", "children": [SemQL, ...] }
ConditionRef ::= "<condition_id>"
```

### 3.2 完整示例

Query：`"performance of IO buffer in disk"`

**Step 1 — 条件抽取（Query Compiler 输出）**：

```json
{
  "conditions": {
    "c1": {
      "type": "surface",
      "subtype": "keyword",
      "keywords": ["buffer", "io"],
      "expanded_terms": ["buf", "read", "write", "flush", "ByteBuffer", "ring_buffer"],
      "match_target": "any"
    },
    "c2": {
      "type": "relational",
      "subtype": "type_constraint",
      "element_type": "function"
    },
    "c3": {
      "type": "intentional",
      "intent_statement": "Does this code optimize IO throughput or reduce disk access latency through buffering?",
      "aspect": "non_functional",
      "non_functional_type": "performance"
    }
  }
}
```

**Step 2 — 逻辑组合（SemQL Logic Tree）**：

```json
{
  "logic": {
    "op": "and",
    "children": ["c1", "c2", "c3"]
  }
}
```

**Step 3 — 物理执行计划（CBO 排序后）**：

```
Execute(c1)  →  Execute(c2) on c1_results  →  Execute(c3) on c2_results
   Surface           Relational                     Intentional
  [~10,000 hits]    [~500 hits]                    [~30 hits]
```

---

## 4. 完整运行示例（End-to-End Running Example）

本节以一个具体 Query 展示 SemQL 2.0 的完整七步执行流程，并在末尾与传统检索方案进行量化对比。

### 4.0 输入

```
"login authentication entrypoint that validates token expiry, not registration"
```

**目标仓库**：一个典型的 Web 后端服务，含用户认证、令牌管理、注册、密码重置等模块，共约 50,000 个代码元素。

---

### 4.1 Query Compiler 输出

**片段识别与条件类型分配**：

| Query 片段 | 识别类型 | 理由 |
|---|---|---|
| "login", "token", "expiry" | Surface | 可做关键词匹配与同义词膨胀 |
| "entrypoint" | Relational | 结构约束词，表示距 API 路由节点近 |
| "validates token expiry" | Intentional | 需推理代码是否执行令牌过期校验逻辑，无法通过关键词判断 |
| "not registration" | NOT(Surface) | 排除含注册逻辑的候选 |

**SemQL 输出（逻辑计划）**：

```json
{
  "conditions": {
    "c1": {
      "type": "surface",
      "subtype": "keyword",
      "keywords": ["login", "token", "expiry"],
      "expanded_terms": ["auth", "authenticate", "jwt", "expire", "expiration", "access_token", "credential"],
      "match_target": "any"
    },
    "c2": {
      "type": "relational",
      "subtype": "graph_distance",
      "element_type": "function",
      "graph_constraint": {
        "anchor": "api_route",
        "relation": "distance_leq",
        "value": 2
      }
    },
    "c3": {
      "type": "intentional",
      "intent_statement": "Does this function validate whether an authentication token has expired?",
      "aspect": "functional",
      "keywords": ["token", "expiry", "validate", "jwt", "expire"]
    },
    "c4": {
      "type": "surface",
      "subtype": "keyword",
      "keywords": ["registration", "register", "signup"],
      "expanded_terms": ["sign_up", "create_account", "new_user"]
    }
  },
  "logic": {
    "op": "and",
    "children": [
      "c1",
      "c2",
      "c3",
      { "op": "not", "children": ["c4"] }
    ]
  }
}
```

---

### 4.2 Query Optimizer 物理执行计划

**RBO 规则检查**：

| 规则 | 检查结果 |
|---|---|
| R1: Intentional-Last | ✓ c3 排在最后执行 |
| R2: Surface-First | ✓ c1 作为第一步入口 |
| R3: Dangling Ban | ✓ c3 有 c1 的 Surface 条件兜底 |
| R4: NOT-Pushdown | ✓ NOT(c4) 提前到 Surface 阶段紧接 c1 执行 |

**CBO 代价估算与执行序**：

```
Step 1: Execute c1  (surface keywords + term expansion)
          预估命中: ~3,200 candidates     Cost: Low

Step 2: Execute NOT(c4)  (surface exclude, 过滤 registration 相关)
          过滤后剩余: ~2,810 candidates   Cost: Low

Step 3: Execute c2  (graph_distance ≤ 2 from api_route)
          过滤后剩余: ~180 candidates     Cost: Medium

Step 4: Execute c3  (LLM-as-a-Judge, validates token expiry?)
          过滤后剩余: ~12 candidates      Cost: High
                                          LLM 调用次数: 180（占全库 0.36%）
```

---

### 4.3 Surface Executor 输出（前 5 条）

```
Symbol                  Score   MatchType    File
──────────────────────────────────────────────────────────────
verify_token()          0.91    exact        auth/token.py
authenticate_user()     0.88    bm25         auth/login.py
check_token_expiry()    0.85    exact        auth/token.py
login_handler()         0.82    bm25         api/auth_routes.py
validate_credentials()  0.79    term_embed   auth/login.py
  └─ "credential" expanded from "login" via term_embed

[NOT(c4) 过滤后移除: register_user(), signup_handler(), create_account()]
```

---

### 4.4 Relational Executor 输出（graph_distance ≤ 2）

```
Symbol                  Hops  Call Chain
────────────────────────────────────────────────────────────────────
✓ login_handler()         1   POST /api/login  →  login_handler
✓ authenticate_user()     2   login_handler    →  authenticate_user
✓ verify_token()          2   login_handler    →  verify_token
✓ check_token_expiry()    2   verify_token     →  check_token_expiry

✗ token_utils.decode()    4   (超出 2 跳，过滤)
✗ validate_credentials()  3   (超出 2 跳，过滤)
```

---
### 4.5 Intentional Executor 输出（LLM Judge）

对 180 个候选调用 LLM，摘取关键判别结果：

```json
[
  {
    "symbol": "check_token_expiry",
    "match": true,
    "confidence": 0.97,
    "reasoning": "Explicitly compares token.exp claim against UTC timestamp; raises TokenExpiredError on failure."
  },
  {
    "symbol": "verify_token",
    "match": true,
    "confidence": 0.91,
    "reasoning": "Calls check_token_expiry() and catches TokenExpiredError to return HTTP 401. Directly orchestrates token expiry validation."
  },
  {
    "symbol": "authenticate_user",
    "match": false,
    "confidence": 0.82,
    "reasoning": "Focuses on credential matching; delegates token validation entirely to verify_token. Not the expiry validator itself."
  },
  {
    "symbol": "login_handler",
    "match": false,
    "confidence": 0.88,
    "reasoning": "Routing dispatcher only; expiry logic is in downstream verify_token."
  }
]
```

Intentional 过滤后保留：`check_token_expiry`（0.97）、`verify_token`（0.91）。

---

### 4.6 Bundle Generator 输出

在内存调用图中，`verify_token` 与 `check_token_expiry` 之间存在直接调用边：

```
verify_token()  ──[direct_callee]──▶  check_token_expiry()
```

合并为一个 Bundle，协同评分：

$$\text{BundleScore} = \frac{0.91 + 0.97}{2} + 0.3 \times \underbrace{1.0}_{\text{direct\_callee weight}} \times \min(0.91, 0.97) = 0.94 + 0.273 \approx 0.96$$

---

### 4.7 最终输出

```json
{
  "bundles": [
    {
      "bundle_id": "b1",
      "bundle_score": 0.96,
      "synergy": "verify_token directly calls check_token_expiry; together they form the complete token expiry validation chain.",
      "nodes": [
        {
          "symbol": "verify_token",
          "file": "auth/token.py",
          "surface_evidence": "token (exact), auth (expanded from 'login')",
          "relational_evidence": "hop=2 from POST /api/login",
          "intentional_evidence": {
            "match": true, "confidence": 0.91,
            "reasoning": "Calls check_token_expiry() and handles TokenExpiredError → HTTP 401."
          }
        },
        {
          "symbol": "check_token_expiry",
          "file": "auth/token.py",
          "surface_evidence": "expiry (exact), token (exact)",
          "relational_evidence": "hop=2, caller=verify_token",
          "intentional_evidence": {
            "match": true, "confidence": 0.97,
            "reasoning": "Compares token.exp against UTC; raises TokenExpiredError."
          }
        }
      ]
    }
  ]
}
```

Agent 拿到的不是一条孤立的代码行，而是**一个完整的调用链上下文**：`verify_token → check_token_expiry`，并附带每层证据，可以直接用于 Debug 或 Code Review。

---

### 4.8 与传统方案的对比

| 检索能力 | Grep | Dense Vector Search | SemQL 2.0 |
|---|---|---|---|
| 召回 `verify_token`（名称不含 "expiry"）| ✗ | △ 排名不稳定 | ✓ term_embed 膨胀 + Intentional 验证 |
| 硬性排除 registration 函数 | ✗ 需手动加 `-v` | ✗ 无法保证 | ✓ NOT(c4) 硬过滤 |
| 限定 "entrypoint"（距 API ≤ 2 跳）| ✗ 无法表达 | ✗ 无法表达 | ✓ graph_distance Relational 条件 |
| `verify_token` + `check_token_expiry` 作为整体返回 | ✗ 各自独立 | ✗ 各自独立 | ✓ Bundle 协同评分 |
| 结果可解释性 | 行号 | 余弦相似度 | 多维证据 + LLM reasoning |
| LLM 调用量 | 0 | 0（离线索引）| 180 次（全库 0.36%）|
| 精度（Top-3 包含正确答案）| 低 | 中 | 高 |

**关键优势总结**：SemQL 2.0 的核心价值不是单一技术点，而是将 Surface（召回）、Relational（结构约束）、Intentional（语义判别）三层严格串联，通过 CBO 保证代价可控，通过 Bundle 将孤立节点聚合为 Agent 可直接使用的上下文链路。

---

## 5. 查询编译器（Query Compiler: NL → SemQL）

> 注：第 4 节的完整运行示例已提前演示了编译器的输出，本节补充其内部设计。

### 4.1 编译器职责

Query Compiler 接受自然语言 Query，输出 SemQL 逻辑表达式。它只负责"语义理解"，不负责"执行顺序"（执行顺序由 Optimizer 决定）。

### 4.2 编译流程

```
NL Query
  │
  ├─ Intent Parsing       分析 Query 的核心意图与属性词
  │                        "performance" → non_functional intent (Intentional)
  │                        "IO buffer"   → surface keyword
  │                        "disk"        → surface keyword / domain hint
  │
  ├─ Condition Typing     对每个语义片段判断属于 Surface / Relational / Intentional
  │
  ├─ Term Expansion       对 Surface conditions 做词向量膨胀
  │                        调用 term_embed 模块生成 expanded_terms
  │
  └─ Logic Assembly       将条件组装为 SemQL Logic Tree（默认 AND）
                           允许大模型推断 OR / NOT 关系
```

### 4.3 Compiler 约束规则（强制）

- 非功能性属性词（performance、security、idempotency 等）**必须**被识别为 Intentional，而非 Surface keyword。
- 结构约束词（entrypoint、caller、subclass of 等）**必须**被识别为 Relational。
- Intentional 条件抽取后，Compiler 必须检查是否存在至少一个 Surface 条件；若不存在，自动基于 Intentional 的 `keywords` 字段补注一个 Surface 条件（Dangling Prevention）。

---

## 6. 查询优化器（Query Optimizer）

### 5.1 优化目标

优化器将 SemQL 的逻辑计划（Logical Plan）转换为物理执行计划（Physical Plan），目标是最小化总执行代价：

$$\text{Cost}_{total} = \sum_{i} \text{Cost}_i \times |R_i|$$

其中 $|R_i|$ 是第 $i$ 个执行阶段的候选集大小。

### 5.2 规则引擎（Rule-Based Optimizer, RBO）

以下规则**强制执行**，不可被覆盖：

| 规则 | 描述 |
|---|---|
| R1: Intentional-Last | Intentional 条件永远在所有 Surface 和 Relational 条件之后执行 |
| R2: Surface-First | 至少一个 Surface 条件必须先于任何 Intentional 条件执行 |
| R3: Dangling Ban | 不存在独立对全库运行的 Intentional 条件；违规时自动注入全局 Surface 兜底 |
| R4: NOT-Pushdown | NOT 逻辑作用的条件尽量提前（在 Relational 阶段处理 exclude） |

### 5.3 代价分析器（Cost-Based Optimizer, CBO）

在 RBO 规则约束内，CBO 进一步优化 Surface 与 Relational 条件的执行顺序：

1. **预估候选集大小**：通过符号索引统计，快速估算每个 Surface 条件的命中数量。
2. **选择性优先（Selectivity-First）**：选择性最高（命中数最少）的 Surface 条件优先执行。
3. **动态截断（Dynamic Truncation）**：如果进入 Intentional 阶段前候选集大于阈值 $T$（建议初始值 1000），引擎触发 Cluster-based 粗筛进行额外削减，确保 LLM Judge 的调用量在可接受范围内。

### 5.4 物理执行计划示例

```
Logical Plan:
  AND(c1_surface, c2_relational, c3_intentional)

Physical Plan (after RBO + CBO):
  Step 1: Execute c1_surface        →  candidates_1  (|candidates_1| ≈ 8,000)
  Step 2: Execute c2_relational     →  candidates_2  (|candidates_2| ≈ 400)
  Step 3: [if |candidates_2| > T]
          Execute cluster_filter    →  candidates_3  (|candidates_3| ≈ 80)
  Step 4: Execute c3_intentional    →  final_results (|final_results| ≈ 15)
```

---

## 7. 执行引擎

### 6.1 Surface Executor

基于现有 `exact_code_search.py`、`full_term_matcher.py`、`invert_index_search.py` 模块扩展，新增：

- **词向量膨胀器（Term Expander）**：利用轻量化本地词向量（fastText code embeddings 或 CodeBERT subword embeddings）生成扩展词集合，膨胀后统一进入倒排索引查询。
- **多路合并（Multi-path Merge）**：exact 路径与 keyword/ngram 路径结果合并，按匹配类型保留证据字段（`match_type: exact | bm25 | ngram | term_embed`）。

### 6.2 Relational Executor

Relational Executor 实现 Section 2.2 中的三级后端，对外暴露统一接口，调用方只需传入逻辑 Relational 条件，Executor 自动路由到最优的物理实现。

**统一接口**：

```python
def execute_relational(condition: RelationalCondition, candidates: list[Symbol]) -> list[Symbol]
    # 根据 condition.subtype 自动选择 Tier 1 / 2 / 3 后端
```

**Tier 1 — 符号索引后端**（`type_constraint`、`in_module`）：

- 直接查询预构建的 Symbol Index，O(1) 或 O(log n) 响应，延迟 < 1ms。
- 无需构建调用图。

**Tier 2 — 内存调用图后端**（`graph_distance`、`has_caller`、`has_callee`）：

- 项目加载/更新时基于 AST 或 LSIF/SCIP 解析全库调用关系，构建有向调用图（NetworkX 或邻接表）。
- 查询时在内存图中执行 BFS/DFS，延迟 < 10ms。
- 支持增量更新：文件变更时仅重新解析受影响的边。

```python
def graph_distance(target_symbol, anchor, max_hops) -> bool
def has_caller(target_symbol, caller_pattern) -> bool
def has_callee(target_symbol, callee_pattern) -> bool
```

**Tier 3 — CodeQL 后端**（`codeql` 子类型；或 Tier 1/2 无法覆盖时的 fallback）：

- 所有 Relational 条件均可自动翻译为等价 CodeQL 查询。CodeQL 是语义 ground truth，用于精确性验证和复杂跨文件/跨模块结构查询。
- 离线执行并缓存结果，避免每次查询重跑。

**后端路由逻辑**：

```
type_constraint | in_module      →  Tier 1（Symbol Index，< 1ms）
graph_distance | has_caller
              | has_callee      →  Tier 2（In-memory Call Graph，< 10ms）
codeql                         →  Tier 3（CodeQL，秒级，离线缓存）
Tier 1/2 失败或需形式化验证    →  fallback to Tier 3
```

### 6.3 Intentional Executor（LLM-as-a-Judge）

- **执行形态**：对每个候选代码块，构造 Judgment Prompt，调用大模型（或 Cross-Encoder 判别模型），获取 `{ match, confidence, reasoning }` 三元组。
- **批处理与并发**：候选集按 batch 并发发送，充分利用 LLM API 的并行吞吐量。
- **语义缓存（Semantic Cache）**：利用向量数据库缓存 `(code_block_hash, intent_statement) → judgment_result` 映射，避免重复推理。
- **模型降级链（Model Fallback Chain）**：

```
Primary:   小型 Cross-Encoder（本地，低延迟，<50ms）
Secondary: 中型 LLM（如 GPT-4o-mini，中等代价，<1s）
Tertiary:  大型 LLM（如 GPT-4，高精度，按需启用）
```

---

## 8. 组合重排（Grouped Reranking / Context Bundle）

### 7.1 动机

传统检索的评分单元是单个代码元素（Pointwise），但 Agent 实际需要的往往是一段具有内在逻辑关联的代码上下文。例如：

- 一个 IO 缓冲写入函数（writer）+ 其调用的底层 flush 函数，两者放在一起才能完整说明 IO 性能的关键路径。
- 一个类的初始化函数 + 其核心方法，构成完整的使用上下文。

如果仅按 Pointwise 分数排序，这些关联节点可能分散在排序列表中，Agent 需要自行拼凑，效率极低。

### 7.2 Bundle 构建

候选集中的节点通过以下关系形成 Bundle：

| 关系类型 | 权重 | 说明 |
|---|---|---|
| `direct_caller` | 1.0 | A 直接调用 B |
| `direct_callee` | 1.0 | A 被 B 直接调用 |
| `shared_data` | 0.6 | A 和 B 共享同一个全局变量或数据结构 |
| `same_class` | 0.7 | A 和 B 在同一个类中 |
| `same_file` | 0.4 | A 和 B 在同一个文件中 |

Bundle 生成算法（连通子图发现）：

1. 以 Intentional Executor 输出的高分候选节点为图的节点集合。
2. 在内存调用图中查找候选节点间是否存在直接边或 2-hop 内的路径。
3. 将存在关联的节点合并为 Bundle；孤立节点保留为单节点 Bundle。
4. 对 Bundle 大小设置上限（建议不超过 5 个节点），避免过度扩张引入无关代码。

### 7.3 协同评分算法（Synergy Scoring）

对每个 Bundle $B = \{v_1, v_2, \ldots, v_k\}$：

$$\text{BundleScore}(B) = \frac{1}{k} \sum_{i=1}^{k} \text{score}(v_i) + \alpha \cdot \text{SynergyBonus}(B)$$

其中：

$$\text{SynergyBonus}(B) = \sum_{(v_i, v_j) \in E_B} w_{ij} \cdot \min(\text{score}(v_i), \text{score}(v_j))$$

$E_B$ 是 Bundle 内节点间的关系边，$w_{ij}$ 是对应关系权重，$\alpha$ 是协同激励超参数（建议初始值 0.3）。

### 7.4 输出结构

```json
{
  "bundles": [
    {
      "bundle_id": "b1",
      "nodes": ["write_buffer", "flush_to_disk"],
      "bundle_score": 0.91,
      "synergy_bonus": 0.18,
      "relation": "direct_caller",
      "evidence": {
        "write_buffer": {
          "surface_match": "buffer, io",
          "intentional_match": true,
          "confidence": 0.94,
          "reasoning": "Manages a write-back buffer with configurable flush thresholds."
        },
        "flush_to_disk": {
          "surface_match": "flush, write",
          "intentional_match": true,
          "confidence": 0.87,
          "reasoning": "Performs the actual disk write with O_DIRECT for bypass buffering."
        }
      }
    },
    {
      "bundle_id": "b2",
      "nodes": ["DiskIOManager"],
      "bundle_score": 0.76,
      "synergy_bonus": 0.0,
      "relation": "singleton"
    }
  ]
}
```

---

## 9. 评测体系

### 8.1 检索质量指标

| 指标 | 说明 |
|---|---|
| Recall@K | 前 K 个结果覆盖真实答案的比率，衡量召回能力 |
| Precision@K | 前 K 个结果中真实答案的比率，衡量精度 |
| MRR (Mean Reciprocal Rank) | 第一个正确结果所在位置的倒数均值 |
| nDCG@K | 考虑结果位置权重的排序质量指标 |
| Bundle Relevance | Bundle 内所有节点对 Query 的平均相关性（人工标注） |
| Bundle Hit Rate | 正确 Bundle（含全部必要节点）出现在 Top-K 的比率 |

### 8.2 效率指标

| 指标 | 目标 |
|---|---|
| P50 / P99 端到端延迟 | Surface-only 查询 < 200ms；含 Intentional < 3s |
| Intentional 命中缓存率 | > 60%（重复查询场景） |
| LLM API 调用次数 / 查询 | 平均 < 50 次（依赖 Surface/Relational 前置过滤） |
| 候选集截断率 | Intentional 阶段进入候选集 < 100 个 / 查询 |

### 8.3 Benchmark 数据集

- **SWE-bench**：从 GitHub Issue 到代码定位，天然的 Agent 代码检索场景，覆盖多跳推理和调用链发现。
- **CodeSearchNet**：标准代码语义搜索基准，用于 Surface + Intentional 联合评测。
- **内部构建 Benchmark**：从真实 Agent 任务（Debug、Feature addition、Refactor）中收集自然语言 Query 与对应正确代码元素对，重点覆盖 Bundle 召回场景与非功能性 Query（如 performance、security）。

---

## 10. 开放研究问题

1. **Term Expansion 的噪声控制**：词向量膨胀容易引入噪声（如 "buffer" 膨胀出 "queue"），如何自动学习每个 Query 的最优膨胀深度与裁剪阈值？

2. **Intentional 判别模型的冷启动**：如何从 LLM Zero-shot 的判别结果中构建高质量训练集，蒸馏训练轻量级 Cross-Encoder，在低代价下达到接近 LLM 的精度？

3. **动态 Bundle 边界**：Bundle 应覆盖多少节点？过小则上下文不完整，过大则引入无关代码。是否可以根据 Query 的意图类型（Debug vs. Feature vs. Refactor）动态调整 Bundle 粒度？

4. **Relational 图的增量维护**：大型仓库的调用图如何在代码频繁提交时高效增量更新，而非每次全量重建？

5. **多仓库跨库检索**：当 Agent 需要跨越多个仓库检索时，如何在不构建全局超图的前提下实现联邦查询？

6. **SemQL 的 LLM 友好文本语法**：Agent 是否可以直接编写 SemQL？如果可以，如何设计一套对 LLM 友好（易于生成和验证）的文本语法，使得大模型能以极低错误率直接输出合法表达式？

---

## 11. 四阶段研发计划路径（Four-Phase Timeline）

### Phase 1 — 概念建模与 DSL 设计（~3 weeks）

**目标**：确立 SemQL 2.0 的完整语法与语义规范。

- 定义三大条件类型的 JSON Schema（`SurfaceCond`, `RelationalCond`, `IntentionalCond`），覆盖所有子类型。
- 实现 Query Compiler 原型：基于大模型 structured output，输入 NL Query，输出合法的 SemQL JSON。
- 制定 Intentional 判别协议（Judgment Protocol）：Prompt 模板、强制输出 schema、confidence 阈值定义。
- 编写 SemQL Schema 合法性校验器，确保 Compiler 输出符合规范。

**交付物**：`DSL/surface_con.py`、`DSL/relational_con.py`、`DSL/intentional_con.py`、`DSL/semql_schema.json`

### Phase 2 — 查询规划器与优化器（~3 weeks）

**目标**：实现 RBO 强制规则 + CBO 代价估算，生成最优物理执行计划。

- 实现 RBO 规则引擎：强制 Intentional-Last、Surface-First、Dangling-Ban、NOT-Pushdown 规则。
- 实现 CBO 代价估算：基于符号索引统计，对每个 Surface 条件预估命中数量。
- 实现动态截断策略：候选集大于阈值 T 时，触发 Cluster-based 粗筛后再进入 Intentional 阶段。
- 实现 Physical Plan 输出格式，便于调试与可视化执行计划。

**交付物**：`planner/rbo.py`、`planner/cbo.py`、`planner/physical_plan.py`

### Phase 3 — 多模态执行引擎落地（~4 weeks）

**目标**：落地三条执行路径，确保每条路径可独立运行并输出带证据的结果。

- **Surface Executor 升级**：集成 Term Expander，合并 exact / keyword / ngram 多路径，统一带证据的结果结构。
- **Relational Executor**：构建全库内存调用图，实现四个图查询原语，支持 BFS 查询与 CodeQL 离线接入。
- **Intentional Executor**：实现批处理 + 并发 LLM 调用、Judgment Protocol 标准化、语义缓存及三级模型降级链。

**交付物**：`executor/surface_executor.py`、`executor/relational_executor.py`、`executor/intentional_executor.py`

### Phase 4 — Bundle 生成与 Grouped Reranking（~3 weeks）

**目标**：实现协同评分重排机制，将结果从扁平列表重构为带上下文的 Bundle 层级输出。

- 实现 Bundle Generator：基于内存调用图，在候选节点集合内识别强关联连通子图，支持大小上限控制。
- 实现 Synergy Scoring 算法，调优协同激励超参数 $\alpha$。
- 实现带证据的分层输出格式（Bundle JSON），供 Agent 直接消费。
- 端到端集成测试：跑通从 NL Query → SemQL → Physical Plan → Execution → Bundle Output 的完整链路。

**交付物**：`reranker/bundle_generator.py`、`reranker/synergy_scorer.py`、端到端集成测试套件

---

## 12. 里程碑与 Action Items

| 时间节点 | 关键产出 |
|---|---|
| Week 1–2 | 三大条件类型 JSON Schema 终稿；Query Compiler 原型可跑通至少 5 个样例 Query |
| Week 3 | Intentional 判别协议完成；Schema 合法性校验器上线 |
| Week 4–5 | RBO 规则引擎完成；CBO 代价估算原型验证 |
| Week 6 | Surface Executor 集成 Term Expansion，Recall@10 相比 baseline 提升 ≥ 15% |
| Week 7–8 | Relational Executor 内存调用图完成；graph_distance 原语可查询 |
| Week 9–10 | Intentional Executor 上线，含语义缓存；Surface → Relational → Intentional 链路端到端验证 |
| Week 11–12 | Bundle Generator + Synergy Scorer 完成；Bundle Relevance 评测指标建立 |
| Week 13 | 全链路集成测试；对比 SWE-bench / CodeSearchNet baseline，输出评测报告 |