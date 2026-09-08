# File 结果目标与通用引用关系设计

日期：2026-09-08
状态：已完成讨论，等待书面设计复核

## 1. 背景

当前 CodeSense 的检索对象是 `Element`，最终结果 `Hit` 也由 `Element` 直接转换而来。
虽然 `Element.file` 保存了所属文件路径，设计文档也已经把 `file` 列为合法 `kind`，但现役索引
只创建类、方法、字段等声明节点。因此下面的查询最终仍返回命中的声明，而不是文件：

> Find Java files containing references to the PageRequest class.

这不是展示层去重就能解决的问题。查询要求的目标对象是文件；“引用 PageRequest”则是文件
入选的原因。目标类型与关系约束必须分开表达，否则后续遇到“导入某类型的文件”“包含某注解
方法的文件”“调用某 API 的文件”时还会继续增加特例。

## 2. 目标与非目标

### 2.1 目标

1. 把文件作为 `Element(kind="file")` 加入现有符号表和 `Frag`，不引入第二套结果模型。
2. 用硬约束表示“最终返回文件”，避免把现有的 `kinds` 偏好误用成输出类型。
3. 用通用 `references` 边表示代码元素对类、文件、函数等目标的引用。
4. 通过一个证据保留的图投影算子，把匹配到的代码元素转换成其所属文件。
5. 保持现有 scan -> postings/graph -> Frag -> SearchResult 的整体流程不变。
6. 默认查询保持原行为：未要求文件结果时，不返回新增的文件节点。

### 2.2 非目标

1. 本次不引入 `FileResult`、第二套文件索引或第二套执行器。
2. 不承诺一次覆盖所有语言的全部引用语法；先建立语言无关契约，并实现 Java 的项目内引用。
3. 不把 `references` 加进默认 coherence 图邻域，避免文件和热门类型成为新的排序枢纽。
4. 第一版不支持任意文件内容正则、全文行级搜索，也不把未索引的测试、生成代码重新纳入范围。
5. 第一版不为文件路径创建 postings；文件先作为结果目标和图节点。按名称或 glob 搜文件可在后续
   独立增加 `IndexField.PATH`，不与本次语义混在一起。

## 3. 核心决策

### 3.1 文件就是一种 Element

每个被 `source_files()` 接受的源码文件创建一个节点：

```python
Element(
    symbol_id=file_id,
    name="PageRecord.java",
    kind="file",
    file="api/src/main/java/.../PageRecord.java",
    span=(1, file_line_count),
    language="java",
)
```

约束如下：

- `symbol_id` 继续作为全索引唯一标识，字段名和 `Hit` 接口不变。
- `name` 是 basename，`file` 是项目根目录下的规范化相对路径。
- `signature`、`container`、`doc` 和 `modifiers` 使用现有空默认值。
- 空文件的 span 为 `(1, 1)`；其他文件覆盖完整源码行数。
- 即使文件没有可索引声明，也创建文件节点。
- 为尽量保持声明 ID 稳定，文件节点在所有声明扫描完成后按规范化路径排序追加。

`Hit` 无需增加子类。文件结果自然表现为 `Hit(kind="file", file=..., line=1)`。

### 3.2 target 是硬输出契约，kinds 仍是软偏好

在 `QuerySpec` 增加：

```python
target: tuple[str, ...] = ()
```

含义：

- `target=("file",)`：最终 `Frag` 只能包含文件节点；非文件候选需要投影到所属文件。
- `target=()`：保持兼容模式，最终结果排除 `kind="file"`，其余行为不变。
- 未来可以扩展到其他硬目标类型，但本次只开放 `file`。

现有 `QuerySpec.kinds` 和 `Narrow.kind` 继续表示排序偏好，不能承担 target 的职责。目标类型错误
不是“分数低”，而是返回对象不符合查询。

`search()` / `Project.search()` 增加可选 `target` 参数。目标解析优先级为：

1. 调用者显式传入的 `target`；
2. planned 路由的结构化理解结果；
3. codegen 脚本明确返回的文件节点；
4. lexical 路由只对明确的 `file` / `files` / `Java files` 等目标词做保守识别。

关系词不参与 target 猜测。“import”“reference”“call”说明入选关系，不等于返回对象一定是文件。
所有路由结束后执行同一个 target 后置条件，防止模型脚本或降级路径返回错误类型。

`SearchResult` 增加 `target` 字段，用于解释实际采用的输出契约；`Hit` 的序列化形状保持兼容。

## 4. 图模型

### 4.1 in_file：归属关系

为每个非文件声明创建：

```text
declaration --in_file--> file
```

边属性：

- `confidence=1.0`
- `provenance="source_path"`
- 不创建 file -> file 自环

不复用 `contains`，原因有三点：

1. 现有 `contains` 的方向是容器类型 -> 成员，语义是代码结构包含；`in_file` 是物理归属。
2. 当前 coherence 默认遍历 `calls` 和 `contains`。复用后文件节点会成为高连接度枢纽并改变旧排序。
3. 独立边使“投影到文件”始终是一次确定的一跳操作，复杂度与输入候选数线性相关。

### 4.2 references：通用引用关系

边方向统一为：

```text
最小的可索引引用方 --references--> 被引用目标
```

两端都是普通 `Element`，因此可以组合出：

```text
method --references--> class
method --references--> method
field  --references--> class
file   --references--> class      # import 等文件级引用
file   --references--> file       # 语言本身使用路径引用时
```

“最小的可索引引用方”是重要约束。方法体中的类型引用归属于方法，而不是一律归属于文件；位于
import、package 或其他声明外区域的引用才归属于文件节点。这样既能回答“哪个方法引用它”，也能
再通过 `in_file` 得到“哪个文件引用它”。

精确关系与宽关系可以共存：

```text
Client.create --references--> PageRequest
Client.create --calls-------> PageRequest.ofSize
Client.java   --imports-----> PageRequest
Client.create --in_file-----> Client.java
```

`calls`、`imports`、`instantiates` 等边保留其更强语义；只要它们确实构成引用，也可以同时物化
一条 `references`。现有 `EdgeKey=(source_id, target_id, kind)` 已允许同一对节点存在多种边。

引用解析遵循“宁缺毋滥”：

- 唯一的项目内全限定名匹配：高置信度；
- 有类型提示的唯一简单名匹配：中高置信度；
- 多个简单名候选：置信度按候选数衰减，并受候选数上限保护；
- 外部库或无法可靠解析的引用：不创建指向虚构节点的边，只计入 unresolved 统计。

每条引用边保存 `site`、`confidence`、`provenance`。索引加载必须恢复 `site`，不能只在保存时存在。

## 5. 单次扫描的数据契约

为了同时获得声明内引用和 import 等文件级引用，而不重复解析源码，语言适配器的扫描结果扩展为：

```python
@dataclass(frozen=True, slots=True)
class ReferenceUse:
    name: str
    line: int
    column: int = 0
    relation: str = "references"
    target_kind: str = ""
    qualified_name: str = ""


@dataclass(frozen=True, slots=True)
class ScanResult:
    declarations: tuple[Declaration, ...]
    references: tuple[ReferenceUse, ...] = ()
```

`Language.scan(source)` 仍是流水线唯一扫描入口，只是从声明序列升级为 `ScanResult`。Java scanner 在
同一棵 tree-sitter AST 上提取两类信息，不允许为 references 再 parse 一次。

pipeline 在一次文件处理中：

1. 保存规范化路径、语言、行数和 `ScanResult`；
2. 按现有规则创建声明、postings 和声明图观察数据；
3. 所有声明完成后追加文件节点；
4. 根据路径创建全部 `in_file` 边；
5. 用引用位置选择同文件内 span 最小的包含声明，找不到时使用文件节点；
6. 在全项目符号表完整后解析目标并创建 `references` / 精确关系边。

这仍保持“先扫描全项目，再建跨文件边”的现有 GraphBuilder 模式。文件节点和引用只扩展其输入，
不新增旁路索引或查询阶段。

## 6. 证据保留的 project 算子

新增一个通用的一跳投影算子：

```python
project(
    frag,
    ctx,
    *,
    edge="in_file",
    direction="forward",
    kind=None,
    include_self=False,
) -> Frag
```

行为：

1. 沿指定边精确移动一跳；
2. `kind` 非空时只保留指定目标类型；
3. `include_self=True` 时，输入中已经是目标类型的节点直接保留；公共 target 后置条件显式使用
   该选项，普通关系投影默认不保留起点；
4. 返回节点 Frag，不物化路径；
5. 把源节点 Evidence 合并到目标节点，并以零分 graph hit 记录边 kind、site 和 provenance。

Evidence 的合并按 query unit 取已有 combined score 的最大值，而不是按同一文件中的命中声明数
求和。因此包含十个同类命中的大文件不会天然压过只包含一个高质量命中的小文件；如果一个文件
满足多个不同 query unit，各 unit 的证据仍会共同贡献最终分数。

`project` 与 `reach` 分工明确：

- `reach` 用于无证据的邻域探索；
- `project` 用于结果对象转换，必须传递证据；
- `hop` 用于验证两个已知 Frag 之间的路径并保留 witness。

## 7. 查询路由集成

### 7.1 codegen

把 `project`、`references`、`imports`、`in_file` 加入脚本命名空间、白名单和 prompt。示例查询的
理想脚本形状是：

```python
page_request = eval_unit(page_request_unit, ctx)
referencers = project(
    page_request,
    ctx,
    edge="references",
    direction="backward",
)
answer = project(
    referencers,
    ctx,
    edge="in_file",
    kind="file",
    include_self=True,
)
```

这里先从 `PageRequest` 目标反向投影到引用方，再投影到文件；两次投影都保留原始词法证据和图
关系说明。表达为 “imports PageRequest” 时，脚本可使用更精确的 `imports` 边。

### 7.2 planned

结构化输出增加 `target`，关系条目从无类型二元组升级为带 edge 的对象：

```json
{
  "target": ["file"],
  "relations": [
    {"src": "group A", "dst": "group B", "edge": ["references"]}
  ]
}
```

现有两端都有显式 query unit 的关系继续通过统计验证，并把 edge kind 原样带入
`GraphConstraint`；验证器只能统计请求的 edge kinds，不能把其他边算作命中。

第一版不为 planned 路由新造“隐式引用方 unit”。因此像示例这样只有 `PageRequest` 一个显式语义
单元、引用方完全由关系反向产生的查询，由 codegen 路由完整表达；planned 路由仍可应用 file
target，但召回取决于其现有候选。后续如要补齐，可单独设计 result-endpoint constraint，避免本次
为了一个路由改写整个 planner。

实现时必须先修复现有 `_planned()` 对 `build_spec()` 的参数传递错误，并正确解包其
`(QuerySpec, notes)` 返回值，再加回归测试；否则新增 planned target 会被异常降级掩盖。

### 7.3 lexical 与降级

lexical 路由不猜复杂关系。它继续获得词法候选，在明确识别或显式传入 `target="file"` 后，统一
通过 `project(..., edge="in_file")` 返回文件。该结果是词法近似，不声称已经验证 `references`。

如果 codegen / planned 失败并降级 lexical，target 契约仍然保留，`SearchResult.notes` 明确记录
关系语义丢失和降级；不能悄悄返回声明节点。

## 8. 兼容性与持久化

### 8.1 默认行为

- 未请求 `target=file` 时，公共搜索后置条件排除文件节点。
- `calls` / `contains` coherence 默认值不变。
- 文件节点第一版不产生 postings，现有 vocabulary 和 declaration posting df 不变。
- posting index 的 `total_symbols` 继续使用可检索声明数，而不是 `len(all_elements)`，避免加入文件
  节点后 ICF、unit specificity 和旧查询排序发生无关漂移。
- `EvalContext` 增加从构造函数注入的 declaration population（并提供兼容默认值）；planner 的
  specificity 分母使用它，不能继续无条件使用包含 file 节点的 `symbols.count()`。

### 8.2 索引格式

新增 file 元素、`in_file` / `references` 边以及 `Edge.site` 的反序列化属于持久化语义变化，
`FORMAT_VERSION` 必须递增。旧索引加载时继续明确报错并要求重建，不做不完整迁移。

payload 记录 declaration population，供 `InMemoryPostingIndex(total_symbols=...)` 和
`EvalContext` 使用；meta 中的 `symbols` 仍表示全部 Element 数量，同时增加或在统计输出中区分
files 与 declarations，避免指标含义模糊。

## 9. 错误处理与性能边界

- 单个文件解析失败时沿用现有策略：记录 failed 并跳过；不创建可能声称已完整解析的文件节点。
- 引用解析失败只增加 unresolved，不中止索引。
- `in_file` 每个声明最多一条，空间复杂度 `O(number_of_declarations)`。
- 引用候选通过按名字和类型建立的表查询，不允许对每个 ReferenceUse 扫描全部 Element。
- `project` 使用 EdgeStore 的方向索引，时间复杂度为 `O(input_nodes + traversed_edges)`。
- 对高频歧义引用沿用候选上限，避免构造高出度噪声图。
- 默认 coherence 不遍历 `in_file` / `references`，旧查询的图搜索规模不增加。

## 10. 分阶段实施

### Slice A：file target 基础

1. 创建 file Element 和 `in_file` 边。
2. 增加 `project` 算子及证据聚合。
3. 增加 target 契约、公共后置条件和 `SearchResult.target`。
4. 更新 codegen / lexical 的文件输出能力。
5. bump index format，并保持声明 postings 的统计分母。

完成 Slice A 后，所有“先找到某些代码元素，再返回其文件”的查询都有统一出口。

### Slice B：reference 图增强

1. 增加 `ScanResult` / `ReferenceUse`，Java 单次 AST 扫描提取项目内引用。
2. GraphBuilder 解析并创建 `references`、`imports` 等边及统计。
3. 保存并加载 `Edge.site`。
4. 更新 codegen prompt 和 planned 的带类型 relation schema/验证。
5. 增加 PageRequest 端到端用例。

两个 slice 使用同一数据模型，但可以独立验收。Slice A 不等待完整引用解析即可交付；Slice B 不再
修改结果模型，只增强“为什么这个文件入选”的结构化能力。

## 11. 测试与验收标准

### 11.1 单元测试

- pipeline 为有声明、无声明、多语言文件创建唯一 file Element。
- 每个声明恰有一条正确方向的 `in_file`；无 dangling edge。
- `project` 支持 forward/backward、kind 过滤、include_self 和空 Frag。
- 多个同 unit 命中投影到同一文件时取最大分，不按数量累加。
- 多个不同 unit 投影到同一文件时保留全部证据。
- `QuerySpec.target` 反序列化、显式 target 优先级和默认排除 file。
- 索引 round-trip 保留 file 节点、edge kind、site、confidence、provenance。
- relation validator 只按请求的 edge kind 验证。
- planned 路由不再因 `build_spec` 参数错误降级。

### 11.2 集成测试

建立最小 Java fixture：

```text
PageRequest.java        # 声明 PageRequest
Controller.java         # import 并在方法体中引用 PageRequest
Unrelated.java          # 不引用
Empty.java              # 无声明
```

验收：

1. `target=file` 的结果全部为 `kind="file"`。
2. “Find Java files containing references to the PageRequest class.” 返回
   `Controller.java`，不把其中的方法作为最终 Hit。
3. 仅声明 PageRequest、但没有引用关系时，不因目标类自己的名称命中而错误返回
   `PageRequest.java`。
4. 使用 `imports` 时只返回具有 import 关系的文件。
5. 不带 file target 的旧查询仍返回声明，不出现 file Hit。
6. codegen 失败降级时仍满足 file target，并在 notes 中说明只保留词法近似。

### 11.3 回归与性能

- 现有 QL、index、search 测试全部通过。
- 在固定旧查询集上比较修改前后的 lexical 非文件 Hit 顺序；由于 postings、ICF 分母和 coherence
  边集合均保持不变，结果应完全一致。codegen prompt 和 planned 缺陷修复会改变各自行为，单独做
  质量回归，不要求逐项同序。
- 记录索引的 declarations/files/edges 数量和构建耗时；确认额外空间近似线性增长。
- 提交前执行 `ruff check .`、`ruff format --check .`、`pytest`。

## 12. 涉及模块

预计修改集中在现有边界内：

- `codesense/ql/frag.py`：继续复用 `Element` / `Edge`，无需新结果基类。
- `codesense/lang/base.py`、`codesense/lang/java/scanner.py`：扫描结果和引用事实。
- `codesense/indexing/pipeline.py`、`codesense/indexing/graph.py`：文件节点、归属边、引用解析。
- `codesense/index.py`：格式版本、declaration population 和 `Edge.site` round-trip。
- `codesense/ql/operators/`：新增 `project` 并导出。
- `codesense/ql/compile/`、`codesense/llm/`：target 与带 edge kind 的关系。
- `codesense/search.py`、`codesense/project.py`：公共 target 契约和结果说明。
- 对应 `tests/unit/` 与 Java 集成 fixture。

不新增执行器、不改变 `Frag` 作为唯一算子流转类型的原则，也不把业务逻辑放进脚本入口。
