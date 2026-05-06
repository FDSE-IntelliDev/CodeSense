# Code Search 项目学术汇报素材

## 1. 项目目标
该项目针对输入的 agent query，高效且精准地完成代码搜索任务。

## 2. 端到端流程总览
输入 agent query 后，系统执行以下阶段：
1. DSL-Driven keyword extraction
2. 多路高召回搜索（grep/倒排索引/embedding 语义搜索）
3. 基于 DSL 约束和语义的候选过滤
4. 两阶段 reranking（single + group）
5. 输出最终 code search 结果

## 3. Step1: DSL-Driven keyword extraction
- 设计 query parsing DSL。
- 所有输入 query 都会被解析成 structured query，抽取关键词与 filters。
- DSL 文件路径：`DSL/query_dsl.json`。

### DSL 示例
```json
{
  "keywords": [
    {
      "term": "readahead",
      "synonyms": ["read-ahead", "read_ahead", "prefetch"]
    }
  ],
  "target": "function",
  "filters": [
    { "concept": "disk", "relation": "related_to" },
    { "concept": "performance", "relation": "related_to" }
  ],
  "exclude": [],
  "raw_query": "search functions that enhance readahead performance in disk"
}
```

## 4. Step2: 多路高召回搜索
在得到 structured query 后，进行并行/组合搜索，形成高 recall 候选代码元素集合：
- grep 式正则搜索
- 倒排索引搜索
- embedding model 语义搜索

> 该部分在汇报中简要提及，不展开实现细节。

## 5. Step3: 候选过滤（Filtering）
### Rule-based filtering
利用 DSL 约束进行规则筛选。
- 例如 DSL 中 `target=function`，则从候选集中移除 class 等不符类型元素。

### Semantic filtering
使用大模型抽取候选代码语义，判断是否满足 filter 语义要求，从而进一步提升候选质量。

## 6. Step4: Reranking
### 4.1 Single reranking（单元素重排）
对每个代码元素独立打分，示例手段：
1. LLM 相关性判断：判断候选与 query 是否相关。
2. Token overlap scoring：
   - 通过 tokenizer 计算 query token 与代码 token 覆盖。
   - 例：query=`netlink`，code=`net_link_unbounded`，可覆盖 `net` + `link`。
   - 可结合缩写-全称匹配加分。
3. Code weight scoring：
   - 优先高相关元素。
   - 可基于调用链距离赋值。
   - 可按 query 类型设定元素类型权重（如函数更高）。

### 4.2 Group reranking（组级重排）
考虑代码元素之间的结构关联（如调用链）。
- 元素 A/B 单独看可能与 query 弱相关，但组内关系能强化相关性。
- 例：query 寻找 disk 模块下与 readahead 相关函数。
  - `init_readahead` 调用 `malloc_disk`。
  - `malloc_disk` 单独看与关键词 readahead 不强相关。
  - 但在“init_readahead -> malloc_disk”调用组中，`malloc_disk` 具有更高任务相关性，应加权。

## 7. 输出与价值
完成 rerank 后输出最终 code search 结果。

### 预期价值
- 更高检索召回（多路搜索）
- 更高结果精度（规则 + 语义过滤）
- 更强上下文关联理解（group reranking）
- 更适配 agent query 的工程化检索流程

## 8. 汇报建议结构
- 背景与问题定义
- 方法总体框架
- DSL 解析与 structured query
- 高召回检索模块
- 过滤模块
- 双阶段重排模块
- 案例分析
- 总结与未来工作
