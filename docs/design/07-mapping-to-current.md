# 07 与当前实现的对应

这套设计不是推倒重来。**大部分执行层能力已经有了**，缺的是组织方式。

## 概念对应

| 新设计 | 当前实现 | 差别 |
|---|---|---|
| Query Unit | `SurfaceKeywordGroup`（组内 OR、组间 AND） | 现在的组只在匹配阶段有身份，匹配完拍平；新设计里单元贯穿到最终证据 |
| `match` | `FullTermMatcher` + 倒排索引 + 缩写扩展 | 基本可直接用，需补 `field` 参数（现在只匹配名字相关字段） |
| `hop` | `SurfaceGroupLogic(graph_scope, hop_count)` + `RelationGraphStore.reachable` | **现在返回可达元素集，路径在遍历时就丢了**——最大的一处改动 |
| `degree` | `filter_candidates_by_roles`（entry_point/leaf/isolate） | 三个命名角色 → 通用出入度谓词 |
| `of_type` | `filter_symbols_by_type` | 可直接用 |
| `intent` | `LLMJudgeFilter` | 批量、结构化返回、三分（kept/discarded/uncertain）都有了 |
| `similar` | `EmbeddingFilter` + `FiltrationDispatcher` | 现在是写死的两级（cluster→embedding），新设计里是脚本里的两行 |
| `&` `\|` `-` | surface executor 的四层集合运算 | 从写死的执行顺序变成普通表达式 |
| 证据 | `surface_evidence_hop_0.json`、`matched_subtokens` | 已有雏形，需要统一成 `ElementEvidence` 并贯穿所有算子 |
| QL 脚本 | 三份 `*_semql.json` + 三个 executor | 解释执行 → 生成代码 |

## 已有、可直接复用

这些不用重写：

- **倒排索引与缩写扩展**（`indexing/invert_index.py`、`expansion/`、`tokenizer/`）——
  `match` 的底座，且刚重构过（`CodeTokenizer`、`AbbreviationGenerator` 已收成类）
- **代码图库**（`codegraph.sqlite`：1718 符号、677 调用边、159 实现关系、
  2107 文件依赖）——`hop` 的底座
- **LLM judge**（`llm_judge_filter.py`）——`intent` 的实现
- **Embedding 通道**（ICF / semantic / hybrid）——`similar` 的实现
- **SemCon 抽取**（`llm_semCon_extractor.py`）——编译期第一步

## 需要改的

### 1. `hop` 要保留路径（最大的一处）

`RelationGraphStore.reachable()` 现在是 BFS 求可达集：

```python
def reachable(self, start_ids, direction, max_depth, ...) -> Set[int]:
    frontier = ...
    visited = set(frontier)
    result: Set[int] = set()          # ← 只留终点
```

`hop` 需要路径。改法是遍历时记前驱链，或者改成双向 BFS 后回溯。
**这会显著增加内存**，所以 `max_paths` 的默认值和截断日志是必须的，
不是可选优化。

### 2. 图只有 `calls` 边

```
code_edges          677 行，kind 全是 'calls'
code_implementations 159 行   ← 独立的表
code_dependencies   2107 行   ← 文件级，独立的表
```

`hop(edge="implements")` 和 `hop(edge="imports")` 现在得跨表查。
建议在 QL 层统一成一个边视图，把 `kind` 作为查询条件——
否则每加一种边就要改 `hop` 的实现。

`dataflow` 边（def-use）目前完全没有，`dataflow` 算子做不了，
要先扩索引（见 [08](08-open-questions.md)）。

### 3. 关键词派生要加 `derived` 这一类

当前 `Term.source` 只有 `keyword | synonym`。要加 `derived`（语义联想），
并给每个词带 `weight` 和 `reason`（[04](04-query-unit.md)）。

这是**改 prompt 加改 schema**，不动执行层，成本低但收益大——
`io performance on disk` 这类泛词查询的召回主要靠它。

### 4. 证据要统一

现在证据散在几处，形态不一：`matched_subtokens`（surface）、
`_embedding_score` / `_cluster_tier`（直接挂在候选 dict 上）、
judge 的 `reason`。

新设计要求所有算子往同一个 `ElementEvidence` 里**追加**。
顺带能解决一个现存问题：现在有些算子会往候选 dict 上原地挂字段，
这是 [ARCHITECTURE.md](../../ARCHITECTURE.md) 明确禁止的原地修改。

### 5. 产物路径知识要收口

`hop` 要读 codegraph，`match` 要读倒排索引。这些资源应当在 `Query`
构造时注入。当前虽然已经把 relation 那条链改成了构造函数注入
（`RelationGraphStore` 从外面传进来），但别处还有从候选文件路径反推
产物目录的写法（`_project_output_dir_from_candidate_path`）——
那是产物布局知识渗进了业务层，新设计里不应保留。

## 迁移路径（建议）

**不要一次性替换。** 三个阶段，每个阶段都能独立验证：

### 阶段 A：QL 层落地，不动现有管线

实现 `codesense/ql/`：数据模型 + 算子，底层调现有的 matcher / graph store /
filter。**手写**几段 QL 脚本，验证能不能表达真实查询。

验证方式：拿 `output/<project>/query_1/` 那条真实查询，
手写等价的 QL 脚本，结果应与 `filtered_by_relation.json` 一致。
这是现成的 golden（`tests/integration/test_golden_relation_filter.py` 已经在用）。

### 阶段 B：编译器

SemCon → QL 脚本。此时两条路并存：老管线仍可跑，新脚本作为对照。
在同一批查询上比对两者的结果差异，差异本身就是最好的评测材料。

### 阶段 C：切换

新路径的召回与精度不差于老路径后，把老 executor 降级为兼容层，
再逐步删除。

**每个阶段结束都应该能回答「比老的好在哪、差在哪」**，
而不是做完才发现方向不对。这需要先有评测——见
[evaluation/README.md](../../evaluation/README.md)，那个目录目前还是空骨架。

下一篇：[08 待定问题](08-open-questions.md)。
