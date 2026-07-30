# scripts/

可直接运行的入口脚本。

**查询理解**

| 脚本 | 用途 |
|---|---|
| `run_llm_keyword_extractor.py` | 跑一次 LLM 关键词抽取，结果追加到 `data/dsl_samples/extracted_results.json` |
| `run_keyword_extraction.py` | 用 KeyBERT 抽一次关键词，人工核对用 |
| `run_keyword_expansion.py` | 跑一次关键词扩展（Step A 检索 + Step B 扩展） |

**检索与过滤**（各阶段可单独跑，便于定位问题出在哪一层）

| 脚本 | 用途 |
|---|---|
| `run_regex_search.py` | 在符号表上做一次关键词/正则检索 |
| `run_invert_index_search.py` | 倒排索引 + 缩写扩展检索 |
| `run_exact_search.py` | 按 SemQL 做精确匹配（code_element / code_line） |
| `run_relation.py` | 按 `relation_semql.json` 做结构关系过滤 |
| `run_cluster.py` | 语义聚类过滤 |
| `run_embedding_filter.py` | term-level embedding 过滤 |

**排查工具**

| 脚本 | 用途 |
|---|---|
| `lsp_smoke_check.py` | 手动验 JDT.LS 通不通，起服务抽一条调用链 |

```bash
python -m scripts.run_regex_search readahead ra --mode and --limit 20
python -m scripts.run_relation \
    output/<project>/query_1/relation_semql.json \
    output/<project>/query_1/filtered_by_type.json
python -m scripts.run_cluster --threshold 0.3
```

完整 pipeline 不在这里，走包自己的入口：

```bash
python -m codesense --init --query "..."
```

---

## 写脚本的唯一规则

**脚本里不写业务逻辑。**

脚本只做三件事：解析命令行参数、调用包里的类、打印结果。真正的逻辑放
`codesense/`（核心功能）或 `evaluation/`（研究脚手架）里。

理由：

- 脚本没法被 `import`，也就没法被测试；
- 逻辑写在脚本里，别的地方要复用只能复制粘贴；
- 两份复制的代码会立刻开始分叉。

判断标准很简单：**如果这段代码值得测试，它就不该待在 `scripts/` 里。**

另外，参数一律走 `argparse` + `configs/`，不要在脚本里写死路径。
早期这些脚本就是写死的（`/Users/huangzhuochen/...`），换台机器全跑不了。

---

## 这里的脚本是从哪来的

大部分是从核心包里搬出来的**胶水代码**——读进来、拼起来、调一下、写出去，
自己一行计算都没有。它们原来以两种形态住在 `codesense/` 里：

- 模块底部的 `if __name__ == "__main__":` 块，装着完整工作流；
- 形如 `run_xxx(输入路径, 输出路径)` 的「便利函数」。

搬出来的理由不是洁癖：留在核心包里，核心就背着「产物叫什么、放在哪」的知识，
产物布局一改核心跟着改；而且那些流程没法被 import、没法测、想复用只能复制粘贴。

对应关系：

| 现在 | 原来在 |
|---|---|
| `run_relation.py` | `executors/relation_executor.py` 的 `run_relation_executor()` + `__main__` |
| `run_cluster.py` | `filters/cluster_pipeline.py` 的 `__main__`（32 条语句） |
| `run_embedding_filter.py` | `filters/embedding_filter.py` 的 `__main__` |
| `run_exact_search.py` | `search/exact_code_search.py` 的 `exact_code_search()` + `__main__` |
| `run_invert_index_search.py` | `search/invert_index_search.py` 的 `invert_index_search4symbol()` + `__main__` |
| `lsp_smoke_check.py` | `parsers/java_lsp_client.py` 的 `__main__` |

判断标准见组内 DEV-COOKBOOK 的
`.claude/skills/cookbook-refactor/references/script-boundary.md`：
**核心包只留功能逻辑，IO 处理归边界层，胶水粘合归这里。**
