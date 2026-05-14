# Code Search Pipeline (SemQL-Driven)

## 1. 背景与目标
本流程面向代码智能体（Agent）的代码搜索场景。核心目标是：
- 在大规模代码库中实现高召回（High Recall）
- 通过结构化约束与语义理解提升精度（High Precision）
- 让搜索结果具备可解释性（为什么这个结果被排在前面）

本方法把 Query DSL（SemQL）作为整个流程的输入契约（Pipeline Contract），后续每个阶段按字段消费信息。

---

## 2. 示例输入 DSL

```json
{
  "intent": {
    "action": {
      "term": "handle",
      "synonyms": ["process", "manage", "authenticate", "verify"]
    },
    "object": {
      "term": "user login authentication",
      "synonyms": ["login", "user auth", "authentication flow", "sign-in"]
    }
  },
  "keywords": [
    {
      "term": "entry function",
      "synonyms": ["main entry point", "startup function", "initial handler", "bootstrap function"]
    },
    {
      "term": "user login authentication",
      "synonyms": ["login authentication", "user auth", "sign-in verification"]
    }
  ],
  "target": "function",
  "filters": [
    {"concept": "authentication", "relation": "core security domain"},
    {"concept": "entry point", "relation": "initial execution location"}
  ],
  "exclude": ["registration", "password reset", "logout"],
  "raw_query": "Find the entry function that handles user login authentication."
}
```

---

## 3. End-to-End Pipeline

### Step 1. SemQL 解析与字段分发
将自然语言查询转换为结构化字段，并把字段路由到后续模块：
- `keywords/synonyms` -> 高召回检索
- `intent(action/object)` -> 语义过滤与重排对齐
- `target` -> 规则过滤
- `filters` -> 规则+语义联合过滤
- `exclude` -> 负向筛选
- `raw_query` -> 最终语义相关性判别

当前候选集：
- 暂无（仅完成语义结构化，不做召回）

---

### Step 2. 多路高召回检索（Candidate Generation）
并行使用三类检索策略：
1. `grep`/正则检索：快速覆盖显式术语与别名
2. 倒排索引检索：基于子词（sub-token）与缩写匹配高效召回
3. Embedding 语义检索：补齐字面不匹配但语义相关的候选

输出：高召回候选代码元素集合（此时函数与类会混在一起）。

当前候选集（Step 2 后）：
- `login` (function)
- `authenticateUser` (function)
- `doFilterInternal` (function)
- `commence` (function)
- `register` (function)
- `resetPassword` (function)
- `handleLogout` (function)
- `AuthController` (class)
- `LoginService` (class)
- `AuthEntryPoint` (class)

---

### Step 3. Filtering（规则过滤 + 语义过滤）

#### 3.1 Rule-based Filtering
- 使用 `target=function`：移除 class/module/file-level 非函数候选
- 使用 `exclude=[registration,password reset,logout]`：去掉负向语义候选

规则过滤后的候选集（Step 3.1 后）：
- `login` (function)
- `authenticateUser` (function)
- `doFilterInternal` (function)
- `commence` (function)

说明：
- 被 `target=function` 筛除：`AuthController`, `LoginService`, `AuthEntryPoint`
- 被 `exclude` 筛除：`register`, `resetPassword`, `handleLogout`

#### 3.2 Semantic Filtering
利用大模型对候选代码语义进行核验：
- 是否属于认证核心域（authentication）
- 是否具备“入口函数/入口点”属性（entry point）
- 是否与 `raw_query + intent` 一致

语义过滤后的候选集（Step 3.2 后）：
- `login` (function)
- `authenticateUser` (function)
- `commence` (function)
- `doFilterInternal` (function)

说明：
- `doFilterInternal` 语义上可能更偏全局过滤逻辑，保留但会在后续重排中下调。

---

### Step 4. Reranking（Single + Group）

#### 4.1 Single Reranking（单元素打分）
对每个候选代码元素独立打分，综合：
- LLM relevance score（与 query 的语义相关性）
- Token overlap score（query token 与符号 token 的覆盖程度）
- Code weight score（元素类型、位置、结构权重）

示意分数（Step 4.1 后）：
- `authenticateUser` -> 0.92
- `login` -> 0.90
- `commence` -> 0.81
- `doFilterInternal` -> 0.73

当前候选集（按 single score 排序）：
1. `authenticateUser`
2. `login`
3. `commence`
4. `doFilterInternal`

#### 4.2 Group Reranking（组关系加权）
引入调用链/结构关系，对“单看不显著但组内关键”的元素加权。

示意分组：
- Group A: `login -> authenticateUser -> issueToken`
- Group B: `doFilterInternal -> verify`

若 query 强调“登录认证入口”，Group A 相关元素整体上浮。

当前候选集（Step 4.2 后）：
1. `login`
2. `authenticateUser`
3. `commence`
4. `doFilterInternal`

---

### Step 5. Final Result 输出
融合 single + group 分数，得到最终 Top-K。

最终候选集（Step 5 输出）：
1. `login`
2. `authenticateUser`
3. `commence`
4. `doFilterInternal`

同时输出解释信息（可用于汇报与调试）：
- 命中的关键词/同义词
- 命中的 filter 与 exclude 证据
- `target=function` 的筛除证据（哪些 class 被移除）
- 触发 group rerank 的调用链关系
- 最终得分组成（single/group）

---

## 4. 字段到阶段的映射总表

| DSL 字段 | 作用阶段 | 作用方式 |
|---|---|---|
| `keywords[].term/synonyms` | Step 2 | 多路召回检索词扩展 |
| `intent.action/object` | Step 3/4 | 语义一致性判断与重排对齐 |
| `target` | Step 3 | 硬规则过滤（筛除 class，仅保留 function） |
| `filters[]` | Step 3 | 规则 + 语义联合约束 |
| `exclude[]` | Step 3 | 负向过滤降噪 |
| `raw_query` | Step 4 | 单元素语义相关性主参考 |

