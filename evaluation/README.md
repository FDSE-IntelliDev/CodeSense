# evaluation/

研究脚手架：trace 适配、query mining、指标、评测编排和实验归档。

当前已经落地 Open-SWE-Traces Java trace → query 的最小路径。先用 dry-run 检查 LLM 将看到
的 prefix-only prompt：

```bash
python scripts/mine_trace_queries.py \
  --input open_swe_java.jsonl \
  --output /tmp/codesense-query-prompts.jsonl \
  --dry-run --limit 3
```

确认 prompt 后，去掉 `--dry-run` 并设置 `CODESENSE_API_KEY`，即可生成最终 query JSONL。
输出中的 `strategy`、`raw_action`、`source_events` 和 `provenance.prompt` 用于人工检查
query 是否泄露路径、工具输出或未来 trace。

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
