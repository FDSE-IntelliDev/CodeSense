# CodeSense `finetune` 策略轻量化 Demo 设计

> 状态：收缩版 Demo 已实现
>
> 日期：2026-08-06
> 范围：项目级 embedding 初始化、训练、持久化与 expansion 生成

## 1. 目标

当前 `finetune` 会加载并续训完整 `cc.en.300.bin`，实际可能占用十几到二十 GB
内存。CodeSense 面向 Agent 和资源有限的本地环境，默认路径不应承担这个成本。

本次只快速搭建一个可运行、可调试、方便 review 的 Demo：

- 提供 `lightweight`、`full_force`、`warn_full` 三种 profile；
- `lightweight` 使用约 100～300MB 的紧凑基础包，峰值内存硬预算默认 2GB；
- `full_force` 用完整 FastText 初始化，但最终仍训练紧凑 Word2Vec；
- `warn_full` 保留原来的完整 FastText 续训能力，并要求显式危险确认；
- 项目模型保存到 `.codesense/embedding/project.model`；
- 搜索只读取 `expansion.json`，不加载 embedding 模型；
- 第一版只支持英语查询词。

## 2. Demo 明确不做的事情

以下内容不是当前可运行 Demo 的必要条件，不进入本次代码：

- 不实现离线基础模型生成器、固定项目扫描器和查询基准生成器；
- 不实现按文件增量训练、corpus diff、自动恢复和复杂兼容性判断；
- 不实现通用 provider 插件框架；
- 不实现训练资源监控、RSS 基准和详细训练报告；
- 不实现多代模型备份、目录事务和崩溃恢复；
- 不为了潜在扩展加入当前没有调用方的接口。

紧凑基础包由离线流程预先准备。其词表仍应来自固定英文代码搜索查询、固定多语言
开源项目和少量强制锚点，但这个制备流程不属于当前 Demo 实现。

## 3. 文件边界

保留之前确认的目录边界，但每个模块只承担一个直接职责：

```text
codesense/indexing/
├── grounding.py        lexical/vectors 现役逻辑和 finetune 调度
└── finetune/
    ├── config.py       profile 和必要训练参数
    ├── vocabulary.py   词表选择和简单内存预算
    ├── providers.py    三种 profile 的向量准备
    ├── initializers.py 项目 OOV 初始化
    ├── trainer.py      紧凑 Word2Vec 训练
    ├── corpus.py       可重复迭代的磁盘语料
    ├── artifacts.py    固定产物路径和最小 manifest
    └── expansion.py    baseline/adapted 取较优结果
```

不为这些模块再增加抽象基类、事务类型、报告类型或未使用的协议。

## 4. 三种 profile

| Profile | 初始化来源 | 训练载体 | 行为 |
|---|---|---|---|
| `lightweight` | 紧凑基础包 | 紧凑 Word2Vec | 默认，不得加载完整 FastText |
| `full_force` | `cc.en.300.bin` | 紧凑 Word2Vec | 子进程提取所需词向量后退出 |
| `warn_full` | `cc.en.300.bin` | 完整 FastText | 保留旧行为，显式危险选项 |

### 4.1 `lightweight`

紧凑基础包只要求以下运行时文件：

```text
compact-base/
├── baseline-vectors.npy
├── compact-vocabulary.txt
├── subword-initializer.npz     可选
└── manifest.json
```

运行时 memory-map 基础向量，只选择固定通用词和当前项目所需词。不得调用
`load_facebook_model` 或 `load_facebook_vectors`。

### 4.2 `full_force`

完整 FastText 只在 `spawn` 子进程加载。子进程接收已规划词表，输出紧凑 `.npy`
和词表文件并退出；父进程随后训练 Word2Vec，避免完整模型和训练矩阵长期共存。

### 4.3 `warn_full`

只有同时设置以下参数才允许执行：

```python
finetune_profile="warn_full"
allow_unsafe_full=True
```

启动前提示可能需要 15～25GB RAM。默认只导出紧凑项目模型；只有
`preserve_full_model=True` 才额外保存完整训练模型。

## 5. 词表和内存预算

`lightweight` 的训练词表只包含：

1. 紧凑基础包中的通用查询词；
2. 当前项目中达到 `min_df` 的词；
3. lexical expansion 的目标词；
4. 这些目标词的必要训练上下文。

内存采用一个保守估算：

```text
estimated = (Word2Vec 输入矩阵 + 输出矩阵 + baseline) × 1.35
```

超过 `memory_budget_mb` 时，先删除低频非目标上下文。目标词和通用查询词仍超预算就
直接报错，不实现多级裁剪策略。

## 6. 项目词初始化

保留之前确认的可靠性顺序，但用一个直接函数完成，不建立初始化框架：

```text
1. 基础模型精确命中
2. lexical 给出的规范形或缩写全称
3. 标识符拆分后的已知组成词
4. 紧凑字符 n-gram（基础包存在时）
5. 已知项目上下文向量中心
6. 固定种子的随机向量
```

所有非零向量归一化。Demo 不保存每个词的详细初始化审计记录。

## 7. 训练

`lightweight` 和 `full_force` 最终都训练 Word2Vec Skip-gram：

```text
window=5
negative=10
epochs=2（调用方可改）
start_alpha=0.005
end_alpha=0.0005
min_count=2
```

通用基础词 `lockf=0`，项目新增词 `lockf=1`。不再计算 alignment 和连续
trainability；这部分不是验证 Demo 可行性的必要条件。

训练后保存：

```text
.codesense/embedding/
├── project.model
├── baseline-vectors.npy
├── baseline-vocabulary.json
└── manifest.json
```

manifest 只记录 profile、基础包版本、向量维度、词表大小和 epochs。明确重新执行
`Project.build` 时可以重新训练；普通 `Project.open` 直接复用索引和 expansion，
不触发训练。

## 8. Expansion

生成 expansion 时同时比较：

```text
base_score    = 基础空间相似度，上限 0.60
adapted_score = 项目空间相似度，上限 0.65
final_score   = max(base_score, adapted_score)
```

同一 `(query_term, project_term)` 只保留较高分及其来源，不相加。lexical 仍由
`grounding.py` 通过现有 `_merge` 合入，上限 0.75。项目目标继续使用现有 `min_df`
和 ICF 门禁。

## 9. 构建和搜索流程

```text
Project.build
  → 扫描项目，同时把训练句子流式写入临时 JSONL
  → lexical grounding
  → 按 profile 准备基础向量
  → 规划词表并初始化 OOV
  → 训练并保存 project.model
  → 生成并保存 expansion.json

Project.open().search
  → 只读取 index + expansion.json
```

失败时：`strict_profile=True` 直接抛错；否则保留 lexical expansion，并在
`meta.json` 记录 `grounding_status="degraded"` 和简短原因。Demo 不自动从
`full_force` 切换到另一套模型路径。

## 10. 验收

自动化测试只覆盖可快速运行的关键路径：

- lightweight 不调用任何 Facebook FastText loader；
- 小词表 Word2Vec 能训练并精确冻结通用词；
- full_force 子进程退出后父进程才读取紧凑输出；
- baseline/adapted 对同一映射取较高分；
- 项目模型和 manifest 保存到 `.codesense/embedding/`；
- `Project.open().search()` 不加载 embedding。

`warn_full` 不执行真实完整模型测试。最终门禁仍为：

```text
ruff check .
ruff format --check .
pytest
```
