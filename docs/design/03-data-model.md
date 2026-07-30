# 03 数据模型

算子之间流动的东西只有一种：**代码片段（Frag）**。

这是本设计里赌得最大的一个决定，先说理由。

## 为什么是片段，不是集合

朴素做法是让算子在「元素集」上运算，图算子额外产出「路径集」。
两套类型，中间靠 `endpoints` 转换。

问题在于：**答案本来就不是一堆孤立元素。**

```
BlockDevice.write ──calls──► AsyncWriter.submit ──calls──► BatchPool.acquire
   命中 io、disk                命中 perf(async)              命中 perf(batch)
```

用户要的是这**一整块**——三个节点加两条边，加上「谁命中了哪个单元」。
把它拍成 `[write, submit, acquire]` 就丢掉了使它成为答案的结构；
拍成三条路径又会在节点复用时重复计数。

所以核心类型是**带标注的代码子图**：

```python
class Frag:
    """代码库的一个片段：一组节点、它们之间的边、以及每个节点的证据。"""
    nodes: Mapping[int, Element]              # symbol_id -> Element
    edges: Mapping[EdgeKey, Edge]             # 去重后的边
    evidence: Mapping[int, Evidence]          # symbol_id -> 它为什么在这里
    witnesses: tuple[Path, ...] = ()          # 产生这个片段的路径（可选）
```

- `match` 返回**只有节点、没有边**的 Frag
- `hop` 返回节点 + 边 + 路径见证的 Frag
- 集合运算按节点做，边随节点保留
- 「取路径起点」变成一次投影，不再是跨类型的必经通道

**`witnesses` 为什么单独留一份。** 只保留边会丢掉路径的多重性——
A 到 B 有三条不同路径和只有一条，是不同强度的信号，而边集合把它们合并了。
路径见证是可选的：不需要时不物化，需要排序或展示时再用。

> **这是个赌注。** 更保守的做法是 ElementSet / PathSet 两套类型（初稿如此）。
> 收益是概念少一半、答案形态天然正确；风险是简单查询也要背着图结构。
> 判断依据：真实查询里「只要一个元素列表」的场景有多少。**需要用真实查询验证。**

## Element

```python
@dataclass(frozen=True)
class Element:
    symbol_id: int
    name: str
    kind: str            # function / method / class / field / file / module / annotation
    file: str
    span: tuple[int, int]
    signature: str = ""
    container: str = ""
    language: str = ""
    doc: str = ""
    modifiers: frozenset[str] = frozenset()   # static / abstract / public / async ...
```

`frozen=True`：元素在多个算子间流转，任何原地修改都会让上游拿到被篡改的数据。
要附加信息就进证据。

`modifiers` 是新加的——`async`、`abstract`、`static` 本身就是查询条件
（「异步的写盘函数」），塞进 `signature` 里再用正则抠出来是浪费。

## Edge：完整的边类型

不受索引现状限制，查询需要什么边就定义什么边：

| kind | 含义 | 典型查询 |
|---|---|---|
| `calls` | A 调用 B | 「谁调用了这个函数」 |
| `implements` | A 实现 / 重写 B | 「这个接口有哪些实现」 |
| `contains` | A 结构上包含 B | 「这个类里有哪些方法」 |
| `imports` | A 依赖 B（模块级） | 「谁依赖了这个模块」 |
| `reads` / `writes` | A 读 / 写字段 B | **「谁修改了这个状态」** |
| `flows_to` | 值从 A 流到 B（def-use） | **「这个参数的值从哪来」** |
| `instantiates` | A 创建 B 的实例 | 「谁在造这个对象」 |
| `throws` | A 抛出 B | 「这个异常从哪来」 |
| `annotated_by` | A 被注解 B 标记 | **「所有 @Transactional 的方法」** |

后三行是初稿因为「现在图里没有」而回避的，但它们承载的信号很强：

- **`flows_to`** 让「数据流」类查询成为可能。这类查询现在完全答不了，
  而它恰恰是理解代码时最常问的。
- **`annotated_by`** 在 Java/Spring 项目里几乎是最强的语义信号——
  `@RestController` 直接告诉你这是 HTTP 入口，比任何关键词都准。
  把注解当成边而不是字符串，是因为它天然是「元素指向元素」的关系。
- **`reads`/`writes`** 区分读写，「谁修改了这个字段」和「谁读了它」
  是完全不同的问题。

```python
@dataclass(frozen=True)
class Edge:
    source_id: int
    target_id: int
    kind: str
    site: tuple[int, int] | None = None   # 发生位置（行、列）
    confidence: float = 1.0
    provenance: str = ""                  # lsp / codeql / ast / heuristic
```

`confidence` 与 `provenance` 是必需的，不是装饰：动态分派、反射、
接口多实现都会让边带不确定性，查询时要能按置信度过滤，
出结果时要能说清这条边是怎么来的。

## Path

```python
@dataclass(frozen=True)
class Path:
    nodes: tuple[int, ...]        # symbol_id 序列
    edges: tuple[Edge, ...]
    def __len__(self) -> int: return len(self.edges)
```

路径只存 id，元素本体在 `Frag.nodes` 里——避免同一个元素在几百条路径里
重复出现几百份。

## Evidence

回答一个问题：**这个元素为什么在结果里？**

```python
@dataclass(frozen=True)
class UnitHit:
    unit: str                # "performance"
    signal: str              # lexical | annotation | semantic | structural | graph
    detail: str              # 命中的词 / 注解名 / 相似度来源
    field: str = ""          # name | signature | container | doc | body
    score: float = 1.0
    span: tuple[int, int] | None = None


@dataclass(frozen=True)
class Evidence:
    unit_hits: tuple[UnitHit, ...] = ()
    verdicts: tuple[Verdict, ...] = ()        # intent 之类的判定，含理由
    scores: Mapping[str, float] = ...
```

`signal` 字段是相对初稿的关键变化：单元可以由**多种信号**满足，
不只是词法命中（[04](04-query-unit.md)）。

三条约束：

1. **只追加，不覆盖。** 可解释性来自完整因果链。
2. **不参与相等判断。** 片段的同一性只看节点与边，否则 `a | a ≠ a`。
3. **必须可序列化。** 它要落盘供人事后查。

## 片段代数

```python
a | b     # 并：节点并、边并、证据合并
a & b     # 交：节点交；边保留两端都在的；证据合并
a - b     # 差：去掉 b 的节点及其关联边；保留 a 的证据
```

**交集的证据必须合并**——一个元素同时满足两边，两边的理由都得留。
这条最容易在实现时漏掉，然后就出现「结果里有个元素但说不出它为什么在这儿」。

## 投影

```python
f.roots()      # 出度为 0 的节点（路径起点）
f.leaves()     # 入度为 0
f.only_nodes() # 丢掉边，退化成纯节点集
f.induced(ns)  # 取子集诱导的子图
```

`only_nodes()` 是逃生舱：**当片段结构反而碍事时**，一行退回集合语义。
它的存在也是上面那个赌注的对冲——如果实践中大部分地方都在调它，
说明片段统一是过度设计，该退回两套类型。

下一篇：[04 Query Unit](04-query-unit.md)。
