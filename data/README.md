# data/

数据集、人工标注，以及任何**输入**。产物属于 `output/` 和 `runs/`，不放这里。

## 什么进版本库，什么不进

| 类型 | 进 git？ | 放哪 |
|---|---|---|
| 人工标注、查询集（几 KB 的 YAML/JSON） | ✅ 进 | 本目录 |
| 数据集元信息、下载脚本、划分文件 | ✅ 进 | 本目录 |
| 被检索的目标代码库 | ❌ 不进 | 本地 clone，路径作为命令行参数传入 |
| 原始数据集（几十 MB 以上） | ❌ 不进 | 服务器共享目录，本目录写清怎么拿 |
| 模型权重 | ❌ 不进 | 同上 |
| 索引与检索产物 | ❌ 不进 | `output/`，已在 `.gitignore` 里 |

判断标准：**别人重新生成它需要多久？** 几秒能重跑出来的别进版本库；
靠人工标了两周的必须进。

## 当前内容

| 路径 | 说明 |
|---|---|
| `dsl_samples/query.json` | 手写的查询集，按 `feature_localization` 等类别分组 |
| `dsl_samples/query_dsl.json`、`query_dsl_new.json` | 查询 DSL 的样例产物，新旧两版 |
| `dsl_samples/extracted_results.json` | LLM 关键词抽取的累积结果，`scripts/run_llm_keyword_extractor.py` 往里追加 |
| `dsl_samples/code_schema.json` | 代码元素的 schema 样例 |
| `prompts/semcon_prompt.md` | SemCon 抽取的 prompt 草稿 |

## 不在这里但也是输入的东西

有两份资源因为要跟着代码一起分发，留在了包里，不在 `data/`：

| 路径 | 说明 |
|---|---|
| `codesense/tokenizer/sentencepiece_*.model` / `*.vocab` | 训好的分词器，运行时必需。体积小，**必须进版本库**（`.gitignore` 里专门为它开了豁免） |
| `codesense/expansion/dataset/valid_abbr.json` | 缩写白名单，`abbreviate.py` 直接按包内相对路径读 |

## 目标代码库怎么配

被检索的项目不进版本库，自己 clone 到本地，路径作为命令行参数传给脚本：

```yaml
target:
  project_path: "~/Workspace/projects/youlai-boot-master"
  project_name: "youlai-boot-master"
```

## 大数据集怎么写

不要把几个 G 的东西塞进来，在这里留一份说明就行：

```markdown
## <数据集名>

- 位置：`/data/shared/<名字>/`（组内服务器）
- 版本：2026-06 快照，md5 `abc123...`
- 获取：`bash scripts/fetch_data.sh <名字>`
- 划分：`splits/<名字>.json`
```
