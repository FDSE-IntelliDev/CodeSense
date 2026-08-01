# CodeSense: Intent-Aware Code Search Beyond Keywords

> **重写进行中。** 现役实现是 `codesense/ql/`（按 `docs/design/` 从头写）；
> 重写前那套 SemCon → SemQL → 三执行器已归档到 `legacy/`，不参与构建与测试。
> 本文档描述的多数内容属于归档实现，正在逐步更新。

基于语义查询语言（SemQL）的代码搜索系统。给定自然语言查询，通过 LLM 提取结构化
语义条件，经由倒排索引 + 缩写扩展 + embedding 匹配生成候选集，再通过类型过滤、
聚类过滤、embedding 过滤、调用关系过滤等多阶段精排，返回最相关的代码元素。

---

## 先跑起来

```bash
conda activate codesearch
pip install -e ".[dev]"

cp .env.example .env && $EDITOR .env     # 填 CODESENSE_API_KEY
# 参数通过命令行传给 scripts/ 下的脚本，不需要改配置文件

pytest                                   # 70 个测试，应该全绿
python -m codesense --help
```

离线建索引，再在线查一次：

```bash
python -m codesense --init                                  # 建索引（慢，几分钟起）
python -m codesense --query "Find the entry function that handles user login authentication"
```

> ⚠️ 当前在线 pipeline 只有 Intention Executor 是打开的，Surface 和 Relation
> 两步在 `codesense/__main__.py` 里被注释掉了。这是调试时留下的状态，
> 见 [ARCHITECTURE.md 的待办](ARCHITECTURE.md#待办这轮整理没做完的事)。

---

## 阅读顺序

1. **[ARCHITECTURE.md](ARCHITECTURE.md)** —— 离线/在线两段怎么分层、各层能做什么不能做什么。
   入组第一天读这份。
2. `codesense/query/plan_models.py` —— planner 和 executor 之间的契约都在这些 dataclass 里。
3. **[docs/search-pipeline.md](docs/search-pipeline.md)** —— 检索流程的详细设计。
4. **[docs/decisions/](docs/decisions/)** —— 想知道「为什么当初这么设计」时翻。
5. **[CONTRIBUTING.md](CONTRIBUTING.md)** —— 提交前要过哪几条。

完整文档索引见 [docs/README.md](docs/README.md)。

---

## 系统架构

### 离线索引

把源代码解析成结构化索引：

1. **代码解析**（`codesense/indexing/code_parser.py`）—— 解析源文件，提取符号表、依赖图
2. **子词分词**（`indexing/ngram_split.py`）—— 对符号名分词，构建 `子词 → [代码元素]` 索引
3. **倒排索引**（`indexing/invert_index.py`）—— 基于缩写扩展，构建 `标识符 → [缩写子词]` 倒排索引
4. **代码图库**（`indexing/codegraph/`）—— 落成 SQLite；`codesense/codeql/` 是同 schema 的
   CodeQL 路线，用于和 LSP 结果对照

### 在线查询

把自然语言转成结构化检索条件并执行：

1. **查询理解**（`codesense/query/`）—— LLM 抽出三类 SemCon 原子条件，
   三个 planner 各自编译成独立执行计划
2. **候选召回**（`codesense/executors/`）—— 倒排索引 + 缩写扩展召回，
   四层集合运算：term OR、group AND(n)、condition subtract、跨 condition 合并
3. **精排过滤**（`codesense/filters/`）—— 类型 / 聚类 / embedding / 调用关系

### 支持的语言

| 语言 | 解析方式 |
|------|---------|
| Java | `tree-sitter` + JDT.LS (LSP) |
| Python | 内置 `ast` |
| JavaScript / TypeScript | `tree-sitter` |
| C / C++ | `ctags` |

---

## 目录导览

```
├── codesense/               核心功能实现 —— 研究要做的那件事本身
│   ├── __main__.py          CLI（只解析参数，不含业务逻辑）
│   ├── config.py            配置（YAML → frozen dataclass，字段拼错立刻报错）
│   ├── indexing/            离线索引
│   │   ├── code_parser.py       源码 → 符号表 + 依赖图
│   │   ├── ngram_split.py       符号名分词与 ngram 索引
│   │   ├── invert_index.py      缩写扩展倒排索引
│   │   └── codegraph/           SQLite 代码图库（LSP 路线）
│   ├── codeql/              CodeQL 路线，产出同 schema 的库用于对照
│   ├── parsers/             多语言解析器
│   ├── dsl/                 SemCon 三类条件的 schema
│   ├── query/               查询理解
│   │   ├── llm_semCon_extractor.py   LLM 抽 SemCon
│   │   ├── planners/                 SemCon → 三份独立执行计划
│   │   └── plan_models.py       ★ planner 与 executor 的契约
│   ├── executors/           surface / relation / intention 三个执行器
│   ├── filters/             类型 / 聚类 / embedding / 调用关系过滤
│   ├── search/              倒排索引检索、全词匹配、模糊与正则
│   ├── expansion/           缩写扩展
│   ├── embedding/           term embedding 训练与查询
│   ├── tokenizer/           分词器（camel + BPE / unigram，含训好的模型）
│   └── utils/
│
├── evaluation/              研究脚手架 —— 围绕核心转，刻意不随核心发布（骨架待填）
├── experiments/             每个实验一个目录：配置 + 记录
├── tests/
│   ├── unit/                纯逻辑，不碰 IO
│   ├── integration/         端到端，标 slow
│   └── fixtures/mini_project/   测试用的最小目标代码库
│
├── legacy/                 重写前的实现（只读归档）
├── data/                    查询集、标注、prompt（大文件不进版本库）
├── scripts/                 入口脚本（只做参数解析和调用）
├── docs/                    专题文档与设计决策记录
├── output/                  索引与检索产物（gitignore）
├── runs/                    实验归档（gitignore）
└── slides/                  答辩 PPT 与素材（gitignore）
```

---

## 产物结构

离线产物落在 `output/<project_name>/`：

| 文件 | 内容 |
|---|---|
| `symbols_index.json` | 代码符号表 |
| `dependency_graph.json` | 依赖关系图 |
| `ngramed_symbol.json` | 子词 → 代码元素索引 |
| `invert_index.json` | 标识符 → 缩写子词倒排索引 |
| `codegraph.sqlite` | 代码图库 |

每次在线查询在 `output/<project_name>/query_<id>/` 下独立成目录：

| 文件 | 内容 |
|---|---|
| `query_plan.json` | 轻量清单，只记录三类子计划的位置 |
| `surface_semql.json` | keyword groups、组内 OR、group logic、匹配类型、include/exclude |
| `relation_semql.json` | caller/callee、图角色、路径及其他结构约束 |
| `intention_semql.json` | 语义 query profile 与 include/exclude 意图要求 |
| `surface_group_search_results.json` | 逐 group 的直接命中，位于集合运算之前 |
| `surface_evidence_hop_0.json` | 最终候选的 condition/group、term、matched term 与图距离证据 |

---

## SemQL 查询结构

SemQL 把自然语言查询分解为三类原子条件：

| 条件类型 | 用途 | 关键字段 |
|---------|------|---------|
| **Surface** | 字面文本匹配 | keywords, synonyms, match_kind, code_element_type, code_text |
| **Intention** | 语义功能约束 | intent (action+object), aspect, keywords |
| **Relation** | 代码结构约束 | caller, callee, graph_constraint, file_path, code_ql |

每条条件通过 `property` 字段标记为 `include` 或 `exclude`。

- Surface plan 按层级表达组合逻辑：group 内 keywords/synonyms 做 OR，同一 condition
  的 include groups 做 AND(n)，exclude groups 构造负向集合并从正向结果中减去。
  多个 condition 用 `(match_kind, code_element_types)` 作兼容键：相同取交集，不同取并集。
- Relation plan 将单个 clause 内的 file、graph role、caller、callee 约束按 AND 执行；
  多个 include clause 做 INTERSECT，多个 exclude clause 做 UNION 后减去。
  caller/callee 优先查代码图库，无法解析时回退 LSP。在线 `code_ql` 执行尚未接入，
  计划会保留该字段并在执行报告中标记为未执行。

为什么这么分，见 [docs/decisions/0001-semcon-three-condition-split.md](docs/decisions/0001-semcon-three-condition-split.md)。

---

## 外部依赖

分析 Java 调用链需要 Eclipse JDT.LS：

```bash
brew install jdtls                     # macOS
```

其他系统参考 [eclipse.jdt.ls](https://github.com/eclipse/eclipse.jdt.ls)，
装好后把路径作为命令行参数传给对应脚本。

CodeQL 对照路线默认生成独立的 `codegraph.codeql.sqlite`，不会覆盖 LSP 版本。
安装方式与完整命令见 [`codesense/codeql/README.md`](codesense/codeql/README.md)。

---

## 日常命令

```bash
pytest                          # 全部测试（跳过 slow）
pytest tests/unit               # 只跑快的
pytest -m slow                  # 需要重依赖的那些
ruff check . --fix              # 查 + 自动修
ruff format .                   # 统一格式

python -m codesense --init                       # 建索引
python -m codesense --query "..." --query_id 2   # 查询
python -m scripts.run_regex_search readahead ra  # 单步调试
```

提交前这三条要过：`ruff check .`、`ruff format --check .`、`pytest`。
详见 [CONTRIBUTING.md](CONTRIBUTING.md)。
