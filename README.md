# CodeSearch

基于语义查询语言（SemQL）的代码搜索系统。给定自然语言查询，通过 LLM 提取结构化语义条件，经由倒排索引 + 缩写扩展 + embedding 匹配生成候选集，再通过类型过滤、聚类过滤、embedding 过滤、调用关系过滤等多阶段精排，返回最相关的代码元素。

## 系统架构

系统分为 **离线索引** 和 **在线查询** 两大阶段：

### 离线索引（Offline Indexing）

将源代码解析为结构化索引，供在线检索使用：

1. **代码解析** (`code_parser.py`) — 解析源文件，提取符号表、依赖图
   - `init/build_code_db.py` — 现有 parser + Java LSP 数据库构建链路
   - `codeQL/` — CodeQL 批量解析与同 schema 数据库构建链路，用于和 LSP 结果对比
2. **子词分词** (`ngram_split.py`) — 对符号名进行分词，构建 `子词 -> [代码元素]` 的 ngram 索引
3. **倒排索引** (`invert_index.py`) — 基于缩写扩展，构建 `标识符 -> [缩写子词]` 的倒排索引

### 在线查询（Online Search）

将自然语言查询转化为结构化检索条件并执行搜索：

1. **查询理解**
   - `query_processing/llm_keyword_extractor.py` — LLM 提取关键词、意图、目标类型
   - `query_processing/llm_semCon_extractor.py` — LLM 提取 SemCon 原子条件（surface / intention / relation）
   - `query_processing/planners/` — 将三类 SemCon 分别编译为独立执行计划
   - `query_processing/semQL_composer.py` — 迁移期保留的旧版组合 SemQL 兼容层
2. **候选召回** (`executor/surface_executor.py`)
   - 倒排索引 + 缩写扩展召回（`search/`）
   - Planner 类型过滤与四层集合执行：term OR、group AND(n)、condition subtract、跨 condition 合并
   - call scope 按无向调用链距离补齐 group coverage，并输出 `surface_evidence_hop_0.json`
   - 在执行 clause 的 identity / OR / AND(n) 前，将各 group 的直接检索结果保存到 `surface_group_search_results.json`
3. **精排过滤**
   - 聚类过滤（`filters/cluster_pipeline.py`）— 基于语义向量聚类，按簇相关性分层
   - Embedding 过滤（`filters/embedding_filter.py`）— 基于训练好的 term embedding 细粒度打分
   - 调用关系过滤（`filters/relation_filter.py`）— 基于 LSP 查询 caller/callee 约束

## 支持的语言

| 语言 | 解析方式 |
|------|---------|
| Python | 内置 `ast` |
| Java | `javalang` AST + JDT.LS (LSP) |
| JavaScript / TypeScript | `tree-sitter` |
| C / C++ | `ctags` |

## 项目结构

```
CodeSearch/
├── main.py                    # 主入口（离线 + 在线 pipeline）
├── code_parser.py             # 代码解析：提取符号表与依赖图
├── ngram_split.py             # 符号名分词 & ngram 索引构建
├── invert_index.py            # 倒排索引构建（缩写扩展）
├── definition.py              # 全局常量与配置
├── parsers/                   # 多语言代码解析器
│   ├── registry.py            # 语言注册 & 路由
│   ├── python_parser.py
│   ├── java_parser.py
│   ├── javascript_parser.py
│   ├── c_cpp_parser.py
│   ├── ctags_parser.py
│   ├── java_lsp_client.py     # Java LSP 客户端
│   ├── parallel_java_lsp_client.py
│   └── code_element_types.py  # 代码元素类型注册表
├── codeQL/                    # CodeQL 离线解析与 codegraph.codeql.sqlite 构建
│   ├── build_code_db.py
│   ├── runner.py
│   ├── transform.py
│   └── queries/java/
├── query_processing/          # 查询理解
│   ├── llm_keyword_extractor.py   # LLM 关键词提取
│   ├── llm_semCon_extractor.py    # LLM SemCon 条件提取
│   └── semQL_composer.py          # SemQL 组合器
├── DSL/                       # 查询 DSL 定义
│   ├── query_dsl.py           # 原始查询 DSL schema
│   ├── surface_con.py         # Surface 条件 schema
│   ├── intention_con.py       # Intention 条件 schema
│   └── relation_con.py        # Relation 条件 schema
├── search/                    # 候选召回
│   ├── invert_index_search.py     # 倒排索引搜索入口
│   ├── full_term_matcher.py       # 关键词 -> 缩写 -> 子词 -> 符号匹配
│   ├── fuzzy_matcher.py
│   └── regex_search.py
├── filters/                   # 精排过滤
│   ├── type_filter.py         # 代码元素类型过滤
│   ├── cluster_pipeline.py    # 语义聚类过滤
│   ├── embedding_filter.py    # Term embedding 过滤
│   └── relation_filter.py     # 调用关系过滤（LSP）
├── executor/                  # 执行器
│   ├── surface_executor.py    # Stage 1: 召回 + 类型过滤
│   └── relation_engine.py
├── embedding/                 # Term Embedding 模型
│   ├── embedding_main.py      # 统一入口（训练 / 查询）
│   ├── hybrid_term_embedding.py
│   ├── semantic_term_embedding.py
│   ├── icf_term_embedding.py
│   └── pairwise_term_reranker.py
├── expansion/                 # 缩写扩展
│   ├── abbreviate.py          # 缩写生成（前缀 / 辅音骨架 / 子序列）
│   └── NameHandler.py
├── tokenizer/                 # 分词器
│   ├── tokenizer_core.py      # 分词入口（camel + BPE / unigram）
│   ├── sentencepiece_bpe_tokenizer.py
│   └── sentencepiece_unigram_tokenizer.py
├── utils/
│   ├── file_utils.py
│   └── llm_api.py             # LLM API 调用封装
└── output/                    # 索引与搜索结果输出
```

## 安装

```bash
pip install -r requirements.txt
```

### 系统依赖 (LSP 支持)

分析 Java 项目调用链需要安装 JDT.LS：

- **macOS (Homebrew)**:
  ```bash
  brew install jdtls
  ```
- **其他系统**: 参考 [eclipse.jdt.ls](https://github.com/eclipse/eclipse.jdt.ls) 官方页面，将 `jdtls` 添加到环境变量。

### 可选系统依赖（CodeQL 对比链路）

CodeQL 版本默认生成独立的 `codegraph.codeql.sqlite`，不会覆盖 LSP 版本。
安装方式、构建模式和完整命令见 [`codeQL/README.md`](codeQL/README.md)。

## 运行

### 离线的索引构建

```bash
python main.py --project_path /path/to/project --output_dir /path/to/output
```

生成文件：
- `symbols_index.json` — 代码符号表
- `dependency_graph.json` — 依赖关系图
- `ngramed_symbol.json` — 子词 -> 代码元素索引
- `invert_index.json` — 标识符 -> 缩写子词倒排索引

### 在线查询

```bash
python main.py --query "Find the entry function that handles user login authentication"
```

每次在线查询会在 `output/<project>/query_<id>/` 下生成独立查询计划：

- `query_plan.json` — 轻量计划清单，只记录三类子计划的位置
- `surface_semql.json` — Surface keyword groups、组内 OR、group logic、匹配类型和 include/exclude 逻辑
- `relation_semql.json` — caller/callee、图角色、路径及其他结构约束
- `intention_semql.json` — 语义 query profile 和 include/exclude 意图要求
- `surface_group_search_results.json` — term OR 与类型过滤后的逐 group 直接命中，位于 clause 集合运算之前
- `surface_evidence_hop_0.json` — 最终 Surface 候选的 condition/group、term、matched term 与图距离证据

三个 Planner 只解析各自的 SemCon 字段：

```text
SurfaceCon   -> SurfacePlanner   -> surface_semql.json
RelationCon  -> RelationPlanner  -> relation_semql.json
IntentionCon -> IntentionPlanner -> intention_semql.json
```

Surface 与 Relation Executor 已分别直接读取 `surface_semql.json` 和
`relation_semql.json`；Intention Executor 仍按后续迁移顺序接入独立计划。旧组合
SemQL 只保留兼容入口，不再作为 Surface/Relation Executor 的输入。

Surface plan 按层级表达组合逻辑：group 内的 keywords/synonyms 做 OR，同一
condition 的 include groups 做 AND(n)，exclude groups 构造负向集合并从正向
结果中减去。多个 SurfaceCon condition 使用 `(match_kind, code_element_types)`
作为兼容键：兼容键相同的结果取交集，不同兼容组之间取并集。

Relation plan 将单个 clause 内的 file、graph role、caller 和 callee 约束按 AND
执行；多个 include clause 继续做 INTERSECT；多个 exclude
clause 做 UNION 后从结果中减去。caller/callee 优先查询项目代码关系数据库，无法
解析时复用 LSP fallback。在线 `code_ql` 执行尚未接入，计划会保留该字段并在执行
报告中明确标记为未执行。

## 数据结构 (Schema)

### 代码元素 (symbols_index.json)

```json
{
  "symbol_id": 1,
  "name": "YouLaiBootApplication",
  "type": "class",
  "file": "/absolute/path/to/file.java",
  "range": {
    "start_line": 15,
    "end_line": 21
  },
  "signature": "class YouLaiBootApplication",
  "language": "java",
  "doc": "包含的文档注释内容",
  "container": "com.youlai.boot"
}
```

### SemQL 查询结构

SemQL 将自然语言查询分解为三类原子条件：

| 条件类型 | 用途 | 关键字段 |
|---------|------|---------|
| **Surface** | 字面文本匹配 | keywords, synonyms, match_kind, code_element_type, code_text |
| **Intention** | 语义功能约束 | intent (action+object), aspect, keywords |
| **Relation** | 代码结构约束 | caller, callee, graph_constraint, file_path, code_ql |

每条条件通过 `property` 字段标记为 `include` 或 `exclude`。
