# 文档索引

| 文档 | 什么时候看 |
|---|---|
| [../ARCHITECTURE.md](../ARCHITECTURE.md) | **入组第一天读**。分层规则、各层职责、planner/executor 契约 |
| [design/](design/) | **下一版设计**：把 query 编译成 QL 脚本（算子编排）。初稿，未实现 |
| [search-pipeline.md](search-pipeline.md) | 想搞清楚一次查询到底经过了哪些阶段 |
| [research-pipeline.md](research-pipeline.md) | SemQL 2.0 的研发研究计划，讲动机和路线 |
| [semql-report.md](semql-report.md) | SemQL 2.0 的完整方案报告 |
| [search-strategy-codegraph.md](search-strategy-codegraph.md) | CodeGraph 的多级降级 + 多信号重排序策略 |
| [schemas/](schemas/) | 某个产物文件的数据结构，以及改它要同步动哪些函数 |
| [decisions/](decisions/) | 想知道「为什么当初这么设计」时翻 |
| [html/](html/) | 生成的可视化报告（聚类流程、系统总览、模型训练等） |
| [../CONTRIBUTING.md](../CONTRIBUTING.md) | 提交前要过哪几条 |

## 文档写在哪

| 内容 | 位置 |
|---|---|
| 项目是什么、怎么跑起来 | 根目录 `README.md` |
| 架构规则和理由 | 根目录 `ARCHITECTURE.md` |
| **还没实现的**设计方案 | `docs/design/`，标明状态 |
| 操作手册、专题说明 | `docs/` |
| 某个产物的数据结构 | `docs/schemas/` |
| 某次设计决策的来龙去脉 | `docs/decisions/` |
| 某个实验为什么跑、结果如何 | `experiments/EXP-XXXX/README.md` |
| 某段代码在干什么 | 代码里的 docstring，**不要**写进 docs/ |

最后一条最容易被违反。文档和代码分开放就一定会不同步，
**能写在 docstring 里的就别写进文档**。
