# 类型化通用查询理解设计

日期：2026-09-11
状态：设计已完成讨论，等待书面设计复核

## 1. 背景

planned 搜索目前由 `codesense.llm.compiler.QueryUnderstanding` 调用一次 LLM，要求模型返回一段
松散 JSON，再用手工逻辑截取花括号、解析字段并兼容模型偶尔产生的不同形状。这个边界存在三类
问题：

1. 输出格式靠 Prompt 约定，`terms`、`relations` 和 `target` 仍需逐字段猜测与修复；
2. `target` 只开放了 `file`，不能表达搜索类、方法、字段或同时返回多种代码元素；
3. Prompt 把项目词表误当成输出白名单，而 planned 编译又在 grounding 之前按精确 posting 丢弃
   词表外 term，使现有 expansion table 无法完整处理 LLM 给出的通用语义词。

本次把 LLM 边界升级为由 Pydantic 定义的通用查询理解 IR，通过 OpenAI-compatible Chat
Completions 的 `response_format.type=json_schema` 请求严格结构化输出。它仍然只是
`QuerySpec` 的语义输入，不取代 grounding、统计校验、规划器或执行器。

## 2. 目标与非目标

### 2.1 目标

1. 用 Pydantic 模型作为 LLM 输出和本地解析的唯一结构定义。
2. 抽取查询中的语义单元、原始 query term、同义词、相关派生词、关系、注解、判定标准和结果
   target。
3. `targets` 支持文件、类型、类、接口、枚举、记录、函数、方法、构造函数、字段和注解类型，
   并允许同时返回多种元素。
4. LLM 输出的 term 无需存在于项目词表；canonical term 通过现有 expansion table 接地到项目
   拼写。
5. Prompt 只提供有限的、重复出现的代表性项目词汇及其 df，避免完整词表占满上下文。
6. 保持现有 `Project.search -> route -> QuerySpec -> planner -> Plan.run -> SearchResult` 架构和
   planned 失败后的 lexical fallback。
7. 统一编译、估算、校验与执行阶段对 exact term 和 grounded surface 的解析方式。

### 2.2 非目标

1. 不让 LLM 直接生成 `QuerySpec`、算子序列或执行计划。
2. 不把项目词表变成合法输出白名单，也不因单个 term 无法接地而拒绝整个理解结果。
3. 不新增第二套文件、类或函数结果模型；所有结果仍然是 `Element` / `Hit`。
4. 不因 target 类型自动推断 `contains`、`calls` 等关系；关系必须由查询语义显式提出并通过统计
   校验。
5. 不为不支持 Structured Outputs 的 provider 保留第二套松散 JSON 解析协议。
6. 不改变 codegen 和 lexical 路由各自生成候选的方式，只共享扩展后的通用 target 后置条件。

## 3. 架构边界

完整数据流为：

```text
query + representative project vocabulary
                    |
                    v
       LLM structured extraction
                    |
                    v
       Pydantic structural validation
                    |
                    v
 canonical terms -> project grounding -> postings
                    |
                    v
 group cohesion / relation lift validation
                    |
                    v
        QuerySpec -> planner -> Plan.run
                    |
                    v
      target enforcement -> SearchResult
```

职责划分：

- LLM 提出语义：查询讲哪些概念、有哪些 literal/synonym/derived term、显式表达了什么关系、希望
  返回哪些元素类型。
- Pydantic 保证结构：字段、枚举、数值范围、额外字段和跨字段引用合法。
- Grounding 处理词汇落地：canonical term 先精确查找，再通过 expansion table 映射到项目拼写。
- 统计层验证事实：分组是否有 posting 凝聚度、关系是否有足够 edge lift、哪些 kind/field 更适合、
  哪个执行顺序成本更低。
- Target 只约束最终结果类型，不隐式创造候选间关系。
- Relation endpoint 指明关系两端是已命名 unit 还是待求的 result；它解决“返回关系哪一端”，不与
  target 的元素类型职责混合。

Pydantic 只放在 `codesense.llm`。`codesense.ql` 继续保持标准库契约，并接收普通 dataclass、tuple
和 enum 字符串。

## 4. 类型化查询理解 IR

### 4.1 枚举

```python
class TermSource(StrEnum):
    LITERAL = "literal"
    SYNONYM = "synonym"
    DERIVED = "derived"


class TargetKind(StrEnum):
    FILE = "file"
    TYPE = "type"
    CLASS = "class"
    INTERFACE = "interface"
    ENUM = "enum"
    RECORD = "record"
    FUNCTION = "function"
    METHOD = "method"
    CONSTRUCTOR = "constructor"
    FIELD = "field"
    ANNOTATION = "annotation"


class RelationKind(StrEnum):
    CALLS = "calls"
    CONTAINS = "contains"
    REFERENCES = "references"
    IMPORTS = "imports"
    IN_FILE = "in_file"


class EndpointKind(StrEnum):
    UNIT = "unit"
    RESULT = "result"
```

`literal` 表示查询中直接出现的名称或概念；`synonym` 表示语义上基本可互换的词；`derived`
表示在代码场景或当前项目中相关但不可互换的概念。三者都可以不在项目词表中，区别用于评分和
Evidence，不用于决定是否合法。

### 4.2 Pydantic 模型

```python
class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SemanticTerm(StrictModel):
    value: str = Field(min_length=1)
    source: TermSource
    weight: float = Field(ge=0.0, le=1.0)
    related_query_terms: list[str]
    reason: str


class SemanticUnit(StrictModel):
    name: str = Field(min_length=1)
    concept: str = Field(min_length=1)
    query_terms: list[str]
    terms: list[SemanticTerm] = Field(min_length=1)


class RelationEndpoint(StrictModel):
    kind: EndpointKind
    unit: str | None


class QueryRelation(StrictModel):
    source: RelationEndpoint
    target: RelationEndpoint
    edges: list[RelationKind] = Field(min_length=1)


class QueryUnderstandingResult(StrictModel):
    units: list[SemanticUnit] = Field(min_length=1)
    relations: list[QueryRelation]
    targets: list[TargetKind]
    annotations: list[str]
    criterion: str = Field(min_length=1)
```

所有模型返回字段都是 required；没有 relation、target 或 annotation 时返回空数组。Pydantic
model validator 另外保证：

- unit name 唯一；
- `kind="unit"` 的 endpoint 引用已存在 unit，`kind="result"` 时 unit 必须为 null；
- relation 两端不能引用同一 unit，也不能同时为 result；
- 一次查询最多包含一个带 result endpoint 的 relation；
- canonical `value.casefold()` 在整份响应中唯一，重复项保留首次出现；去重后 unit 仍须至少有一个 term；
- targets 保持首次出现顺序并去重。

局部重复属于可安全归一化的数据，不让一次查询因此降级；未知枚举、缺字段、额外字段、空 unit
或越界 weight 属于契约错误，拒绝整份响应。

### 4.3 示例

查询：

> Find Java files containing references to the PageRequest class.

结构化结果：

```json
{
  "units": [
    {
      "name": "page_request",
      "concept": "the PageRequest pagination type",
      "query_terms": ["PageRequest"],
      "terms": [
        {
          "value": "PageRequest",
          "source": "literal",
          "weight": 1.0,
          "related_query_terms": ["PageRequest"],
          "reason": "explicitly named by the query"
        },
        {
          "value": "pagination",
          "source": "synonym",
          "weight": 0.8,
          "related_query_terms": ["PageRequest"],
          "reason": "semantic equivalent in the pagination domain"
        },
        {
          "value": "page",
          "source": "derived",
          "weight": 0.6,
          "related_query_terms": ["PageRequest"],
          "reason": "frequent project-specific term related to PageRequest"
        }
      ]
    }
  ],
  "relations": [
    {
      "source": {
        "kind": "result",
        "unit": null
      },
      "target": {
        "kind": "unit",
        "unit": "page_request"
      },
      "edges": ["references"]
    }
  ],
  "targets": ["file"],
  "annotations": [],
  "criterion": "A Java file containing code that refers to PageRequest"
}
```

## 5. Structured Outputs 请求

Schema 由 Pydantic 在请求时生成，不在 Prompt 和 Python 字典里维护第二份：

```python
@cache
def query_understanding_response_format() -> dict[str, object]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "query_understanding",
            "strict": True,
            "schema": QueryUnderstandingResult.model_json_schema(),
        },
    }
```

使用惰性缓存函数而不是模块顶层生成 Schema，遵守 import 模块不执行逻辑的仓库约定。

Chat Completions 请求增加：

```python
"response_format": query_understanding_response_format()
```

成功响应直接解析：

```python
QueryUnderstandingResult.model_validate_json(content)
```

原来的花括号截取、`json.loads()`、字段 `.get()` 猜测、terms list 兼容和 target 缺失语义全部
删除。Prompt 只说明字段的业务含义和抽取标准，不再重复输出 JSON 示例。

`QueryUnderstanding.understand()` 返回 `QueryUnderstandingResult | None`，不再返回无类型 dict。
下游通过一个显式适配步骤把每个 `SemanticTerm` 转换为现有 QL `Term`：

```python
Term(
    value=semantic.value.lower(),
    source=semantic.source.value,
    weight=semantic.weight,
    reason=semantic.reason,
)
```

unit 提议转换成现有 `groups`。两端都是 unit 的 relation 转换成现有三元组并继续通过
`codesense.ql.compile.validate`；带 result endpoint 的 relation 进入第 8.3 节定义的结果绑定
步骤。任何 relation 都不会仅因来自严格 Schema 而跳过项目事实校验。

通过统计校验的 model group 名称直接成为 `QueryUnit.name`，不再改写为位置相关的 `u0/u1`；
没有 model groups、由统计自动 partition 时仍使用 `q/uN`。如果 group 校验把某个提议折叠进另一
group，build adapter 根据该组保留下来的 grounded terms 把 relation endpoint 映射到最终 unit。
result-bound relation 的 anchor group 若完全无法接地，则编译失败并走现有 lexical 降级，不能静默
改成另一个结果语义。

## 6. 代表性项目词表

项目词表在 Prompt 中承担两项作用：

1. 告诉模型项目偏好的拼写和领域术语；
2. 帮助模型产生项目特有的 synonym/derived term。

它不是完整项目词表，也不是输出白名单。Prompt 明确写成：

```text
Representative terms repeatedly used in this project. The number after each
term is how many code elements contain it. Prefer project terminology when
useful, but you may emit terms outside this sample; later grounding maps
semantic terms to project spellings.
```

输入采用 `term:df`，例如：

```text
page:10377, redis:1289, cache:834, pagination:76
```

现有 `Index.vocabulary()` 已按 `(-df, term)` 排序，继续复用。采样器只做一次线性过滤并在达到
limit 后停止：

```python
def representative_vocabulary(vocabulary, *, limit, min_df):
    if limit <= 0:
        return []
    selected = []
    for term, df in vocabulary:
        if df < min_df:  # input is df-descending, so all later rows are lower
            break
        selected.append((term, df))
        if len(selected) >= limit:
            break
    return selected
```

`vocab_size` 继续控制上限，新增 `vocab_min_df` 控制重复阈值，默认值为 2。两者通过 CLI、
`Project.build/open` 和构造函数传入，不进入配置文件。codegen 与 planned 共用同一份代表性词表；
lexical 不向模型发送词表。

复杂度为 `O(min(V, limit + skipped))`，内存至多 `O(limit)`；不复制完整词表到 Prompt。

## 7. Grounding 贯通

### 7.1 当前阻断

运行期 `collect_term_hits()` 已支持：

```text
canonical term -> exact surface + expansion targets -> postings
```

但 `build_spec()`、`partition()`、`validate_groups()`、`relation_lift()` 和 kind/field 推断仍在
grounding 之前使用 `postings.term_info(term)` 或 `postings.lookup(term)`。因此词表外 canonical term
会被过早丢弃，或者在执行能命中时仍被统计层误判为零行。

### 7.2 统一解析函数

新增标准库模块 `codesense/ql/term_resolution.py`：

```python
def resolved_surfaces(term: str, ctx: EvalContext) -> tuple[Expansion, ...]:
    """Return exact and grounded project spellings that have postings."""


def resolved_postings(term: str, ctx: EvalContext) -> tuple[Posting, ...]:
    """Return deduplicated postings across exact and grounded surfaces."""


def resolved_symbol_ids(term: str, ctx: EvalContext) -> frozenset[int]:
    """Return symbols matched by one canonical semantic term."""
```

约束：

- exact surface 永远排第一且分值为 1.0、reason 为 `exact`；
- expansion target 按 expansion table 原顺序追加；
- surface 按 target 去重，exact 胜过指向相同 target 的 expansion；
- 只返回确实存在 `TermInfo` 的 surface；
- postings 按 `(symbol_id, field)` 去重，避免 exact 和 expansion 重合时重复计分；
- 不在查询时计算向量或调用第二次 LLM。

以下位置统一改用该模块：

- `collect_term_hits()`；
- `estimate_unit()`；
- `build_spec()` 的可执行 term 判定；
- `infer_kinds()` / `infer_fields()`；
- `partition()` 的 posting 集合；
- `validate_groups()` 的 cohesion；
- `relation_lift()` 的两侧端点集合。

单个 term 无法解析时记录 validation note 并忽略。只要至少一个 unit 还有可执行 term，查询继续；
所有 unit 均为空时 `build_spec()` 抛出当前类型的 `ValueError`，由 planned fallback 捕获。

`literal`、`synonym`、`derived` 都走相同 grounding 路径，source 只影响语义解释和权重，不影响
term 是否有资格进入 expansion table。

## 8. 通用、多类型 target

`targets` 是硬输出契约，使用并集语义：

- `[]`：无显式 target，保持默认返回非文件声明；
- `["file"]`：只返回文件；
- `["method"]`：只返回方法；
- `["class", "interface"]`：返回类与接口；
- `["file", "method"]`：同时返回文件与方法。

调用者显式传入 `Project.search(target=...)` 时仍高于 LLM 结果。planned Schema 始终包含 targets，
因此该路由不再使用“字段缺失代表模型未决定”的状态；空数组就是明确的无 target。

### 8.1 归一化

target 使用独立于 soft kind preference 的映射：

| 抽取值 | 具体 `Element.kind` |
|---|---|
| `file` | `file` |
| `type` | `class`, `interface`, `enum`, `record`, `annotation_type` |
| `class` | `class` |
| `interface` | `interface` |
| `enum` | `enum` |
| `record` | `record` |
| `function` | `method`, `constructor` |
| `method` | `method` |
| `constructor` | `constructor` |
| `field` | `field` |
| `annotation` | `annotation_type` |

`class` 严格对应 class；泛指所有声明类型时使用 `type`。target 归一化保持首次出现顺序并去重。
现有 `QuerySpec.kinds` 仍是统计推断出的排序偏好，不承担硬 target 职责。

### 8.2 执行

无 file target 时只需按 kind 对当前 Frag 做直接过滤。包含 file target 时：

1. 保留当前 Frag 中已符合其他 target kind 的直接命中；
2. 沿 `in_file` 把所有命中声明投影到所属文件；
3. 对两个 Frag 求并集，由 Frag 合并 symbol 和 Evidence；
4. target enforcement 位于 intent/relation boost 之后、公共 limit 之前。

现有 `project(edge="in_file", kind=targets, include_self=True)` 已经具备此语义，planner 的
`ProjectTarget` 只需接受放开后的通用 target。没有 file 时使用直接 `induced()`，避免遍历必然被
kind 过滤掉的 `in_file` 边。

第一阶段不因为 `target=["class"]` 就自动把命中的 method 提升到所属 class，也不因为
`target=["method"]` 就展开命中 class 的全部成员。此类转换必须来自显式 `contains` relation，
不能由结果类型偷偷改变查询语义。

### 8.3 result endpoint：返回关系的哪一端

target 只能回答“最终允许返回什么 kind”，不能回答“返回 relation 的 source 还是 target”。
因此 relation endpoint 可以引用一个已命名 unit，也可以使用唯一的开放变量 `result`：

```json
{
  "source": {"kind": "result", "unit": null},
  "target": {"kind": "unit", "unit": "page_request"},
  "edges": ["references"]
}
```

它表达：

```text
$result --references--> page_request
```

而不是“page_request 自己是结果”。执行规则：

- `unit -> unit`：沿用现有 relation lift validation 和 ranking boost；
- `result -> unit`：先计算 unit Frag，再沿 edge 反向 `project()`，投影得到的 source Frag 成为结果
  候选；
- `unit -> result`：先计算 unit Frag，再沿 edge 正向 `project()`，投影得到的 target Frag 成为结果
  候选；
- `result -> result`：Pydantic model validator 拒绝。

result-bound relation 是候选生成操作，不是软 boost。投影已经要求索引中存在指定 kind 的真实边，
因此不再用一个未知 endpoint 做 lift 估算；没有匹配边时返回空 Frag。投影保留 anchor unit 的
Evidence，并增加 relation kind、site 和 provenance 的 graph evidence。

第一版每个查询最多允许一个 result-bound relation。多个 unit-to-unit relation 仍可与它并存，
但暂不定义多个开放结果关系的 AND/OR 组合，避免把布尔查询语言隐藏在 Schema 中。

planner 增加一个窄职责的 `ResolveResultRelation` step，内部复用现有 `project()`，不增加执行器或
图 API。顺序为：

```text
evaluate anchor units
-> validate/apply unit relations
-> resolve result-bound relation
-> intent judge on result candidates
-> target enforcement/project-to-file
-> public limit
```

示例查询先以 `PageRequest` unit 找到目标声明，再沿 `references` 反向投影到引用方，最后依据
`targets=["file"]` 沿 `in_file` 投影到文件。这能返回 `Controller.java`，不会错误返回仅声明
`PageRequest` 的 `PageRequest.java`。

## 9. 失败与降级

下列结构化请求问题使 `understand()` 返回 None，并沿现有 planned -> lexical fallback：

- HTTP、超时或非成功响应；
- provider/model 不支持请求中的 JSON Schema；
- message 表示 refusal 或缺少 content；
- content 不符合 Pydantic 模型。

Schema 合法但所有 semantic term 都无法接地时，`understand()` 已经成功；随后 `build_spec()`
产生 `no grounded terms` 的 planned failure，并进入同一个 lexical fallback。合法的
result-bound relation 没有对应图边时则是有效的空搜索结果，不视为编译失败。

日志区分 `request failed`、`refused`、`schema validation failed` 和 `no grounded terms`，但不输出
完整 Prompt、API key 或未截断的模型响应。

不在失败后自动重试松散 JSON 请求。双协议会重新引入手工解析、缺字段语义和不可预测分支；需要
planned 路由的 provider 必须支持 JSON Schema，不支持时使用已有 lexical 降级。

## 10. 性能

1. Pydantic Schema 通过无参数缓存函数生成一次。
2. Prompt vocabulary 在达到 limit 后停止，不复制完整词表。
3. `resolved_surfaces()` 每个 canonical term 在一次编译中会被 build、validation、cost 多次使用，
   因此 `_planned` 为一次查询创建一个 `TermResolver(ctx)`，并把它传给 `build_spec()` 与 `plan()`；
   resolver 内以 canonical term 为键缓存 surface/posting/symbol 结果，查询结束即释放，不使用无界模块
   全局缓存。
4. surface 和 posting 去重使用 dict/set，单 term 复杂度为 `O(S + P)`；grounding 的
   `max_targets` 已限制 S。
5. result-bound relation 使用 EdgeStore 的方向索引和现有 `project()`，复杂度为
   `O(anchor_nodes + traversed_edges)`，不扫描全图。
6. 不增加查询时向量计算、索引扫描或 LLM 往返。

缓存由 planned 编译调用局部持有，查询结束即释放，避免长期打开多个 Project 时保留 store。

## 11. 兼容性与迁移

- `QueryUnderstanding.understand()` 从 dict 返回值改为 Pydantic model，仅 `codesense.search._planned`
  是生产调用方；单元测试中的 fake understanding 同步改为模型对象。
- `QuerySpec.target` 和 `SearchResult.target` 继续使用 `tuple[str, ...]`，只是合法值从 file 扩展到
  具体 Element kinds。
- 调用者显式 `target="file"` 的现有行为不变。
- 未显式要求 target 的 lexical/codegen 查询继续排除 file；planned 使用 Schema 的空 targets
  表达相同行为。
- `normalise_target()` 接受旧字符串、序列和新的 enum 值，保留已有公开调用方式。
- 原有 unit-to-unit relation 行为不变；只有 Schema 明确提供 result endpoint 时才增加候选投影。
- `pydantic>=2` 作为核心直接依赖加入 `pyproject.toml`；不依赖其被 `openai` 间接安装。

## 12. 修改范围

核心文件：

- 新增 `codesense/llm/schema.py`：Pydantic 模型、enum、validator 和 response format 生成；
- 修改 `codesense/llm/compiler.py`：Prompt、结构化请求、拒绝处理和类型化解析；
- 新增 `codesense/ql/term_resolution.py`：exact + expansion 的统一解析；
- 修改 `codesense/ql/satisfiers/base.py`：复用统一 surface/posting 解析；
- 修改 `codesense/ql/compile/build.py`、`cost.py`、`partition.py`、`validate.py`：grounding-aware
  统计；
- 修改 `codesense/ql/compile/spec.py`：通用 target enum 映射和 result relation IR；
- 修改 `codesense/ql/compile/plan.py`、`planner.py`、`emit.py`：执行和输出
  `ResolveResultRelation`；
- 修改 `codesense/search.py`：代表性词表、类型化 understanding 和多 target 后置条件；
- 修改 `codesense/project.py`、`codesense/cli.py`：词表上限与 min_df 参数流水线；
- 修改 `pyproject.toml`：直接声明 Pydantic；
- 功能完整实现后更新 `CHANGELOG.md`。

不修改 Frag、Element、Hit、graph schema、三条 route 的控制结构或公共执行入口。

## 13. 测试与验收

### 13.1 Schema 与 HTTP 边界

- JSON Schema 的所有对象都生成 `additionalProperties: false`；
- 请求包含 `response_format.type=json_schema`、固定 name 和 `strict=true`；
- 合法响应解析为 `QueryUnderstandingResult`；
- 缺字段、额外字段、非法 source/target/edge、越界 weight、未知 relation unit 被拒绝；
- target、relation 和 annotations 的空数组合法；
- result endpoint 的 unit 必须为 null，unit endpoint 必须引用已有 unit；
- 两个 result endpoint、同一 relation 的相同 unit，以及第二个 result-bound relation 被拒绝；
- refusal、缺 content、HTTP 错误和 Pydantic ValidationError 返回 None。

### 13.2 词表与 grounding

- Prompt 只包含 `df >= vocab_min_df` 且不超过 vocab_size 的 `term:df`；
- Prompt 明确允许输出样本外 term；
- 词表外 `buffer` 可通过 expansion 命中项目内 `buf`；
- exact 与 expansion 指向同一 surface 时不重复 posting/score；
- grounded term 同样参与 build 可执行性、kind/field 推断、partition、group cohesion、relation lift
  和 cost estimate；
- 不可接地的单个 term 只产生 note，全部不可接地才降级。

### 13.3 Term IR

- literal/synonym/derived、weight 和 reason 完整进入 QL `Term`；
- model unit 转换为 group 后仍经过 cohesion validation；
- relation 转换后仍经过 edge lift validation；
- result-bound relation 不做未知端点 lift，而是只保留真实 edge 投影得到的候选；
- Evidence 能显示 canonical term 到项目 surface 的接地链路。

### 13.4 Target

- 每个抽象 target 正确映射为具体 Element kinds；
- `targets=["class", "interface"]` 使用并集过滤；
- `targets=["file", "method"]` 同时保留 method 和投影得到的 file，并合并重复 Evidence；
- target enforcement 位于公共 limit 之前；
- 显式 Project.search target 高于模型 target；
- 空 target 保持默认排除 file；
- planned 下游失败时保留已解析 target 并交给 lexical fallback。

### 13.5 Result relation

- `$result --references--> page_request` 从 PageRequest unit 反向投影到真实引用方；
- `factory --calls--> $result` 从 factory unit 正向投影到调用目标；
- result relation 在 intent、target enforcement 和公共 limit 之前执行；
- 没有对应图边时返回空结果，而不是退回 PageRequest 声明；
- unit-to-unit relation 的现有 boost、验证和发射脚本保持回归一致；
- `to_script()` 产生的 result projection 与 `Plan.run()` 返回相同 symbol 集合和 Evidence 来源。

### 13.6 完成门禁

在 `codesearch` conda 环境运行：

```text
pytest
ruff check .
ruff format --check .
git diff --check
```

并执行一次真实 planned 查询，确认终端 trace 依次出现 understand、grounding-aware spec、plan、
execution、target enforcement 和最终结果，同时验证 `trace=False` 保持静默。
