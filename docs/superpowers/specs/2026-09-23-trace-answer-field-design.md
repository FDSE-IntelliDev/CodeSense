# Trace Answer 字段设计

> 状态：设计已确认，待用户审阅书面规格
>
> 日期：2026-09-23
>
> 范围：为搜索过程监督 benchmark 的每条 query 增加同一 trace 共享的
> `trace_answer`，不改变现有搜索和评测逻辑

## 1. 背景与目标

当前 benchmark 会从一条 Open-SWE-Traces 轨迹中抽取多个搜索 episode，每个
episode 形成一条独立 query，并使用该次搜索结果中被 Agent 后续实际使用的位置作为
`answer`。这种 episode 级答案适合评估单次搜索，但有些 episode 只提供文件级线索，
而 Agent 的最终回答可能明确给出整个 trace 最终定位到的文件和函数。

本次修改为每条 query 增加独立字段 `trace_answer`，记录 Agent 最终回答中可确定的、
原本存在于仓库中的生产 Java 文件和函数。同一 trace 产生的所有 query 共享同一份
`trace_answer`，但 `answer`、`candidate_answers` 和 `usage_evidence` 的含义保持不变。

未来评测可以利用该字段实现跨 query 的加分或扣分策略。本次只生产和展示数据，不实现
任何评分规则，也不把 `trace_answer` 合并进当前 gold。

## 2. 数据模型

在 `PreparedQuery` 中增加带空值默认的字段：

```python
trace_answer: tuple[CodeLocation, ...] = ()
```

序列化后的 JSONL 示例：

```json
{
  "query": "Find the logic that can leave navigation state inconsistent.",
  "answer": [
    {"file": "src/main/java/PageState.java", "functions": []}
  ],
  "trace_answer": [
    {
      "file": "src/main/java/Navigation.java",
      "functions": ["afterCursor"]
    }
  ]
}
```

`trace_answer` 使用既有 `CodeLocation` schema，不新增另一套文件或函数表示。旧调用方
没有传入该字段时得到空元组，现有测试 fixture 和内部构造代码可以继续工作。

## 3. 抽取规则

抽取过程是确定性的，不增加 LLM 调用：

1. 从 trace 中查找最后一个有效 `finish` 工具调用，并读取其中的 `message`；
2. 从最终回答中识别明确提到的 Java 文件；
3. 候选文件必须能唯一匹配 reference patch 中的既有生产 Java 文件；
4. reference patch 标记为新增、删除的文件，以及测试、样例、生成代码和构建产物均不进入
   `trace_answer`；
5. 路径优先按完整仓库相对路径匹配；只有 basename 在既有生产文件中唯一时，才允许使用
   basename 匹配；
6. 函数只在能够与同一文件可靠关联且最终回答明确提及时记录；否则保留文件并使用空
   `functions`；
7. 重复文件合并，函数按首次出现顺序去重；
8. 没有 `finish`、最终回答没有可验证位置，或最终回答只提到新增文件时，返回空
   `trace_answer`。

reference patch 仅用于验证文件原本存在并排除新增文件，不把 patch 中未被 Agent 最终回答
提到的位置自动加入 `trace_answer`。因此该字段仍然表示 Agent 的最终定位结果，而不是
reference patch gold 的副本。

## 4. 数据流

`mine_queries()` 在完成 trace episode 监督构造后，对当前 `TraceCase` 只抽取一次
`trace_answer`。随后生成每个 `PreparedQuery` 时复用该不可变元组：

```text
TraceCase.events + TraceCase.answer
             |
             v
确定性抽取 trace_answer（每个 trace 一次）
             |
             +------> query 1.trace_answer
             +------> query 2.trace_answer
             +------> query N.trace_answer
```

query 生成 prompt 不包含最终回答、reference patch 或 `trace_answer`，避免答案泄漏。
某个 episode 的 LLM query 生成失败时不会影响同一 trace 其他 query 共享该字段。

## 5. 代码边界

本次预计只修改：

- `evaluation/models.py`：为 `PreparedQuery` 增加字段及 JSON 序列化；
- `evaluation/trace_search.py`：增加确定性的 trace 最终答案抽取函数，并复用现有路径、
  `finish` 和函数识别逻辑；
- `evaluation/query_mining.py`：每个 trace 抽取一次并注入所有 query；
- 对应单元测试：验证 schema、共享行为、函数关联和新增文件排除。

不修改：

- `scripts/evaluation.py` 的现有评分实现；
- CodeSense 的索引、搜索、route 或算子；
- query 生成 prompt 和语义门禁；
- `answer`、`candidate_answers`、`usage_evidence` 的构造规则。

## 6. 兼容性与失败处理

- 新字段具有空默认值，内部 Python 调用保持向后兼容；
- 新生成的 JSONL 总是输出 `trace_answer`，没有结果时为 `[]`；
- 旧 JSONL 不包含该字段时，当前 evaluator 不读取它，因此行为不变；
- 格式异常或无法唯一匹配的最终回答采取保守策略：跳过不确定位置，不中断整个 trace；
- 本次不修改 viewer；其余消费者可在需要时按可选字段读取。

## 7. 测试范围

至少覆盖以下场景：

1. `PreparedQuery.to_dict()` 正确序列化 `trace_answer`；
2. 同一 trace 的多条 query 获得相同 `trace_answer`；
3. 最终回答中的既有生产文件及其明确函数能够被抽取；
4. reference patch 中新增的 Java 文件不会进入 `trace_answer`；
5. 测试文件、歧义 basename 和无法关联的函数被忽略；
6. 仅能确定文件时输出空函数列表；
7. 缺少有效最终回答时输出空列表；
8. evaluator 的现有评分测试保持不变，证明本次没有引入评分行为。

完成实现后运行仓库要求的 `ruff check .`、`ruff format --check .` 和 `pytest`。
