# `_cohere` 结构邻近加权修复设计

## 背景与目标

`codesense.search._cohere()` 期望把位于强命中附近的候选节点乘以 `1.6`，但当前实现只是把节点 ID 按临时加权值排序后传给 `Frag.induced()`。`Frag.induced()` 会把输入转成集合，最终 `_rank()` 也会重新按 Evidence 中的原始分数排序，因此结构加权没有生效。

本次修复的目标是：候选节点若位于前 `seeds` 个强命中的 `calls` 或 `contains` 一至两跳邻域内，则其最终分数为原分数的 `1 + boost` 倍；默认 `boost=0.6`，即乘以 `1.6`。候选集合、原始词汇证据、边和路径均保持不变。

## 方案比较

### 方案一：追加结构 Evidence（采用）

为满足条件的节点追加一条 `structural` 类型的 `UnitHit`，分数为该节点原分数的 `boost` 倍。`score_of()` 会把它与原分数相加，因此得到：

```text
原分数 + 原分数 × boost = 原分数 × (1 + boost)
```

优点是最终排序、`Hit.score` 和 `result.explain()` 使用同一份可解释数据，不需要改变 `Frag` 或 `SearchResult` 的公共接口。

### 方案二：让 `_rank()` 额外接收 boosted 集合

该方案只在最终排序阶段临时乘权。它需要改变 `_cohere()` 的返回类型以及 `_rank()` 的接口，而且 `Frag`、`score_of()` 和结果 Evidence 仍不知道为什么发生了加权，容易出现展示分数与内部分数不一致。

### 方案三：依赖 `Frag.nodes` 的迭代顺序

该方案继续使用排序后的节点映射表示排名。它与 `Frag` 的集合语义冲突，任何 `induced`、交并差操作或最终重新排序都会丢失顺序，因此不采用。

## 详细设计

保留现有邻域计算：

1. `top(frag, seeds)` 选出原始分数最高的种子。
2. `reach(..., edge=["calls", "contains"], direction="any", hops=(1, 2))` 查找一至两跳邻域。
3. 邻域节点与原候选节点取交集，保证结构关系只加权已有词汇依据的候选，不引入新候选。

对每个满足条件的节点：

1. 在修改 Evidence 前读取 `base_score = score_of(frag, symbol_id)`。
2. 创建 `UnitHit`：
   - `unit="coherence"`
   - `signal="structural"`
   - `field="graph"`
   - `detail` 说明它位于高分种子的 `calls/contains` 一至两跳邻域内
   - `score=base_score * boost`
3. 用 `Evidence.merge()` 追加证据，保留原始命中。
4. 构造新的 `Frag`，复用原 nodes、edges 和 witnesses，只替换更新后的 Evidence。

不满足条件的节点完全不变。没有邻近候选时直接返回原 `Frag`。

## 测试设计

新增一个不依赖 LLM 和磁盘索引的单元测试，构造三个带原始 Evidence 的节点：

- 种子节点：原分数 `0.9`。
- 邻近候选：原分数 `0.7`，通过一条 `calls` 边连接到种子。
- 非邻近候选：原分数 `0.8`，没有相关边。

使用 `seeds=1`、`boost=0.6` 后验证：

- 邻近候选分数为 `0.7 × 1.6 = 1.12`。
- 种子和非邻近候选仍分别为 `0.9`、`0.8`。
- 最终排名为邻近候选、种子、非邻近候选。
- 邻近候选包含 `structural`/`graph` Evidence。
- 节点集合没有增加或减少。

测试必须先在旧实现上因邻近候选仍为 `0.7` 而失败，再实施生产代码修改。完成后运行目标测试、全部 `tests/unit/test_search.py`、Ruff、格式检查和完整 pytest。

## 非目标

- 不改变 `reach()` 的遍历规则。
- 不给仅存在于图邻域、但没有原始检索依据的节点新增候选资格。
- 不修改 planned/codegen 的独立 Cohere/Boost 状态模型。
- 不调整 `seeds=20`、`boost=0.6` 或图边类型的默认值。
