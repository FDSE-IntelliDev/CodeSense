# CodeGraph 搜索策略设计

## 概述

CodeGraph 实现了一套**多级降级 + 多信号重排序**的符号搜索系统，基于 SQLite FTS5 全文搜索引擎，兼顾速度与召回率。

## 架构总览

```
用户查询（如 "authService"）
        │
        ▼
┌─── 查询解析 ───┐
│ 提取结构化过滤器 │  kind:function  lang:typescript  path:src/
│ 剩余文本 → FTS  │  "authService"
└────────────────┘
        │
        ▼
┌─────────────────────────────────────────┐
│         三级搜索降级策略                   │
│                                         │
│  Level 1: FTS5 前缀匹配（毫秒级）         │
│      ↓ 无结果                            │
│  Level 2: LIKE 子串匹配（CamelCase 友好） │
│      ↓ 无结果                            │
│  Level 3: Levenshtein 模糊匹配（容错）    │
└─────────────────────────────────────────┘
        │
        ▼
┌─── 多信号重排序 ───┐
│ BM25 基础分          │
│ + 类型加分(kindBonus) │
│ + 路径相关性          │
│ + 名称匹配精度        │
└────────────────────┘
        │
        ▼
┌─── 硬过滤 ───┐
│ path: 过滤    │
│ name: 过滤    │
└──────────────┘
        │
        ▼
      最终结果
```

## 第一步：查询解析

将用户的自由文本查询解析为结构化部分 + 自由文本部分：

```
输入: "kind:function lang:ts path:src/ authenticate"

解析结果:
  - kinds: ["function"]
  - languages: ["typescript"]
  - pathFilters: ["src/"]
  - text: "authenticate"       ← 驱动 FTS/LIKE
```

结构化过滤器在搜索和排序之后作为硬约束应用，确保 FTS 阶段有足够候选集供筛选。

## 第二步：三级搜索降级

### Level 1：FTS5 前缀匹配

**适用场景**：大多数正常查询，毫秒级响应。

```sql
SELECT nodes.*, bm25(nodes_fts, 0, 20, 5, 1, 2) as score
FROM nodes_fts
JOIN nodes ON nodes_fts.id = nodes.id
WHERE nodes_fts MATCH '"auth"*'
ORDER BY score
LIMIT 500
```

**查询预处理**：
1. `::` 替换为空格（处理 Rust/C++ 限定符）
2. 去除 FTS5 特殊字符 `'"*():^`
3. 过滤布尔操作符（`AND`/`OR`/`NOT`/`NEAR`，防注入）
4. 每个词加前缀通配 `"term"*`，多词用 `OR` 连接

**BM25 列权重设计**：

| 列 | 权重 | 理由 |
|----|------|------|
| id | 0 | 不参与评分 |
| name | 20 | 符号名精确命中最有价值 |
| qualified_name | 5 | 全限定名次之 |
| docstring | 1 | 文档匹配权重低（避免噪声） |
| signature | 2 | 函数签名有参考价值 |

**为什么过量取**：取 `limit × 5` 的候选，因为后续的多信号重排序可能提升 BM25 低排名的结果。

### Level 2：LIKE 子串匹配

**适用场景**：FTS 返回空 && 查询长度 ≥ 2 字符。

```sql
SELECT nodes.*,
  CASE
    WHEN name = 'signIn'       THEN 1.0   -- 精确匹配
    WHEN name LIKE 'signIn%'   THEN 0.9   -- 前缀匹配
    WHEN name LIKE '%signIn%'  THEN 0.8   -- 包含匹配
    WHEN qualified_name LIKE '%signIn%' THEN 0.7
    ELSE 0.5
  END as score
FROM nodes
WHERE name LIKE '%signIn%' OR qualified_name LIKE '%signIn%'
```

**解决的问题**：FTS5 对 CamelCase 词不友好。`TransportSearchAction` 在 FTS 中是一个整体 token，查 `"Search"*` 匹配不到，但 `LIKE '%Search%'` 可以。

### Level 3：Levenshtein 模糊匹配

**适用场景**：FTS + LIKE 均返回空 && 查询长度 ≥ 3 字符。

```typescript
// 遍历所有已知符号名，保留编辑距离在阈值内的
function searchNodesFuzzy(query, options) {
    // 对所有 node name 计算 boundedEditDistance
    // 阈值 = min(floor(query.length / 3), 3)
    // 即：3-5 字符容错 1，6-8 字符容错 2，9+ 字符容错 3
}
```

**解决的问题**：拼写错误（如 `auhtenticate` → `authenticate`）。代价较高（需扫描所有符号名），所以放在最后。

## 第三步：精确名称补充

BM25 可能将短精确名称（如 `getBean`）埋在大量复合名称（如 `getBeanDescriptor`、`getBeanFactory`...）之下。补充一次精确匹配：

```sql
SELECT * FROM nodes WHERE name = ? COLLATE NOCASE LIMIT 20
```

使用 FTS 候选中的最高 BM25 分作为基础分，确保后续的 `nameMatchBonus`（精确匹配 +30 vs 前缀 +20）能将其推到顶部。

## 第四步：多信号重排序

```typescript
finalScore = bm25Score
  + kindBonus(node.kind)           // 类型加分：function/class > variable/import
  + scorePathRelevance(filePath, query)  // 路径中包含查询词加分
  + nameMatchBonus(name, query)    // 精确=30, 前缀=20, 包含=10
```

排序后截取 `limit` 条结果。

## 第五步：硬过滤

对重排序后的结果应用用户指定的 `path:` 和 `name:` 过滤器（大小写不敏感子串匹配）。

放在最后的原因：确保 FTS 阶段获取了足够大的候选池，避免过早截断。

## 性能特征

| 规模 | FTS5 延迟 | LIKE 延迟 | Fuzzy 延迟 |
|------|----------|----------|-----------|
| 1k 符号 | < 1ms | < 5ms | ~10ms |
| 10k 符号 | < 5ms | ~20ms | ~50ms |
| 100k 符号 | < 10ms | ~100ms | ~500ms |

实际场景中 90%+ 查询命中 Level 1，降级情况较少。

## FTS5 索引维护

通过 SQLite 触发器自动同步，零维护成本：

```sql
-- 插入自动索引
TRIGGER nodes_ai AFTER INSERT ON nodes → INSERT INTO nodes_fts(...)

-- 删除自动清理
TRIGGER nodes_ad AFTER DELETE ON nodes → INSERT INTO nodes_fts(nodes_fts, ...) VALUES('delete', ...)

-- 更新 = 删除旧 + 插入新
TRIGGER nodes_au AFTER UPDATE ON nodes → delete old + insert new
```

---

# 函数调用依赖解析

## 概述

CodeGraph 通过**三阶段流水线**建立函数间的调用关系：提取 → 解析 → 查询。核心思想是先用 Tree-sitter 从 AST 中提取"谁调用了谁（名称）"，再通过多策略并行匹配将名称绑定到具体的图节点，最终形成可遍历的调用图。

## 总体流程

```
源代码文件
    │
    │ Tree-sitter AST 解析
    ▼
┌──────────────────────────────────────┐
│ 阶段1：提取 (Extraction)              │
│ 发现 call_expression 节点             │
│ 产出: unresolved_ref                  │
│   {from: 函数A, name: "foo", kind: "calls"} │
└──────────────────────────────────────┘
    │
    │ 存入 unresolved_refs 表
    ▼
┌──────────────────────────────────────┐
│ 阶段2：解析 (Resolution)              │
│ 将名称字符串绑定到具体 Node           │
│ 多策略并行候选 + 置信度竞争            │
└──────────────────────────────────────┘
    │
    │ 写入 edges 表
    ▼
┌──────────────────────────────────────┐
│ 阶段3：图查询 (Traversal)             │
│ getCallers / getCallees              │
│ getCallGraph / getImpactRadius       │
└──────────────────────────────────────┘
```

## 阶段 1：提取 — 从 AST 发现调用关系

### 工作原理

Tree-sitter 将源代码解析为 AST，提取器遍历 AST 时遇到各语言配置的 `callTypes` 节点（如 TypeScript 的 `call_expression`），就提取被调用者的**文本名称**。

```typescript
// 各语言通过 callTypes 配置哪些 AST 节点代表调用
export const typescriptExtractor: LanguageExtractor = {
  callTypes: ['call_expression'],  // TS/JS
};
export const pythonExtractor: LanguageExtractor = {
  callTypes: ['call'],             // Python
};
export const goExtractor: LanguageExtractor = {
  callTypes: ['call_expression'],  // Go
};
```

### 调用名称提取逻辑

```typescript
// 遇到 call_expression 时的处理
// 1. 简单调用: foo() → calleeName = "foo"
// 2. 方法调用: obj.method() → calleeName = "obj.method"
//    （跳过 self/this/cls 等无意义接收者）
// 3. 作用域调用: Module::function() → calleeName = "Module::function"

if (calleeName) {
  this.unresolvedReferences.push({
    fromNodeId: callerId,       // 当前所在函数/方法的 Node ID
    referenceName: calleeName,  // 被调用者的文本名称
    referenceKind: 'calls',     // 关系类型
    line: node.startPosition.row + 1,
    column: node.startPosition.column,
  });
}
```

### 除 `calls` 外的其他引用类型

| referenceKind | 触发场景 |
|---------------|---------|
| `calls` | `foo()`、`obj.method()` |
| `imports` | `import { foo } from './bar'` |
| `extends` | `class A extends B` |
| `implements` | `class A implements I` |
| `instantiates` | `new Foo()` |
| `type_of` | `x: SomeType` |
| `decorates` | `@Decorator` |
| `references` | 其他通用引用 |

### 关键限制

此阶段**只知道名称字符串**，不知道被调用者对应图中哪个 Node。所有引用存入 `unresolved_refs` 表，等待下一阶段解析。

## 阶段 2：解析 — 将名称绑定到目标 Node

### 策略链架构

```
unresolved_ref {from: A, name: "foo", kind: "calls"}
        │
        ▼
┌─── 前置过滤 ───┐
│ 内置符号? (console/print/len) → 跳过    │
│ 全局无匹配 + 无 import + 框架不认领? → 跳过 │
└────────────────┘
        │
        ▼
┌─── JVM 全限定名 ───┐  (仅 Java/Kotlin/Scala)
│ import com.example.Bar → 直接按 qualifiedName 匹配 │
│ 命中 → 直接返回                                    │
└───────────────────┘
        │
        ▼ (并行收集候选，按置信度竞争)
┌─────────────────────────────────────────────────┐
│                                                 │
│  策略1: 框架特定解析器                             │
│    置信度 ≥ 0.9 → 短路返回                        │
│    否则 → 加入候选池                              │
│                                                 │
│  策略2: Import 路径解析                           │
│    置信度 ≥ 0.9 → 短路返回                        │
│    否则 → 加入候选池                              │
│                                                 │
│  策略3: 名称匹配（精确 + 模糊）                    │
│    → 加入候选池                                   │
│                                                 │
└─────────────────────────────────────────────────┘
        │
        ▼
  取候选池中置信度最高者 → 写入 edges 表
```

**重要**：策略 1/2/3 不是串行降级，而是**并行收集候选**。只有高置信度（≥ 0.9）才短路，否则所有策略的结果都进入候选池，最终比较置信度选最优。

### 策略 1：框架特定解析器

利用框架的结构约定直接定位目标，无需通用匹配。

**支持的框架**（20+）：

| 框架 | 解析能力 |
|------|---------|
| React / React Native | hooks 调用、组件引用、Native Bridge 模块 |
| NestJS | 控制器路由、依赖注入、模块注册 |
| Express | 路由 → handler 映射 |
| Vue / Svelte | 组件引用、事件绑定 |
| Laravel / Drupal | 路由、服务容器 |
| Spring (Java) | 注解驱动的 Bean 注入、路由映射 |
| Go (gin/echo) | 路由注册 |
| Swift / ObjC | 跨语言桥接（名称自动转换） |
| Rust / Cargo workspace | 模块路径解析 |
| Expo Modules | TS 接口 ↔ Native 实现 |

**示例 — Swift ↔ ObjC 桥接**：

```
Swift:  func play(song: String)
ObjC:   [obj playWithSong:@"Hello"]

框架解析器知道 Apple 的命名转换规则:
  playWithSong: → Swift 中的 play(song:) → 匹配到 play 方法的 Node
```

**示例 — NestJS 路由**：

```typescript
@Controller('users')
class UsersController {
  @Get(':id')
  findOne() {}   // 框架解析器知道这对应路由 /users/:id
}
```

### 策略 2：Import 路径解析

沿文件的 import/require 语句追踪到目标文件中导出的符号。

```
文件 A: import { authenticate } from './auth'
            │
            ▼ 解析 './auth' → 找到 auth.ts
            ▼ 在 auth.ts 的导出中查找 'authenticate'
            ▼ 找到 → 置信度 0.95
```

**支持的特性**：
- TS/JS path aliases（`@/utils` → `src/utils`）
- 桶文件 re-export（`export { foo } from './inner'`）
- Go module 路径（`go.mod` 中的模块前缀）
- C++ include 目录
- Python 包相对导入

### 策略 3：名称匹配

在**整个索引的 nodes 表**中按名称搜索（不限于 import 指向的文件）。

```typescript
function matchReference(ref, context): ResolvedRef | null {
  // 1. 精确名称匹配：name === ref.referenceName
  //    → 置信度基础 0.85
  //    → 同文件/同类 +0.05，跨语言 -0.1
  
  // 2. 如果精确匹配有多个候选：
  //    - 同文件优先
  //    - 同 package/module 优先
  //    - 可见性匹配（exported > private）
  
  // 3. 文件路径匹配（Liquid/模板引用）：
  //    "snippets/drawer-menu.liquid" → 匹配文件节点
}
```

**这是最后的兜底**——当没有框架规则也没有 import 信息时，靠全局名称碰撞来建立关系。

### 置信度评分机制

| 来源 | 典型置信度 | 说明 |
|------|-----------|------|
| 框架解析器 | 0.90 - 0.99 | 框架约定精确，几乎不会错 |
| Import 路径解析 | 0.90 - 0.95 | import 明确指定了来源 |
| 精确名称（唯一） | 0.85 | 全局只有一个同名符号 |
| 精确名称（多候选） | 0.70 - 0.80 | 需要启发式消歧 |
| 文件路径匹配 | 0.70 - 0.95 | 取决于路径精确程度 |

### 解析后的边类型自动提升

解析时还会根据目标 Node 的类型**自动修正关系类型**：

```typescript
// calls → instantiates：如果目标是 class/struct
// Python: Foo() 提取时是 "calls"，解析发现 Foo 是 class → 改为 "instantiates"
if (kind === 'calls') {
  const targetNode = getNodeById(ref.targetNodeId);
  if (targetNode.kind === 'class' || targetNode.kind === 'struct') {
    kind = 'instantiates';
  }
}

// extends → implements：如果目标是 interface/protocol
if (kind === 'extends') {
  const targetNode = getNodeById(ref.targetNodeId);
  if (targetNode.kind === 'interface' || targetNode.kind === 'protocol') {
    kind = 'implements';
  }
}
```

## 阶段 2.5：回调边合成 (Callback Synthesizer)

处理**静态 AST 分析无法直接看到**的动态调度模式。

### 问题

```typescript
// 注册点
scene.onUpdate(this.triggerRender);

// 分发点（另一个方法）
triggerUpdate() { for (cb of this.callbacks) cb(); }

// 静态分析只能看到:
//   - 某处调用了 scene.onUpdate
//   - triggerUpdate 内部调用了 cb（匿名）
// 看不到: triggerUpdate → triggerRender 的间接调用关系
```

### 解决方案：模式匹配合成边

**模式 1 — 字段型观察者**：

```
注册方法名匹配: /^(on[A-Z]\w*|subscribe|addListener|register|watch|listen)$/
分发方法名匹配: /(emit|trigger|notify|dispatch|fire|publish|flush)/

通过共享字段名配对:
  onUpdate(cb) { this.callbacks.add(cb) }  ← 字段 = callbacks
  triggerUpdate() { for (cb of this.callbacks) cb() }  ← 字段 = callbacks
  
  同一字段 → 合成边: triggerUpdate → 被注册的回调
```

**模式 2 — EventEmitter 字符串键**：

```
this.on('mount', function onmount() {...})   ← 事件名 = 'mount'
fn.emit('mount', this)                       ← 事件名 = 'mount'

同一事件名 → 合成边: (含 emit 的方法) → onmount
```

所有合成边标记 `provenance: 'heuristic'`，与静态确定的边区分。

## 阶段 3：图查询 — 利用调用依赖

边建立完成后，提供多种遍历查询能力。

### 查找调用者 (getCallers)

```typescript
getCallers(nodeId: string, maxDepth: number = 1) {
  // 查找 incoming edges where kind in ('calls', 'references', 'imports')
  // 递归向上追溯 maxDepth 层
  // 批量获取 caller nodes（避免 N+1 查询）
}
```

### 查找被调用者 (getCallees)

```typescript
getCallees(nodeId: string, maxDepth: number = 1) {
  // 查找 outgoing edges where kind in ('calls', 'references', 'imports')
  // 递归向下追溯 maxDepth 层
}
```

### 双向调用图 (getCallGraph)

```typescript
getCallGraph(nodeId: string, depth: number = 2): Subgraph {
  // 以目标函数为中心，同时向上（callers）和向下（callees）展开
  const callers = this.getCallers(nodeId, depth);
  const callees = this.getCallees(nodeId, depth);
  // 返回完整子图（节点 + 边）
}
```

### 影响分析 (getImpactRadius)

"如果我改了这个函数/类，哪些代码可能受影响？"

```typescript
getImpactRadius(nodeId: string, maxDepth: number = 3): Subgraph {
  // 沿 incoming edges 反向遍历（谁依赖我 → 谁依赖他们 → ...）
  
  // 特殊处理容器节点:
  // class 改了 → 展开其所有 methods → 各 method 的调用者都受影响
  if (containerKinds.has(focalNode.kind)) {
    for (const child of getChildren(nodeId)) {
      getImpactRecursive(child.id, ...);
    }
  }
}
```

### BFS 图遍历 (traverseBFS)

通用图遍历，支持方向/深度/边类型过滤：

```typescript
traverseBFS(startId, options) {
  // 边优先级排序: contains > calls > 其他
  // 确保先发现内部结构，再扩展到外部引用
  adjacentEdges.sort((a, b) => {
    const priority = (e) => e.kind === 'contains' ? 0 : e.kind === 'calls' ? 1 : 2;
    return priority(a) - priority(b);
  });
}
```

## SQL 层面的调用关系查询

```sql
-- 查找函数 foo 的所有直接调用者
SELECT nodes.* FROM edges
  JOIN nodes ON edges.source = nodes.id
  WHERE edges.target = '<foo_node_id>'
    AND edges.kind = 'calls';

-- 查找函数 foo 调用的所有函数
SELECT nodes.* FROM edges
  JOIN nodes ON edges.target = nodes.id
  WHERE edges.source = '<foo_node_id>'
    AND edges.kind = 'calls';

-- 联合索引保证 O(log n) 查询性能
CREATE INDEX idx_edges_source_kind ON edges(source, kind);
CREATE INDEX idx_edges_target_kind ON edges(target, kind);
```

## 跨语言调用依赖

这里的"跨语言"特指**同一编译产物内**通过框架/编译器桥接机制互相引用的情况，不是 RPC/HTTP 级别的跨服务调用。

| 桥接类型 | 机制 | 示例 |
|---------|------|------|
| Swift ↔ ObjC | Apple 编译器自动名称转换 | `play(song:)` ↔ `playWithSong:` |
| React Native Bridge | 声明式模块注册 | JS `NativeModules.Camera` ↔ ObjC `RCT_EXPORT_MODULE(Camera)` |
| Expo Modules | TS 接口对应 Native 实现 | TS `Camera.takePicture()` ↔ Swift `func takePicture()` |

**不支持**的场景：Python 调 Java（通过 HTTP/gRPC/JNI）— 这类动态运行时交互超出静态图谱能力。

---

## 与 LSP 方案的对比

### LSP 方案原理

Language Server Protocol (LSP) 是 IDE 实现"跳转到定义"、"查找引用"等功能的底层协议。理论上通过 LSP 的 `callHierarchy/incomingCalls` 和 `callHierarchy/outgoingCalls` 方法，可以精确提取函数间的调用关系，且**不需要针对任何框架做特殊适配**——因为语言服务器（如 tsserver、gopls、rust-analyzer）内置了完整的语义分析能力。

```python
# LSP 方案伪代码
for symbol in all_symbols(project):
    callers = lsp.callHierarchy_incomingCalls(symbol)   # 精确的调用者
    callees = lsp.callHierarchy_outgoingCalls(symbol)   # 精确的被调用者
    graph.add_edges(...)
```

### 为什么选择 Tree-sitter + Resolution 而非 LSP

| 对比维度 | CodeGraph (Tree-sitter) | LSP 方案 |
|---------|------------------------|----------|
| **项目是否需要可编译** | ❌ 不需要，只要有源码文件 | ✅ 必须可编译（依赖已安装、配置正确） |
| **环境依赖** | 零依赖，自带 WASM 运行时 | 每种语言需要对应的工具链（JDK、Go、rustup 等） |
| **索引速度** | 秒级（Tree-sitter WASM 解析） | 分钟级（语言服务器首次索引大项目需要完整类型检查） |
| **内存开销** | 几十 MB SQLite 数据库 | 大项目 tsserver 占 2-4GB，rust-analyzer 占 1-3GB |
| **多语言支持** | 统一接口，20+ 种语言同一套 WASM | 每种语言独立的 LSP server，配置各不相同 |
| **批处理友好** | 一次性全量索引 | LSP 为交互式设计，批量请求慢 |
| **符号解析精确度** | 启发式，有误匹配可能（置信度 0.7-0.99） | 编译器级别，100% 精确 |
| **框架覆盖** | 需要手写框架解析器 | 语言服务器原生支持 |
| **类型推断** | 无 | 完整 |
| **部署方式** | `git clone` 后立即可用 | 需要项目完整环境就绪 |

### CodeGraph 方案的核心优势场景

**1. 项目不可编译时仍可工作**

这是最关键的差异。现实中很多场景项目无法立即编译：
- 刚 `git clone`，依赖还没安装（`node_modules` / `vendor` / `target` 不存在）
- CI 环境中只有源码，没有完整工具链
- 跨团队审查代码，不想配置完整开发环境
- 历史代码 / 归档项目，编译环境已不可用
- AI Agent 分析代码时，不想等待 `npm install` + 类型检查

CodeGraph 只需要源码文件即可构建完整的知识图谱，不依赖任何编译器或包管理器。

**2. 跨语言桥接的特殊处理**

LSP 的每种语言服务器是独立的进程，它们之间互不通信。一个 iOS 项目同时有 Swift 和 ObjC 代码：
- `sourcekit-lsp`（Swift）不知道 ObjC 那边定义了什么
- `clangd`（ObjC）不知道 Swift 那边导出了什么

CodeGraph 的框架解析器可以**跨语言建立边**：

```
Swift:  func play(song: String)     ← sourcekit-lsp 管这个
ObjC:   [obj playWithSong:@"Hello"] ← clangd 管这个

CodeGraph: 知道 Apple 的命名转换规则，直接建边
         play(song:) ←→ playWithSong:
```

同理 React Native Bridge（JS ↔ ObjC/Java）、Expo Modules（TS ↔ Native）都是跨 LSP 边界的场景。

**3. 统一的多语言图谱**

CodeGraph 将所有语言的符号存入同一个 SQLite 数据库，可以：
- 一次查询跨越多种语言的调用链
- 统一的 MCP 接口暴露给 AI Agent
- 全局影响分析不受语言边界限制

而 LSP 方案需要分别查询每种语言的服务器，再自行合并结果。

### LSP 方案更适合的场景

- 单一语言项目，且项目可正常编译
- 对精确度要求极高（不允许任何误匹配）
- 需要完整的类型信息（如重构时）
- 项目规模中小（LSP 启动和内存开销可接受）

### 总结

CodeGraph 的设计哲学是**"用精确度换取可用性"**——在项目可能不可编译、环境不完整、多语言混合的场景下，通过启发式方法提供"足够好"的调用关系，而非追求编译器级别的 100% 精确。这使得它可以作为 AI Agent 的通用代码理解层，在任何 `git clone` 之后立即开始工作。

---

## 设计权衡总结

### 搜索系统

| 决策 | 取舍 |
|------|------|
| FTS5 为主路径 | 速度优先，但牺牲 CamelCase 内部匹配 |
| LIKE 兜底 | 覆盖 CamelCase，但 O(n) 扫描 |
| Fuzzy 最后 | 容错拼写，但性能最差，仅在无结果时触发 |
| 过量取候选 (5×) | 允许重排序发现好结果，但内存占用略高 |
| BM25 name 权重 20 | 强调名称匹配，可能低估文档中的重要描述 |
| 硬过滤后置 | 保证候选池充足，但排序阶段处理更多数据 |

### 调用依赖解析

| 决策 | 取舍 |
|------|------|
| 两阶段分离（提取 vs 解析） | 支持全量/增量索引，但需要额外存储 unresolved_refs |
| 并行策略 + 置信度竞争 | 兼顾精度和召回，但可能有多策略重复计算 |
| 高置信度短路 (≥ 0.9) | 框架/import 精确时快速返回，不浪费后续策略 |
| 全局名称匹配兜底 | 覆盖无 import 的场景，但可能误匹配同名符号 |
| 回调合成标记 heuristic | 覆盖动态调度，但明确标注不确定性 |
| 边类型自动提升 | 语义更精确（calls→instantiates），但依赖解析正确性 |
