# evaluation/

研究脚手架：trace 适配、query mining、指标、评测编排和实验归档。

当前 query mining 只读取严格满足 `resolved == 1` 的 Open-SWE-Traces Java 记录。一条
trace 最多调用一次 LLM，并产生一条行为、职责、状态变化或失效机制导向的英语语义 query。
模型输入由完整原始 issue 和每个搜索事件的前一条、当前、后一条 assistant/tool 事件组成；
reference patch 不进入 prompt。

输出使用扁平 schema：顶层 `query` 是传给 `Project.search()` 的唯一查询，顶层 `answer`
来自 reference patch 中被修改的既有生产 Java 文件和可确定函数。新增、删除和测试文件不进入
主答案；函数无法确定时仍保留文件，并令 `functions` 为空。

## 生成语义 query

在 `scripts/mine_trace_queries.py` 顶部填写 `INPUT`、`OUTPUT` 和 `DRY_RUN`。先将
`DRY_RUN = True`，检查 LLM 将看到的 prompt：

```bash
conda run -n codesearch python scripts/mine_trace_queries.py
```

确认 prompt 后，将 `DRY_RUN` 改为 `False` 并设置 `CODESENSE_API_KEY`，即可生成最终
`codesense-semantic-query.jsonl`。缺少有效 patch gold、没有搜索事件、模型返回无效，或 query
退化为文件名、符号名和直接引用关系查找时，该 trace 会按明确的 skip reason 跳过。

每条输出记录的核心结构如下：

```json
{
  "query_id": "trace-1",
  "repo": "owner/repo",
  "issue_statement": "...",
  "query": "Find the logic that can leave navigation state inconsistent ...",
  "answer": [
    {"file": "src/main/java/Navigation.java", "functions": ["afterCursor"]}
  ],
  "source_event_indices": [7, 8, 9],
  "strategy": "semantic-generated",
  "source_events": [],
  "provenance": {"prompt_version": "semantic-query-v1", "query_reason": "..."}
}
```

需要人工审阅 query、patch gold 和原始 trace 时，修改 `evaluation/query_viewer.py` 顶部的
`INPUT`，然后启动动态 JSONL viewer：

```bash
conda run -n codesearch python evaluation/query_viewer.py
```

页面默认监听 `127.0.0.1:8766`，浏览器每两秒重新读取一次 JSONL，因此 query 文件变化后
不需要重新生成静态 HTML。

## 批量搜索评测

`scripts/evaluation.py` 对 query JSONL 中每条记录的顶层 `query` 执行一次评测，并按照
`ROUTES` 分别运行 `lexical`、`planned`、`codegen` 中选定的搜索路径。每条路径保留 Top 20
结果并计算文件级 Precision/Recall；存在函数 gold 时还会计算函数级 Precision/Recall。
如果 requested route 降级为其他 route，该结果会记录为错误，不计入 requested route 指标。

运行前直接编辑脚本顶部的 `BENCHMARK`、`PROJECT_PATHS`、`ROUTES` 和模型参数，然后执行：

```bash
conda run -n codesearch python scripts/evaluation.py
```

默认会在 `127.0.0.1:8765` 启动只读的实时结果页，并只在终端打印访问地址，不会自动
打开浏览器。手动打开该地址后，每完成一条 query 就会追加一张结果卡：绿色表示命中的
gold 文件或函数，红色表示未命中的 gold，灰色表示额外搜索结果；页面同时显示每个 route
的文件级和函数级 Precision/Recall。所有卡片都保留在同一页中，评测结束时展示汇总。
这个轻量页面只在评测进程运行期间提供；如不需要，可将脚本顶部的 `VIEWER_ENABLED` 改为
`False`，端口可通过 `VIEWER_PORT` 调整。viewer 启动、推送或浏览器断连不会中止评测，
最终 JSON 报告格式不受影响。

项目路径优先使用 `PROJECT_PATHS`；未映射的仓库会在 `evaluation/projects/` 中查找，
不存在则从 GitHub 浅克隆最新默认分支。CodeSense 索引保存在
`evaluation/projects/.indexes/`，项目源码、索引和评测输出都不提交到版本库。

## 为什么和 `codesense/` 分开

```
codesense/     核心功能实现  →  研究要做的那件事本身：给一个查询，返回代码元素
evaluation/    研究脚手架    →  为了验证它而搭的架子：算分、跑批、归档
```

依赖方向必须单向：

```
evaluation  ──依赖──>  codesense          ✅
codesense   ──依赖──>  evaluation         ❌
```

`pyproject.toml` 里 `include = ["codesense*"]`，所以 `pip install` 出来的只有核心，
不含本目录。等哪天核心要单独发出去给别人用，别人要的是检索能力，
不是检索能力外加半套评测设施。

反向依赖是悄悄长出来的：某天你在 `codesense/filters/` 里想用一下这里的某个小工具，
import 一下很方便，当时也确实能跑，等半年后要拆才发现核心拖着整个评测层。
**现在拦住比那时候拆便宜得多。**

## 建议放什么

| 文件 | 内容 |
|---|---|
| `models.py` | 评测领域的数据（`EvalReport` 之类），**不要**塞进 `codesense/` |
| `metrics.py` | 指标。代码检索一般用 MRR / MAP / nDCG@k / Recall@k |
| `harness.py` | 读 `data/` 里的标注，对着检索结果算分 |
| `experiment.py` | 跑一次实验并把配置/环境/结果/评分归档到 `runs/` |

指标别写成一排散函数——它们是典型的「有多种做法」，
按 ARCHITECTURE.md 的规则先定一个 ABC 再写实现，之后加指标不用改调用方。

## 归档要有哪四样

跑实验不要在终端里手敲一串参数跑完就完事，三个月后你不会记得当时改过什么。
每次运行往 `runs/<时间戳>-<名字>/` 至少写这四样：

| 文件 | 内容 |
|---|---|
| `config.yaml` | 配置**快照**，不是「用了 default.yaml」——那个文件下周就会被改 |
| `environment.json` | git commit、**有没有未提交的改动**、Python 版本、机器名、时间 |
| `result.json` / `report.txt` | 结果，机器读的和人读的各一份 |
| `metrics.json` | 分数及分子分母 |

`runs/` 已在 `.gitignore` 里，产物不进版本库；`experiments/` 里的配置和结论要进。
