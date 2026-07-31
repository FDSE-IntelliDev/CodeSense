# 10 图基座：用 CodeQL 构建边

[05](05-operators.md) 定义了 `hop` 的语义，[09](09-grounding.md) 讲了词法基座。
这一章讲图基座：**边从哪来，`hop` 怎么在上面跑。**

---

## 一、现状：图跑不动

先看实测（`output/youlai-boot-master/codegraph.sqlite`）：

| | |
|---|---|
| `code_edges` | **677** |
| `unresolved_calls` | **3186** |
| **调用解析率** | **677 / 3863 = 18%** |
| 出现在任何边上的符号 | 621 / 1718 = **36%** |
| **图上的孤点** | **1097 个符号（64%）** |
| 平均度数 | **0.8** |

**64% 的符号在图上是孤点，`hop` 永远碰不到它们。**
这不是算子设计问题——基座就是断的，再好的算子也救不回来。

而且 677 条边的 `provenance` **全部**是 `java_lsp_call_hierarchy`：
`codesense/codeql/` 下的 `.ql` 查询**从未真正跑过**，
现有图完全来自 LSP。

### LSP 漏掉了什么

看 `unresolved_calls` 的实际内容，模式很清楚：

```
FileController.uploadFile      → uploadFile              接口分派
FileController.deleteFile      → judge                   静态工具方法
LocalFileService.uploadFile    → getOriginalFilename     外部库调用
YouLaiBootApplication.main     → run                     外部框架调用
```

三类：**接口/虚方法分派、静态工具方法、外部库调用**。
LSP 的 call hierarchy 对这三类都不可靠——它是为 IDE 的「跳转到定义」设计的，
不是为全量图抽取设计的。

---

## 二、现有 CodeQL 查询：有什么、缺什么

`codesense/codeql/queries/java/` 已经有 `Symbols.ql`、`Calls.ql`、
`Dependencies.ql`、`Implementations.ql` 和 `CodeSearchModel.qll`。

**`Implementations.ql` 是对的**——已经用了 `getAnOverride()` 和
`extendsOrImplements()`，产出 159 条关系。

**`Calls.ql` 有一处关键缺陷**：

```ql
callee = call.getCallee()          // ← 静态被调方，即声明的那个方法
```

对 `FileService.uploadFile()` 这样的接口调用，它解析到**接口方法**，
不是各个实现。这正是 `unresolved_calls` 里那批接口分派的来源。

改成同时产出两类边：

```ql
// 静态边：调用点声明的目标
callee = call.getCallee()                                    kind = "calls"

// 虚分派边：所有可能的实现
callee = call.getCallee().(Method).getAPossibleImplementation()
                                                             kind = "calls_virtual"
```

**分成两种 kind 而不是合并**，因为它们的置信度不同——
虚分派是「可能调到」，静态边是「一定调到」。`hop` 的 `min_confidence` 要能区分。

**完全缺失的三项**：

| 缺什么 | CodeQL 里怎么取 | 对应的边 |
|---|---|---|
| 字段读写 | `FieldRead` / `FieldWrite` | `reads` / `writes` |
| 注解 | `Annotatable.getAnAnnotation()` | `annotated_by`（[09](09-grounding.md) 第八节） |
| 数据流 | `DataFlow::localFlowStep` | `flows_to` |

`CodeSearchModel.qll` 里已经定义了 `projectField` 但没有查询用它——
字段那部分是开了个头没写完。

---

## 三、架构：CodeQL 是索引构建器，不是查询运行时

这是本章最重要的一个判断。

**不要在查询时调 CodeQL。** 理由是延迟量级完全不匹配：

| | 量级 |
|---|---|
| CodeQL 建库 | 分钟～小时 |
| CodeQL 单次查询 | 秒～分钟 |
| **查询时延迟预算** | **秒级（全流程）** |

而且 QL 脚本要在 Python 里**运行时组合**算子（[06](06-script-and-execution.md)），
CodeQL 的声明式查询没法这样组合。

所以：

```
离线（建索引时）                          查询时
─────────────────────────            ──────────────────
CodeQL 建库                            读物化好的边
  ↓                                      ↓
跑 .ql 查询抽边                         保留路径的图遍历
  ↓                                      ↓
物化进 codegraph.sqlite                 产出 Frag
```

**CodeQL 的价值在于它能抽出 LSP 抽不出的边，不在于它的查询能力被暴露到运行时。**

---

## 四、要物化哪些边

判断标准是**边数是否有界**：

| kind | 来源 | 规模 | 物化 |
|---|---|---|---|
| `calls` | `Call.getCallee()` | O(调用点) | ✅ |
| `calls_virtual` | `getAPossibleImplementation()` | O(调用点 × 实现数) | ✅ |
| `contains` | `code_symbols.container` | O(符号) | ✅ **数据已在库里** |
| `implements` | `getAnOverride()` / `extendsOrImplements()` | O(类型) | ✅ **已有 159 条** |
| `imports` | `code_dependencies` | O(文件×依赖) | ✅ **已有 2107 条** |
| `annotated_by` | `getAnAnnotation()` | O(注解使用) | ✅ |
| `reads` / `writes` | `FieldRead` / `FieldWrite` | O(字段访问) | ✅ |
| `throws` / `instantiates` | `getAThrownExceptionType()` / `ClassInstanceExpr` | O(语句) | ✅ |
| **`flows_to`（方法内）** | `DataFlow::localFlowStep` | O(方法内语句) | ✅ |
| **`flows_to`（跨方法全局）** | `TaintTracking` | **O(source × sink)，可能爆炸** | ❌ 见第六节 |

### 三条边现在就能免费拿到

数据已经在 `codegraph.sqlite` 里，只是没建成边视图：

```
contains      1455   ← code_symbols.container 字段
implements     159   ← code_implementations 表
imports       2107   ← code_dependencies 表
```

**这一步不需要 CodeQL，也不需要重新解析。** 而它的效果是决定性的：

| 阶段 | 边数 | 平均度 | 连通符号 |
|---|---|---|---|
| 现状 | 677 | 0.8 | 621（36%） |
| **+ contains/implements** | **2291** | **2.7** | **1718（100%）** |
| + 调用解析修到 ~90% | 5094 | 5.9 | 1718（100%） |

**只把 `contains` 物化出来，孤点问题就没了**——因为每个方法都属于某个类。
这应当是实现的第一步。

### 边视图统一

[07](07-mapping-to-current.md) 指出边散在三张表里（`code_edges` /
`code_implementations` / `code_dependencies`），`hop(edge=[...])` 要跨表查。

**统一成一张边表**，`kind` 作为普通列：

```sql
CREATE TABLE edges (
    edge_id     INTEGER PRIMARY KEY,
    src         INTEGER NOT NULL,      -- symbol_id
    dst         INTEGER NOT NULL,
    kind        TEXT    NOT NULL,      -- calls | contains | implements | ...
    confidence  REAL    NOT NULL,
    provenance  TEXT    NOT NULL,      -- codeql_calls | lsp | derived_container
    attrs       TEXT                   -- JSON：call_line、annotation args 等
);
CREATE INDEX idx_edges_src ON edges(src, kind);
CREATE INDEX idx_edges_dst ON edges(dst, kind);
```

两个索引都要——`dir="backward"` 走 `dst`。

### 置信度分层

`min_confidence` 要有意义，`provenance` 就必须映射到可比的置信度：

| provenance | confidence | 含义 |
|---|---|---|
| `derived_container` | 1.0 | 从符号表推出，不会错 |
| `codeql_implements` | 1.0 | 类型系统事实 |
| `codeql_calls` | 0.95 | 静态解析的调用 |
| `codeql_calls_virtual` | 0.6 | 可能的实现，未必真调到 |
| `lsp_call_hierarchy` | 0.8 | 现有边，保留但降级 |
| `codeql_taint` | 0.5 | 数据流，可能有假阳 |

数值是初值，与 [09](09-grounding.md) 的权重一样按评测集调。

---

## 五、`hop` 的实现：保留路径的遍历

[07](07-mapping-to-current.md) 说这是**最大的一处改动**。现在是：

```python
def reachable(self, start_ids, direction, max_depth, ...) -> Set[int]:
    result: Set[int] = set()          # ← 只留终点，路径在遍历时丢弃
```

而 `hop` 要返回**路径**（[03](03-data-model.md)：Frag 含节点、边和路径见证）。

### 反向测距 + 正向枚举（已实现）

真正的双向路径枚举要在相遇点做两半路径的笛卡尔积，容易写错。
实际采用的是**同等效果但简单得多**的做法：

```python
# 1. 从 dst 反向 BFS，只算「到 dst 的最短跳数」，不留路径 —— 很便宜
to_dst = distances(dst, flip(direction), hi)

# 2. 从 src 正向 DFS 枚举路径，用 to_dst 剪枝
#    depth + to_dst[node] > hi  ⇒  再走也到不了，整枝剪掉
```

因为 `to_dst` 是**最短**距离，这个剪枝是**可采纳的**——
不会砍掉任何真实存在的合法路径。而剪枝效果与双向相当：
搜索空间同样被压到「两端都够得着」的那部分。

按实测修复后平均度 4.4，`hops=(1,3)` 时单向盲搜约 109 条/起点，
加上反向测距剪枝后只展开真正通向 dst 的分支。

用显式栈的 DFS 而不是递归——路径可以很长，递归会撞 Python 的栈深度限制。

### 路径爆炸的四道闸

1. **`max_paths` 必须有默认值，且截断必须 log。**
   静默截断会让人以为「结果就这么多」——[05](05-operators.md) 已经写了，
   这里给出它为什么是必需项而非优化项。

2. **Hub 节点单独限流。** 当前最大度数 42，修复调用解析后会更高。
   工具类（`Result.success`、`Assert.judge`）会被所有人调用，
   经过它们的路径几乎没有信息量。按度数设 per-node fanout 上限，
   或直接把超高度数节点加进默认 `avoid`。

3. **`hops` 的上界要小。** 默认 `(1, 3)`。跳数越多，「有关系」这个结论越弱——
   任意两个符号在 5 跳内多半都能连上，那样的结果没有意义。

4. **`avoid` 应有默认值。** 测试代码、生成代码、`toString`/`equals`/`hashCode`
   这类样板方法默认排除。

### 虚分派：两种做法，选加边

接口方法与其实现应视为同一个查询目标。两种实现方式：

- **等价合并**（现有 `equivalent_symbol_ids`）：遍历时把等价符号视为一个节点
- **显式加边**（`calls_virtual`）：CodeQL 直接产出到实现的边

**选后者**。等价合并是遍历时的特殊逻辑，每个算子都要重复处理；
显式加边之后，虚分派就是一条普通的边，`hop` 不需要知道它特殊，
而且**置信度可以单独给**（0.6），等价合并做不到这一点。

---

## 六、`flows_to`：唯一不能全量物化的边

[07](07-mapping-to-current.md) 说它「最贵但最不可替代」——
「这个参数的值从哪来」「哪些路径把用户输入带到 SQL 拼接处」，
没有它完全答不了。

但它也是唯一可能爆炸的：跨方法污点分析的边数是 O(source × sink)，
而 source 和 sink 都可能是全量级。

**分两层处理**：

| | 物化 | 理由 |
|---|---|---|
| **方法内数据流** | ✅ 全量物化 | `DataFlow::localFlowStep`，边数 O(方法内语句)，有界 |
| **跨方法污点** | ❌ 按需生成查询 | 需要指定 source/sink，本质上是一次新的 CodeQL 查询 |

跨方法的情况下，QL 脚本里的 `hop(a, b, edge="flows_to")` 编译成
**一次离线的 CodeQL 查询**，而不是查内存里的图：

```python
# 脚本里写的
paths = hop(user_input, sql_exec, edge="flows_to", len=(1, 10))

# 实际发生的：生成一个 @kind path-problem 的 .ql，跑一次，结果转成 Frag
```

这打破了「查询时不调 CodeQL」的原则，所以必须是**显式的、慢的、可选的**：

- 在脚本里明确标注（比如 `edge="flows_to:global"`），不能隐式触发
- 有独立的超时和缓存
- 结果按 (source_frag, sink_frag) 缓存复用

**优先级**：方法内数据流性价比高得多，先做；跨方法留到[07](07-mapping-to-current.md)
的阶段 C。

---

## 七、代价与增量

### 建库代价

CodeQL 建库要编译整个项目，这是最大的一次性成本：

| | 量级 |
|---|---|
| youlai-boot（254 文件） | 分钟级 |
| 中型项目 | 十分钟～小时 |
| Linux kernel 量级 | 小时级 |

**所以建库必须是离线的、可缓存的、按 commit 复用的。**
不能放进任何交互路径。

### 增量

CodeQL 本身对增量支持有限（通常是全量重建）。缓解办法：

- 按 commit hash 缓存整个数据库
- 边的物化产物（`edges` 表）与数据库分开存，
  这样调整 `.ql` 查询不必重建数据库
- `contains` / `imports` 这类不依赖 CodeQL 的边独立重建（秒级）

### 落地顺序

按「收益 / 成本」排，前两步都不需要 CodeQL：

1. **物化 `contains`**（数据已在库里）—— 孤点从 64% 降到 0，**几分钟的工作量**
2. **统一边视图** —— `implements` + `imports` 并进来，边数 677 → 2291
3. **遍历改成保留路径** —— `hop` 才真正可用
4. **跑通 CodeQL 抽 `calls` + `calls_virtual`** —— 解析率 18% → ~90%
5. **加 `annotated_by`、`reads`/`writes`** —— 解锁 [09](09-grounding.md) 第八节
6. **方法内 `flows_to`**
7. **跨方法污点（按需）**

**前三步就能让 `hop` 从「跑不动」变成「可用」，而且完全不依赖 CodeQL。**
这一点值得强调：CodeQL 是提升上限的，不是解除阻塞的——
当前的阻塞点是边没物化，不是边抽不出来。

---

## 小结

1. **图现在跑不动**：调用解析率 18%，64% 的符号是孤点，
   而 `codesense/codeql/` 下的查询从未跑过。

2. **CodeQL 是索引构建器，不是查询运行时。** 建库分钟到小时、
   单查询秒到分钟，与秒级的查询预算差三个量级。
   它的价值是抽出 LSP 抽不到的边（虚分派、字段读写、数据流）。

3. **先物化 `contains`。** 数据已经在 `code_symbols.container` 里，
   建成边之后孤点直接归零——这是整章里性价比最高的一步，且不需要 CodeQL。

4. **虚分派用加边而不是等价合并**，这样它是普通的边、能单独给置信度（0.6），
   算子不必知道它特殊。

5. **`flows_to` 分两层**：方法内全量物化，跨方法按需生成 CodeQL 查询，
   且必须在脚本里显式标注——它是唯一打破「查询时不调 CodeQL」的例外。

6. **`hop` 的路径爆炸要四道闸**：`max_paths` 默认值 + 截断日志、
   hub 节点限流、`hops` 上界小、`avoid` 有默认值。
   实测平均度修复后约 4.4，`hops=(1,5)` 单向就是 2133 条路径/起点。
