# 实时评测结果 Viewer 设计

## 目标

运行 `scripts/evaluation.py` 时启动一个仅用于本机观测的轻量 Web 页面。程序只在终端输出访问地址，不自动打开浏览器；每完成一条 query 的全部 route 评测，页面立即追加该 query 的标准答案、搜索结果、指标和差异状态。最终评测 JSON 的结构与写入流程保持不变。

## 范围

本次支持：

- 在一个页面内累计展示本次运行的所有 query；
- 页面加载或刷新时恢复本次进程内已经完成的 query；
- query 完成后通过 SSE 实时追加结果；
- 展示 `answers`、每个 route 的 `hits`、`metrics`、错误和实际 route；
- 以绿色表示命中的标准答案，以红色表示遗漏的标准答案，以灰色表示额外搜索结果；
- viewer 未打开、浏览器断开或单个推送失败时，评测继续运行。

本次不支持：

- 跨评测进程保存 viewer 状态；
- 修改或标注结果；
- 用户认证、远程访问、WebSocket 或部署；
- 在页面中启动、暂停或控制评测；
- 改变 `_score()` 和最终 JSON 报告的指标语义。

## 架构

### 评测事件

`scripts/evaluation.py` 保持当前的 repository、case、query 和 route 循环。`_evaluate_search()` 返回一条 query 的完整结果后，主循环构造一个自包含展示记录并调用 viewer 的 `publish()`。因此“完成一条 query”定义为该 query 请求的全部 routes 均已得到成功、fallback 或错误结果。

展示记录包含：

- `query_id`、`repo`、`instance_id` 和 query 序号；
- query 文本及 `event_indices`；
- 原始 `answers`；
- `_evaluate_search()` 产生的 `skipped`、`skip_reason` 和 `routes`。

viewer 只消费这些已经存在的数据，不重新计算 Precision/Recall，也不依赖 `Project` 或搜索对象。

### 本地服务

新增 `evaluation/live_results.py`，内部使用 Python 标准库：

- `ThreadingHTTPServer` 在 daemon thread 中监听 `127.0.0.1`；
- `GET /` 返回内嵌 CSS/JavaScript 的单页 HTML；
- `GET /api/snapshot` 返回当前运行元信息和全部已发布 query；
- `GET /events` 建立 SSE 连接，先发送在同一锁内取得的 snapshot，再向该连接推送新增 query 和最终 summary；
- 内存 store 使用锁保护 records、summary 和 subscriber queues。

端口允许配置为整数。默认使用 `8765`；测试使用端口 `0` 获取操作系统分配的空闲端口。服务仅绑定 loopback，不接受外部 host 配置。

`scripts/evaluation.py` 增加硬编码调试参数：

```python
VIEWER_ENABLED = True
VIEWER_PORT = 8765
```

启动成功时打印：

```text
viewer: http://127.0.0.1:8765
```

启动失败时打印简短 warning 并继续评测，不让观测功能改变 benchmark 结果。评测结束后发布 summary；进程的原有退出语义保持不变。

## 页面结构

页面是一个纵向结果流：

1. 顶部显示运行状态、已完成 query 数和 route 汇总；
2. 每条 query 是一张卡片，显示 repo、query 文本、是否 skipped；
3. 卡片内先展示标准答案，再按配置顺序展示 route；
4. route 区域显示 actual route、耗时、file/function Precision/Recall、错误和 hits；
5. 新 query 追加到页面末尾，不清除先前结果。

颜色语义固定为：

- 绿色 `matched`：gold file 或 `(file, function)` 在该 route 的命中集合中；
- 红色 `missed`：gold file 或 `(file, function)` 未在该 route 的命中集合中；
- 灰色 `extra`：hit 不对应 gold file/function；
- 普通中性色：元数据、skipped 或尚无可计算 metrics 的 route。

匹配结果直接使用 `_score()` 已输出的 `matched_files` 和 `matched_functions`，页面不复制函数名规范化逻辑。HTML 初始内容和 JavaScript 动态插入都必须使用文本节点或明确转义，benchmark/query/path/why 不得作为原始 HTML 注入。

## 数据流

```text
_evaluate_search()
        |
        v
route result + metrics
        |
        +----> 原有 cases / 最终 JSON
        |
        +----> LiveEvaluationViewer.publish(record)
                         |
                  memory snapshot
                         |
                  SSE subscriber queues
                         |
                         v
                    浏览器追加卡片
```

## 错误与生命周期

- viewer 服务创建失败：禁用 viewer，评测继续；
- 没有浏览器连接：记录只保存在内存 snapshot，不阻塞；
- 浏览器断开：移除对应 subscriber；后续 query 不等待该连接；
- 页面刷新：SSE 订阅先在锁内登记并取得 snapshot，服务端先发 snapshot 再发后续事件；客户端按稳定 query key 去重，不会在 snapshot/SSE 交界遗漏或重复 query；
- route 搜索失败：沿用当前 `error` 和空 `hits`，卡片照常展示；
- 程序结束：发布最终 summary，不为了保持页面而阻止进程退出。页面是否继续可访问只保证到评测进程结束。

## 测试

采用 TDD 增加以下覆盖：

- store 按发布顺序保存全部 query，并返回隔离的 snapshot；
- subscriber 能逐条收到新增 query，断开不阻塞发布；
- HTTP `/`、`/api/snapshot` 和 `/events` 的 content type 与基本响应；
- 页面包含 matched/missed/extra 三种状态及指标字段；
- query、路径和 `why` 中的 HTML/脚本内容保持 inert；
- `scripts/evaluation.py` 每完成一条 query 发布一次，包含 case 元数据和完整 route result；
- viewer 启动失败不会阻止报告生成；
- viewer 关闭时维持现有评测行为和 JSON 结构。

验收命令遵循仓库要求：`ruff check .`、`ruff format --check .`、`pytest`，并单独运行新增 viewer 与 evaluation script 测试。
