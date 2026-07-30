# 03 数据模型

算子之间流动的东西只有三种：**元素集**、**路径集**、**证据**。
先把它们定死，算子签名才有意义。

## Element：代码元素

一个符号。字段沿用现在 `symbols_index.json` 的 schema，不另起炉灶：

```python
@dataclass(frozen=True)
class Element:
    symbol_id: int
    name: str
    type: str            # function / method / class / variable / file ...
    file: str
    start_line: int
    end_line: int
    signature: str = ""
    container: str = ""  # 所属类 / 包
    language: str = ""
    doc: str = ""
```

`frozen=True` 不是洁癖：元素会在多个算子之间流转，任何一处原地修改都会让
上游拿到被篡改的数据，而这类 bug 极难定位。要附加信息就放到证据里（见下）。

## ElementSet：元素集

```python
class ElementSet:
    elements: Mapping[int, Element]           # symbol_id -> Element
    evidence: Mapping[int, ElementEvidence]   # symbol_id -> 它为什么在这里
```

**集合语义按 `symbol_id` 去重。** 支持 `&`（交）、`|`（并）、`-`（差）。

集合运算时证据的处理规则：

| 运算 | 证据 |
|---|---|
| `a \| b` | 合并双方证据 |
| `a & b` | 合并双方证据（一个元素同时满足两边，两边的理由都要留） |
| `a - b` | 保留 `a` 的证据 |

> 这条规则很容易在实现时被忽略，然后就出现「结果里有个元素，但说不出它
> 为什么在这儿」。交集尤其容易丢——它是 unit 组合的主要形式。

## Path：路径

`hop` 的产物。一条路径是元素与边交替的序列：

```python
@dataclass(frozen=True)
class Edge:
    source_id: int
    target_id: int
    kind: str            # calls / implements / contains / imports
    call_line: int | None = None
    confidence: float = 1.0
    provenance: str = ""  # java_lsp_call_hierarchy / codeql / ...


@dataclass(frozen=True)
class Path:
    nodes: tuple[Element, ...]   # len >= 1
    edges: tuple[Edge, ...]      # len == len(nodes) - 1

    @property
    def source(self) -> Element: return self.nodes[0]
    @property
    def target(self) -> Element: return self.nodes[-1]
    def __len__(self) -> int: return len(self.edges)   # 跳数
```

**为什么保留完整路径而不只是两端。**

1. 路径是给人看的证据。「A 调 B 调 C」比「A 和 C 有关系」有用得多。
2. 中间节点本身可能是答案。`io → perf` 的路径上，那个 `AsyncWriter.submit`
   往往才是用户真正想看的东西。
3. 后续算子可能要对路径中段加条件（「路径不经过测试代码」）。

代价是内存：路径数随跳数指数增长。控制手段见 [06](06-script-and-execution.md)。

> 当前 `RelationGraphStore.reachable()` 返回的是可达**元素集**，路径信息在
> 遍历过程中就丢了。要支持 `hop` 需要扩展它，见 [07](07-mapping-to-current.md)。

## PathSet：路径集

```python
class PathSet:
    paths: Sequence[Path]

    def endpoints(self, side: Literal["source", "target", "both"]) -> ElementSet: ...
    def nodes(self) -> ElementSet: ...      # 含中间节点
```

`endpoints` 是**路径集回到元素集的唯一通道**。没有它，`hop` 的产物就没法
接回其它算子——这是类型系统里最关键的一条边。

## Evidence：证据

证据回答一个问题：**这个元素为什么在结果里？**

```python
@dataclass(frozen=True)
class UnitHit:
    unit: str            # "io"
    term: str            # "flush"
    source: str          # literal | synonym | derived  （见 04）
    field: str           # name | signature | container | doc | body
    span: tuple[int, int] | None = None   # 命中位置，用于高亮


@dataclass(frozen=True)
class ElementEvidence:
    unit_hits: tuple[UnitHit, ...] = ()
    path_refs: tuple[int, ...] = ()        # 指向 PathSet 里的下标
    intent_verdicts: tuple[IntentVerdict, ...] = ()
    scores: Mapping[str, float] = field(default_factory=dict)
```

三条约束：

1. **每个算子只往证据里追加，不覆盖。** 结果的可解释性来自完整的因果链。
2. **证据不参与集合的相等判断。** 集合语义只看 `symbol_id`，否则
   `a | a` 会因为证据合并而不等于 `a`。
3. **证据必须能序列化成 JSON。** 它要落进产物文件供人事后查。

## 类型流转

```
              match ──────────────► ElementSet
                                        │
             of_type / in_file ◄─────────┤ (ElementSet -> ElementSet)
             intent            ◄─────────┤
             degree            ◄─────────┤
             expand            ◄─────────┤
                                        │
                    hop(A, B) ──────────► PathSet
                                        │
                        endpoints ◄──────┘ (PathSet -> ElementSet)
```

**只有 `hop` 产出 `PathSet`，只有 `endpoints`/`nodes` 能从 `PathSet` 回来。**
其余算子都是 `ElementSet -> ElementSet`，因此可以自由串联。

这个约束是刻意的：它让脚本的类型一眼可读，也让实现时不必处理
「某个算子既接受元素集又接受路径集」的分支。

下一篇：[04 Query Unit](04-query-unit.md)。
