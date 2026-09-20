# 语言适配器关系抽取与通用关系存储设计

日期：2026-09-20
状态：已完成讨论，等待书面设计复核

## 1. 背景

CodeSense 当前由语言适配器把单个源码文件扫描为 `Declaration` 和 `ReferenceUse`，随后由通用
indexing pipeline 分配 `symbol_id`、创建 postings 并构建图。现役 Java scanner 已经读取
`extends` / `implements` 子树，但只把两者合并成 `Declaration.supertypes`，主要供调用解析沿父类型
查找方法；它没有物化 `extends`、`implements` 或方法级 `overrides` 边。

这种合并还损失了必要的 Java 语义：`Base<T>` 中的类型参数可能被误识别为父类型，
`interface A extends B` 在当前 tree-sitter 结构下也可能漏掉。于是 codegen 即使生成
`project(..., edge="implements")`，图中也没有对应事实可遍历。

CodeSearch 的实现关系抽取提供了两个可复用原则：先由语言工具判断实现关系，再把解析结果映射到
统一符号 ID；每条结果保留 relation kind、confidence、provenance 和歧义信息。但 CodeSearch 的
CodeQL/LSP 路径把类型继承统一为 `implements_type`，方法覆盖统一为 `implements_method`。CodeSense
需要保留 `extends`、`implements`、`overrides` 的区别，同时不能把 Java 规则写进通用图构建器。

## 2. 目标与非目标

### 2.1 目标

1. 由语言 adapter 负责发现、解析并分类语言相关代码关系。
2. 由通用 indexing 层负责验证、去重、统计和存储 adapter 输出的关系。
3. Java 第一版支持直接类型级 `extends` / `implements`。
4. Java 第一版用 AST 启发式暂时支持方法级 `overrides` / `implements`，允许可观测的误判与漏判。
5. 保持现有 scan -> symbol ID -> edges -> `EvalContext` -> QL 的总体流程。
6. 关系类型保持开放字符串，使后续语言可以增加自身关系而不修改 EdgeStore 和图算子。
7. 每条启发式关系保存 confidence 和 provenance，便于后续替换为更精确的 LSP、编译器或 CodeQL
   实现。

### 2.2 非目标

1. 第一版不引入 JDTLS、Java compiler 或 CodeQL 构建依赖。
2. 第一版不承诺完整的 Java overload resolution、泛型替换或虚方法分派。
3. 第一版不解析外部依赖中没有被 CodeSense 索引的父类型或方法。
4. 不让通用 indexing 层判断一个 Java 关系应当叫 `extends`、`implements` 还是 `overrides`。
5. 不为每种关系创建专用表；所有关系继续使用统一 Edge 表示。
6. 不改变 `project()`、`reach()`、`hop()` 的遍历协议；它们本来就接受开放的 edge kind。

## 3. 架构边界

完整数据流为：

```text
source file
    |
    v
Language.scan()
    |  Declaration + RelationHint + ReferenceUse
    v
通用 pipeline 分配稳定 symbol_id
    |
    v
Language.derive_relations(RelationContext)
    |  RelationFact(source_id, target_id, kind, evidence)
    v
通用 RelationBuilder
    |  校验 -> 去重 -> 统计 -> 序列化
    v
EdgeStore -> project / reach / hop
```

职责必须严格分开：

- language adapter 理解语言。它解析 package/import、父类型、接口、方法签名、修饰符和 override
  规则，并决定两个符号之间是否存在关系以及关系名称。
- pipeline 提供稳定 ID 和只读项目符号视图，按语言调用关系解析入口。
- RelationBuilder 不理解 Java、Python 或 C++。它只接受已经确定两端 ID 的 `RelationFact`，执行
  通用数据完整性操作。
- EdgeStore 和 QL 算子只看到有向边，不依赖产生边的语言。

`scan()` 与 `derive_relations()` 分成两阶段是必要的：前者只解析一个文件，无法知道另一个文件中
声明的父类或方法对应哪个项目符号；后者在全项目扫描和 ID 分配完成后工作，才能解析跨文件关系。

## 4. 语言无关的数据契约

### 4.1 RelationHint：扫描阶段的语法线索

新增语言无关的未解析关系对象：

```python
@dataclass(frozen=True, slots=True)
class RelationHint:
    kind: str
    target_name: str
    target_kind: str = ""
    target_qualified_name: str = ""
    line: int = 0
    column: int = 0
    confidence: float = 1.0
    provenance: str = ""
```

`Declaration` 增加：

```python
parameter_types: tuple[str, ...] = ()
relation_hints: tuple[RelationHint, ...] = ()
```

`RelationHint` 的 source 隐式为持有它的 declaration，避免在 scanner 尚未分配 ID 时建立脆弱的
行号反查。`kind` 是开放字符串；`target_kind` 和 qualified name 是 adapter 已经从语法环境中得到
的约束，不是通用层的推断。

`parameter_types` 是 callable 的语言无关签名事实。没有静态参数类型的语言可以留空；Java adapter
使用它做方法关系启发式匹配。现有 `signature` 继续服务检索和展示，不要求通用层重新解析字符串。

### 4.2 RelationContext：项目级只读符号视图

在符号 ID 分配完成后，pipeline 为每种语言创建：

```python
@dataclass(frozen=True, slots=True)
class IndexedDeclaration:
    symbol_id: int
    file: str
    declaration: Declaration


@dataclass(frozen=True, slots=True)
class RelationContext:
    declarations: tuple[IndexedDeclaration, ...]
```

context 只包含当前 adapter 所属语言的声明。跨语言关系不在第一版范围内；未来若需要，可增加独立
的跨语言 resolver，而不是让一个 adapter 猜测另一种语言的语义。

`RelationContext` 不暴露 EdgeStore、postings 或磁盘结构。adapter 可以自行建立按 qualified name、
simple name、owner、method key 等索引，但不能直接写入索引。

### 4.3 RelationFact 与 RelationBatch：adapter 的最终输出

```python
@dataclass(frozen=True, slots=True)
class RelationFact:
    source_id: int
    target_id: int
    kind: str
    site: tuple[int, int] | None = None
    confidence: float = 1.0
    provenance: str = ""
```

adapter 还需要把无法形成边的尝试交给通用统计，而不能通过可变全局状态传递。为此增加：

```python
@dataclass(frozen=True, slots=True)
class RelationDiagnostics:
    unresolved: int = 0
    ambiguous: int = 0
    skipped: int = 0


@dataclass(frozen=True, slots=True)
class RelationBatch:
    facts: tuple[RelationFact, ...] = ()
    diagnostics: RelationDiagnostics = RelationDiagnostics()
```

diagnostics 只保存聚合数量，不保存任意语言的候选结构。具体判断依据继续通过最终边的 provenance、
adapter 日志和测试暴露，避免通用协议演变成各语言 AST 的并集。

`Language` 协议增加：

```python
def derive_relations(
    self,
    context: RelationContext,
) -> RelationBatch:
    ...
```

没有额外关系的 adapter 返回空 batch。方法返回最终 ID 对，意味着“哪些元素之间是什么关系”已经
由 adapter 决定；通用层不再做基于语言语义的候选选择。

## 5. Java 类型关系解析

Java scanner 在已有 tree-sitter 遍历中读取类型声明的直接父类型子树，不进行第二次 parse：

- `class Child extends Base` 产生 `extends -> Base`；
- `class Child implements One, Two` 产生两条 `implements` hint；
- `interface Child extends One, Two` 产生两条 `extends` hint；
- `enum` 和 `record` 的接口列表产生 `implements` hint；
- annotation type 第一版不合成其隐含的 `java.lang.annotation.Annotation` 关系。

提取目标时只取父类型表达式的根类型，不把泛型参数当作父类型：

```text
Base<T>                 -> Base
pkg.Base<T>             -> pkg.Base
Outer.Inner             -> Outer.Inner
One, pkg.Two            -> One + pkg.Two
```

scanner 根据 compilation unit 的 package 和显式 import 尽量填写 `target_qualified_name`。Java
relation resolver 的解析顺序为：

1. 项目内唯一全限定名；
2. 显式 import 指向的唯一声明；
3. 同 package 中的唯一声明；
4. 唯一 simple name，作为 wildcard import 和不完整语法信息的低置信度兜底；
5. 多个 simple-name 候选或外部类型不建边，计入 ambiguity/unresolved 诊断。

直接类型关系的方向统一为：

```text
subtype / implementer --extends|implements--> supertype / interface
```

因此从抽象类型寻找实现时使用 `direction="backward"`，与现有“使用方指向定义”的图方向一致。
只物化源码直接声明的类型边，不预计算传递闭包；多跳关系交给 `reach()`，避免边数量膨胀。

## 6. Java 方法关系启发式

### 6.1 参数签名

Java scanner 从 AST 参数节点产生标准化 `parameter_types`，不从展示用 `signature` 反向解析。第一版
规范化规则为：

- 去掉参数名和参数注解；
- 泛型参数擦除，`List<User>` 记为 `List`；
- varargs 统一为数组，`String...` 记为 `String[]`；
- 保留数组维度；
- qualified type 使用 qualified identity，无法确定时保留 simple name；
- 类型变量不尝试绑定，例如 `T` 保持 `T`。

方法匹配 key 为 `(name, arity)`，标准化参数类型列表用于进一步消歧。返回类型不进入 key，因为
Java 允许协变返回类型。

### 6.2 候选查找

Java relation resolver 先解析类型关系，再按 owner type 建立方法索引。每个非 constructor 方法：

1. 沿已经解析的 `extends` / `implements` 类型图向上遍历；
2. 在祖先类型中查找相同 name 和 arity 的方法；
3. 优先选择标准化参数类型完全一致的候选；
4. 参数类型不完整时允许退化到 name + arity；
5. class 继承链只连接最近的匹配声明；
6. 不同 interface 中的同签名声明都可以成为合法目标；
7. 用 visited set 防止畸形源码造成继承环，并限制最大祖先深度；
8. 超过候选上限时不建边，记录 ambiguity，避免高频方法名形成噪声枢纽。

下列方法不参与启发式关系：

- constructor；
- source method 是 `static` 或 `private`；
- target method 是 `static`、`private` 或 `final`。

第一版不完整判断 Java package-private 跨包可见性；这种情况使用较低 confidence，并在已知限制中
明确记录。

### 6.3 关系分类

关系名称完全由 Java adapter 决定：

- target owner 是 class：`source method --overrides--> target method`；
- target owner 是 interface，source owner 是 class/enum/record：
  `source method --implements--> target method`；
- interface method 匹配父 interface method：`overrides`；
- 一个方法同时覆盖父类方法并实现接口方法时，允许同时存在两种边。

类型和方法都使用 `implements`，两端 kind 用来区分层级：

```text
class  --implements--> interface
method --implements--> method
method --overrides---> method
```

### 6.4 置信度与 provenance

建议的第一版证据等级为：

| 条件 | confidence | provenance |
|---|---:|---|
| 类型全限定名唯一匹配 | 1.00 | `java_ast_qualified_type` |
| 类型唯一 simple-name 兜底 | 0.80 | `java_ast_simple_type` |
| 方法参数类型一致且有 `@Override` | 0.95 | `java_override_exact` |
| 方法参数类型一致 | 0.90 | `java_signature_match` |
| name + arity 且有 `@Override` | 0.80 | `java_override_arity` |
| 仅 name + arity | 0.65 | `java_name_arity` |

`@Override` 只能提高已有候选的置信度，不能在没有可解析祖先方法时凭空产生目标。一个祖先存在
多个无法消歧的同名同 arity overload 时，不把 confidence 平均分配给虚构候选，而是跳过并记录
ambiguity。

## 7. 通用关系构建与存储

新增通用 RelationBuilder，或把等价逻辑作为 GraphBuilder 的无语言方法；无论具体文件如何组织，
它只执行以下操作：

1. 检查 source/target ID 是否属于当前索引；
2. 拒绝 self edge、空 kind、非有限 confidence 和区间外 confidence；
3. 把 `RelationFact` 转换为现有可序列化 edge row；
4. 按 `(source_id, target_id, kind)` 去重；
5. 重复 key 保留 confidence 最高的事实；confidence 相同时使用确定性的 provenance/site 排序；
6. 按 kind 统计最终关系数量；
7. 与 `contains`、`calls`、`references` 和 `in_file` 边合并后写入现有 `index.json`。

relation kind 不使用封闭 enum。内置文档先登记：

```text
contains, calls, references, imports, in_file,
extends, implements, overrides
```

未来 adapter 可以输出 `mixes_in`、`conforms_to`、`specializes` 等新 kind，而不修改 Index、EdgeStore、
`project()`、`reach()` 和 `hop()`。

类型父类的 AST 标识仍可作为现有 broad `references` 使用；方法 override/implements 是语义关系，
第一版不自动复制成 `references`，避免通用层擅自扩大 reference 语义。

## 8. 失败处理与可观测性

单个 hint 无法解析时不终止构建。Java adapter 继续处理其他关系，并在 `RelationBatch` 的
diagnostics 中记录：

- unresolved type targets；
- ambiguous type targets；
- unresolved method candidates；
- ambiguous method candidates；
- 因修饰符或候选上限跳过的关系。

通用 build stats 增加动态关系统计，而不是为未来每个语言关系增加固定字段：

```python
relation_counts: dict[str, int]
relation_unresolved: int
relation_ambiguous: int
relation_failures: int
```

最终边本身的 `confidence` 和 `provenance` 是可持久化的主要诊断证据。聚合计数用于构建日志和测试；
第一版不把所有被拒绝候选写进 index，避免调试数据显著放大索引。

如果某个 adapter 的整个 `derive_relations()` 意外失败，pipeline 记录该语言一次
`relation_failures` 并保留其他 postings、文件节点以及通用图边。失败不得影响其他语言；日志必须
包含 adapter 名称和异常类型，避免索引静默缺边。

## 9. 性能约束

每个 adapter 应先建立索引再解析关系，禁止为每个 relation hint 线性扫描全部声明。Java resolver
至少建立：

```text
qualified type -> type symbol
simple type -> candidate types
owner type -> methods by (name, arity)
type symbol -> direct parent type symbols
```

在候选数上限和最大祖先深度固定时，预期复杂度接近：

```text
O(declarations + relation hints + methods * bounded ancestors)
```

只物化直接类型关系和经过筛选的方法关系，不计算全项目继承闭包。relation dedup 使用 hash map，
不增加全边两两比较。

## 10. 索引兼容与查询集成

虽然 edge row 的 JSON 形状不变，索引语义已经增加新的必需关系。`FORMAT_VERSION` 必须提升，旧索引
打开时明确要求重新构建，不能让 `project(edge="implements")` 在旧索引上静默返回空集。

QL 算子无需修改遍历逻辑。新增关系后典型查询为：

```python
implementing_types = project(
    interface,
    ctx,
    edge="implements",
    direction="backward",
    kind="class",
)

implementing_methods = project(
    interface_method,
    ctx,
    edge="implements",
    direction="backward",
    kind="method",
)

overriding_methods = project(
    base_method,
    ctx,
    edge="overrides",
    direction="backward",
    kind="method",
)
```

codegen operator reference 和 relation vocabulary 在实现完成后加入这三个 kind，并说明方向为
concrete -> abstract。planned 的结构化 relation enum 若仍为封闭集合，也需要同步开放这三个值；这
只是暴露已有图能力，不在 planner 中新增 Java 特例。

## 11. 测试策略

### 11.1 Java scanner

- class extends generic superclass，只提取根父类型；
- class implements 多个接口；
- interface extends 多个接口；
- enum/record implements；
- qualified、显式 import、同 package 和 wildcard import 场景；
- method 参数类型、泛型擦除、数组和 varargs；
- `@Override`、static/private/final 修饰符。

### 11.2 Java relation resolver

- 类型关系 kind、方向、site、confidence 和 provenance；
- class method override；
- class method interface implementation；
- interface method override；
- 多层继承选择最近 class declaration；
- 一个方法同时产生 overrides 和 implements；
- overload 精确匹配、arity fallback、歧义跳过；
- 继承环、外部父类型和候选上限。

### 11.3 通用 pipeline

用 fake language adapter 返回任意 `RelationFact`，验证：

- pipeline 不解释 kind；
- 未知 relation kind 可以存储和加载；
- 无效 ID、自环和 confidence 被统一拒绝；
- 重复关系保留最强且结果顺序确定；
- 一个 adapter 失败不影响其他语言和通用边；
- relation counts 正确聚合。

### 11.4 QL 与索引兼容

- 保存、加载后 evidence 字段完整；
- `project()` 可正反向遍历 implements/extends/overrides；
- `reach()` 可沿直接类型边做多跳继承搜索；
- 旧 format version 明确拒绝并提示 rebuild；
- 现有 calls/references/contains/in_file 行为不变。

## 12. 预计代码边界

主要改动限定在：

- `codesense/lang/base.py`：通用 relation dataclass、Declaration 字段和 Language 协议；
- `codesense/lang/java/scanner.py`：AST 关系线索和参数类型提取；
- `codesense/lang/java/relations.py`：Java 项目级关系解析；
- `codesense/lang/java/__init__.py`：实现 `derive_relations()`；
- `codesense/indexing/pipeline.py`：构造 context、调用 adapter、聚合边；
- `codesense/indexing/relations.py`：通用验证、去重和统计；
- `codesense/index.py`：提升格式版本；
- codegen/planned relation vocabulary 和对应文档；
- Java adapter、通用 pipeline、index 和 QL 的单元测试。

现有 `codesense/indexing/graph.py` 可以继续负责 contains/calls/references。它不再承担 Java
extends/implements/overrides 的识别，也不应导入 `codesense.lang.java`。

## 13. 已知限制与后续演进

第一版明确记录但不解决：

- 泛型类型变量替换和 bounds；
- package-private 的完整跨包可见性；
- covariant return 的精确验证；
- compiler bridge method；
- default interface method 冲突；
- 外部依赖与 JDK 类型声明；
- 完整 overload resolution；
- 注解处理器或 Lombok 生成的方法；
- 运行时动态代理与反射关系。

后续升级到 JDTLS、javac symbol API 或 CodeQL 时，精确实现仍放在 Java adapter 的
`derive_relations()` 内，并继续输出相同 `RelationFact`。通用 pipeline、磁盘 edge 结构和 QL 查询
不需要随之重写；旧的低置信度 `java_name_arity` 结果可以按 provenance 定位、替换和重新评估。
