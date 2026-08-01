# 架构约定

> **重写进行中。** 现役实现是 `codesense/ql/`（按 `docs/design/` 从头写）；
> 重写前那套 SemCon → SemQL → 三执行器已归档到 `legacy/`，不参与构建与测试。
> 本文档描述的多数内容属于归档实现，正在逐步更新。

> 这份文档解释 CodeSense **为什么这样分层**，以及每条规则拦住了哪种常见写法。
> 通用写法规范见组内 [DEV-COOKBOOK](https://github.com/FDSE-IntelliDev/DEV-COOKBOOK)，
> 这里只写与本项目结构相关的部分。

---

## 一张图

系统分两段。离线把源码变成索引，在线把自然语言变成检索条件再执行。

```
【离线索引】  目标代码库
     │
     ↓  parsers/            按语言解析，产出符号表与依赖图
     ↓  indexing/code_parser.py
     ├─ indexing/ngram_split.py    符号名 → 子词 ngram 索引
     ├─ indexing/invert_index.py   标识符 → 缩写子词倒排索引
     └─ indexing/codegraph/        落成 SQLite 代码图库（LSP 路线）
        codeql/                    同一份 schema 的 CodeQL 路线，用于对照
     │
     ↓
   output/<project>/  symbols_index.json / ngramed_symbol.json / invert_index.json / codegraph.sqlite


【在线查询】  自然语言查询
     │
     ↓  query/llm_semCon_extractor.py     LLM 抽出三类原子条件 SemCon
     │
     ↓  query/planners/                   每类条件各自编译成独立执行计划
     ├─ SurfacePlanner   → surface_semql.json
     ├─ RelationPlanner  → relation_semql.json
     └─ IntentionPlanner → intention_semql.json
     │
     ↓  executors/                        按计划执行
     ├─ surface_executor    倒排索引 + 缩写扩展召回，四层集合运算
     ├─ relation_executor   caller/callee、图角色、路径约束
     └─ intention_executor  语义意图判定
     │
     ↓  filters/                          精排：类型 / 聚类 / embedding / 调用关系
     │
   output/<project>/query_<id>/           每次查询独立一个目录
```

三类条件的分工是整个系统的核心抽象，来龙去脉见
[docs/decisions/0001-semcon-three-condition-split.md](docs/decisions/0001-semcon-three-condition-split.md)。

## 各层职责

> 下表中除入口层外，路径都省略了 `codesense/` 前缀。

| 层 | 位置 | 负责 | 禁止 |
|---|---|---|---|
| 配置 | 构造函数注入（`EvalContext` / `LlmConfig`） | 提供参数 | 写业务逻辑 |
| 数据 | `query/plan_models.py`、`indexing/codegraph/schema.py` | 定义领域概念 | 写业务逻辑 |
| 解析 | `parsers/` | 源码 → 结构化符号 | 知道谁在检索 |
| 索引 | `indexing/`、`codeql/` | 建索引、落库 | 参与在线检索决策 |
| 计划 | `query/planners/` | SemCon → 执行计划 | 执行检索 |
| 执行 | `executors/` | 按计划取候选 | 解析 SemCon 原始字段 |
| 精排 | `filters/` | 缩小候选集 | 扩大候选集 |
| 入口 | `codesense/__main__.py`、`scripts/` | 解析参数、调用 | **写任何业务逻辑** |

最后一行值得特别留意。把逻辑直接写进脚本当下最省事，但那段代码从此没法被测试、
也没法被别处复用。判断标准是——**如果这段代码值得测试，它就不该待在 `scripts/` 里。**

## Planner 与 Executor 的契约

这是最容易被违反的一条边界：

> **Planner 解析 SemCon 原始字段，Executor 只读计划。**

三个 Planner 各自只认自己那类条件，互不干涉：

```text
SurfaceCon   -> SurfacePlanner   -> surface_semql.json
RelationCon  -> RelationPlanner  -> relation_semql.json
IntentionCon -> IntentionPlanner -> intention_semql.json
```

计划是 `query/plan_models.py` 里 `frozen=True` 的 dataclass，序列化成 JSON 落盘。
Executor 读回来直接执行，**不要**再去碰原始 SemCon——一旦 Executor 开始解析
SemCon 字段，改 schema 就得同时改 planner 和 executor 两处，抽象就漏了。

## 核心与脚手架的边界

顶层是两个平级的包，不是一个：

```
codesense/     核心功能实现  →  研究要做的那件事本身
evaluation/    研究脚手架    →  指标、评测编排、实验归档，围绕核心转
```

`pyproject.toml` 里 `include = ["codesense*"]`，所以 `pip install` 出来的只有核心。

依赖方向必须单向：

```
evaluation  ──依赖──>  codesense          ✅
codesense   ──依赖──>  evaluation         ❌
```

同样的分法适用于别的东西：答辩 PPT（`slides/`）、入口脚本（`scripts/`）、
数据与标注（`data/`）——都属于「围绕核心的脚手架」，各自一个顶层目录，
不要塞进核心包。

---

## 几条硬约定

### 1. 参数进配置，不写死在代码里

阈值、路径、模型名、超参全部作为**流水线参数**：命令行 → 构造函数注入，不落配置文件。

理由很实际：跑实验要对比不同参数，参数写在代码里就意味着每次对比都要改代码，
改完还得记得改回去——这是实验结果对不上的头号原因。

这个仓库吃过这个亏：`definition.py` 里写死了 `/Users/bytedance/...`，
`main.py` 甚至把 `parse_args` 的参数写成了固定列表，命令行传什么都没用。

### 2. 密钥只从环境变量读

`codesense/config.py` 里没有 `api_key` 字段，只有 `CODESENSE_API_KEY` 环境变量。
配置文件会进版本库，密钥不能进。

> ⚠️ 历史遗留：`definition.py` 里曾经明文写着 dashscope 的 key，
> 已经进了 git 历史。代码里已经清掉，但**那个 key 必须去控制台吊销重发**。

### 3. 模块顶层不执行任何逻辑

模块顶层只有定义，没有执行。`import` 一个模块不该有任何副作用——
不该读盘、不该起进程、不该 print。

`config.py` 用 PEP 562 的模块级 `__getattr__` 就是为这条：
旧代码 `from codesense.config import PROJECT_OUTPUT_DIR` 照常能用，
但 YAML 是第一次访问时才读的，`import codesense.config` 本身不碰磁盘。

### 4. 数据用 dataclass，不要用 dict 传

跨模块流动的数据结构写成 `frozen=True` 的 dataclass。
拼错 key 的 dict 不会报错，只会返回 `None`，然后在很远的地方炸。

要「改」一个不可变对象，用 `dataclasses.replace(obj, field=新值)` 造新的。

### 5. 什么时候该拆文件

| 信号 | 处理 |
|---|---|
| 一个类超过 200 行 | 大概率承担了多个职责，拆 |
| 一个函数超过 50 行 | 提取子函数 |
| 一个文件超过 500 行 | 按职责拆成子模块 |
| 改一个功能要同时改三个文件 | 抽象错了，重新划分边界 |

按这把尺子，当前这几个文件已经超标，是下一步该拆的：

| 文件 | 行数 |
|---|---|
| `codesense/executors/surface_executor.py` | 982 |
| `codesense/expansion/abbreviate.py` | 964 |
| `codesense/filters/relation_filter.py` | 765 |
| `codesense/filters/embedding_filter.py` | 738 |

---

## 待办：这轮整理没做完的事

这次只搬了位置、改了导入、补了配套设施，**没碰函数内部逻辑**。以下是明确留下的债：

### 1. 配置的过渡层要拆掉

`config.py` 底部的 `_LEGACY_NAMES` 是给 40 处旧调用点留的兼容层。
它们现在写的是 `from codesense.config import PROJECT_OUTPUT_DIR`，
应该逐步改成 `load_config()` 显式传参。迁完就能把整块 `__getattr__` 删掉。

一次性改要动所有函数签名，所以按模块分批迁——改到哪个模块顺手迁哪个。

### 2. 可替换的组件还没有 ABC 和注册表

`filters/` 下有四个过滤器、`executors/` 下有三个执行器、`parsers/` 下有五个解析器，
它们各自是「同一件事的不同做法」，但现在没有共同接口，选哪个靠调用方写死。

按 DEV-COOKBOOK 的规则 3 和 4，这类东西应该：定一个 ABC，用注册表选实现，
再配一套契约测试遍历所有注册实现自动检查。这是下一轮该做的结构性改动。

### 3. ruff 的豁免名单要逐条还

`pyproject.toml` 里给老模块挂了一批具体规则号的豁免，
其中 UP006/UP035/UP045 共 1355 条是 typing 写法现代化，
`ruff check --fix --unsafe-fixes` 能一次修完——建议单独开一个 PR，
别和结构调整混在一起。改到哪个模块就顺手清掉它那条豁免。

### 4. 在线 pipeline 目前是断的

`codesense/__main__.py` 里 Surface 和 Relation Executor 两步被注释掉了，
只有 Intention Executor 在跑。这是调试时留下的状态，整理时原样保留没动，
恢复与否由你决定。
