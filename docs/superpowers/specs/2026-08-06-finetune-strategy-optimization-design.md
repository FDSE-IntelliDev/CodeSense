# CodeSense `finetune` 策略轻量化优化设计

> 状态：设计已确认，尚未实现
>
> 日期：2026-08-06
> 范围：`codesense.indexing.grounding` 的项目级 embedding 初始化、训练、持久化与 expansion 生成

## 1. 背景

当前 `finetune` 策略通过 Gensim 的 `load_facebook_model()` 完整加载
`cc.en.300.bin`，再直接在项目语料上继续训练 FastText。这个模型包含约
200 万个词、200 万个 subword bucket、300 维输入矩阵和输出矩阵：模型文件约
7GB，实际训练通常需要十几到二十 GB 内存。

这条路径存在四个问题：

1. 构建一个项目时加载了远超实际需要的通用词和 subword 参数；
2. FastText 的 `vectors_vocab_lockf` 只能冻结词条向量，不能冻结共享的
   subword 向量，通用语义锚点仍会漂移；
3. 当前项目实际只需要几万通用查询词和项目自身词表；
4. CodeSense 主要作为面向 Agent 的通用代码搜索能力运行，必须考虑低内存、
   CPU-only 和用户进程同时占用资源的环境。

重设计不否认完整 FastText 的价值。完整模型仍适合做更广的候选发现和 OOV
初始化，但不应成为默认项目训练载体。

## 2. 目标与非目标

### 2.1 目标

- 提供 `lightweight`、`full_force`、`warn_full` 三种 `finetune` profile；
- `lightweight` 正常峰值内存控制在约 1GB，硬上限为 2GB；
- `lightweight` 不得加载 7GB 的 `cc.en.300.bin`；
- 用户构建项目时只扫描和训练当前项目，不重新扫描基础模型使用的开源项目；
- 项目训练模型持久化到 `.codesense`，后续增量构建可以继续训练；
- 普通搜索只读取 `expansion.json`，不加载 embedding 模型；
- 保留通用英语查询词与项目代码词处于同一向量空间的能力；
- 通用锚点可以精确冻结，项目特有义项可以受控移动；
- 模型、词表、语料或阈值变化时，可以准确判断训练、重算 expansion 或直接复用；
- 失败时保留旧模型和旧 expansion，并清楚记录实际降级结果。

### 2.2 非目标

- 第一版只支持英语查询词，不直接建立中英文联合向量空间；
- 搜索阶段不做实时向量近邻计算；
- 不将大型模型权重提交到代码仓库；
- 不在本次设计中替换 LLM query planning 或 QL 执行逻辑；
- 不保证 `warn_full` 满足 2GB 内存限制；
- 不试图让一个项目的微调模型被其他项目共享。

## 3. 核心决策

1. 固定开源项目和英文代码搜索查询基准只用于离线生成基础词表和基础模型。
2. 用户初始化时只消费已经生成的紧凑基础模型，并只训练当前项目语料。
3. 正式项目训练模型使用 Word2Vec；FastText 的 subword 只用于初始化。
4. `lightweight` 和 `full_force` 最终导出相同格式的紧凑 Word2Vec 项目模型。
5. `warn_full` 保留当前完整 FastText 续训能力，作为显式危险选项。
6. 项目同时保留只读基础向量和适配后向量，生成 expansion 时取两者较优结果。
7. `.codesense` 保存可继续训练的项目模型，而不只是只读向量。
8. `warn_full` 默认也只导出紧凑项目模型；只有显式设置
   `preserve_full_model=True` 才保存完整训练模型。

## 4. 总体架构

系统分成离线基础模型制备和用户项目适配两个生命周期。

```text
离线制备
固定英文查询基准 + 固定多项目代码语料 + cc.en.300
                           ↓
             compact-base + vocabulary manifest

用户项目构建
compact-base 或 cc.en.300 + 当前项目词表/语料
                           ↓
              项目专属紧凑 Word2Vec
                           ↓
                     expansion.json

用户搜索
index + expansion.json（不加载 embedding）
```

现阶段不拆分现有 `grounding.py`。它继续保留 lexical/vectors 的现役逻辑和
grounding 对外入口；本次新增的 finetune 优化实现放入独立子包：

```text
codesense/indexing/
├── grounding.py        保留现役入口、lexical/vectors 逻辑和 finetune 调度
└── finetune/
    ├── config.py       profile、资源预算和训练参数
    ├── vocabulary.py   基础词表和项目词表规划
    ├── providers.py    紧凑模型、完整模型和危险完整训练的统一接口
    ├── initializers.py 项目词初始化
    ├── trainer.py      Word2Vec 项目适配
    ├── corpus.py       流式语料与增量状态
    ├── artifacts.py    manifest、模型和原子持久化
    └── expansion.py    基础/适配空间的 expansion 生成与合并
```

`grounding.py` 只负责调用 finetune 子包提供的统一入口，不吸收新增的模型制备、
训练和持久化细节。各 profile 通过共同的 provider/trainer 接口接入，避免在业务
流程中堆叠大量 `if/elif` 分支。`finetune/expansion.py` 只负责合并基础空间与适配
空间的向量结果；现有 `codesense/indexing/expansion.py`、lexical 规则和 expansion
对外结构保持不变。

## 5. 基础词表如何生成

### 5.1 数据来源

基础词表由固定数据集离线生成：

- 英文代码搜索查询基准；
- 多语言、多框架、多规模的固定开源项目清单；
- 少量具有明确理由的强制锚点；
- `cc.en.300` 的通用初始向量。

固定项目清单至少覆盖 Java/Kotlin、Python、Go、JavaScript/TypeScript、Rust、
C/C++。统计以“多少项目使用该词”为主，不让一个大型项目的原始词频支配结果。

用户初始化 CodeSense 时不会重新下载或扫描这些项目；它们只属于基础模型的
离线可复现制备流程。

### 5.2 通用查询词

英文查询先使用与 CodeSense 索引一致的分词和规范化，再区分：

- 查询框架词：`find`、`show`、`where`、`code`、`method`；
- 语义载荷词：`retry`、`backpressure`、`pagination`、`permission`。

只有语义载荷词进入 embedding 候选。高频但不能区分代码元素的框架词被排除；
低频但搜索价值高的概念词不能因为频率低被删除。

预计保留约 25,000～30,000 个通用查询词。

### 5.3 代码领域词

代码领域词来自标识符、类型、方法、字段、注释和框架/API 名称。每个词计算：

```text
code_score(term)
  = code_lift(term)
    × repository_breadth(term)
    × language_breadth(term)
    × query_usefulness(term)
```

其中 `code_lift` 衡量该词在代码语料中相对普通英语语料的提升程度。
`cache`、`retry`、`transaction`、`deadlock` 等跨项目概念进入基础词表；
`redisson`、`mybatis` 等项目或框架专名通常在扫描目标项目时动态加入。

预计保留约 15,000～20,000 个代码领域词。

### 5.4 强制锚点

约 1,000～3,000 个强制锚点用于保护统计方法容易漏掉但搜索价值明确的词：

- CodeSense 查询单元使用的规范概念；
- 回归测试中的缩写与全称；
- 常见但相对低频的代码搜索概念；
- `buf ↔ buffer`、`dept ↔ department`、`perms ↔ permission` 等映射。

强制锚点必须记录来源并有基准用例，不能演变成没有依据的大型手工词典。

### 5.5 基础词表产物

三类词去重后，紧凑基础词表目标为 40,000～50,000 个词：

```text
compact-base/
├── model.model                 可继续训练的紧凑 Word2Vec
├── baseline-vectors.npy        只读、可 memory-map 的基础向量
├── compact-vocabulary.txt      约5万词，带向量
├── extended-candidates.txt     约20万词，仅词与分类元数据
├── subword-initializer.npz     只用于 OOV 初始化
└── manifest.json               来源、版本、哈希和词级统计
```

`manifest.json` 应能回答每个词为何入选，例如它属于查询词、代码领域词、强制
锚点中的哪几类，以及 query df、repository df 和语言覆盖数。

## 6. 三种训练方式

| Profile | 初始化来源 | 项目训练载体 | 主要用途 | 资源特征 |
|---|---|---|---|---|
| `lightweight` | 100～300MB 紧凑基础模型 | 紧凑 Word2Vec | 默认、低资源环境 | 正常约1GB，硬上限2GB |
| `full_force` | 完整 `cc.en.300.bin` | 裁剪后的紧凑 Word2Vec | 更强候选覆盖和 OOV 初始化 | 初始化阶段高内存 |
| `warn_full` | 完整 `cc.en.300.bin` | 完整 FastText 直接续训 | 实验和兼容旧行为 | 约15～25GB RAM |

### 6.1 `lightweight`

```text
compact-base.model
  → 加入当前项目词
  → 使用规范形、拆词、紧凑 n-gram 和上下文初始化 OOV
  → 精确冻结通用锚点
  → 训练紧凑 Word2Vec
```

整个流程不得加载完整 FastText。

### 6.2 `full_force`

```text
cc.en.300.bin
  → 从完整词表选择当前项目需要的通用候选
  → 为项目 OOV 生成完整 FastText 子词向量
  → 导出紧凑 baseline
  → 释放完整 FastText
  → 训练紧凑 Word2Vec
```

完整模型提取在独立子进程中执行。子进程退出后，主进程才开始 Word2Vec
训练，避免完整 FastText 和项目训练矩阵长期共存。

`full_force` 的“force”表示使用完整模型进行候选发现和初始化，不表示直接更新
完整模型的所有参数。

### 6.3 `warn_full`

`warn_full` 直接执行完整 FastText 续训，保留当前高资源路径。必须同时显式设置：

```python
finetune_profile="warn_full"
allow_unsafe_full=True
```

CLI 也必须同时提供 `--allow-unsafe-full`，否则拒绝执行。启动前明确提示：

- 可能需要 15～25GB RAM；
- 运行时间可能很长；
- FastText 通用锚点无法精确冻结；
- `preserve_full_model=True` 会在单个项目下额外占用 7GB 以上磁盘。

默认训练结束后只导出紧凑项目 Word2Vec，并释放完整训练副本。显式设置
`preserve_full_model=True` 时才将完整训练模型保存到 `.codesense`；保存前必须
检查可用磁盘空间。

## 7. 项目词表和项目模型

### 7.1 项目词分组

| 分组 | 示例 | 行为 |
|---|---|---|
| 通用锚点 | `permission`, `buffer` | 保留基础位置 |
| 通用词的项目义项 | `around`, `business`, `clean` | 受控移动 |
| 项目缩写/组合词 | `perms`, `redisTemplate` | 初始化后充分训练 |
| 项目专名/OOV | `redisson`, `mybatis` | 初始化后充分训练 |

项目词至少满足一项才进入训练模型：达到 `min_df`、是有效 expansion 目标、是
高价值项目词的训练上下文，或属于框架/API 强制保留词。

### 7.2 项目产物

```text
.codesense/
├── meta.json
├── symbols.jsonl
├── postings.json
├── graph.json
├── expansion.json
└── embedding/
    ├── project.model
    ├── baseline-vectors.npy
    ├── baseline-vocabulary.json
    ├── corpus.jsonl
    ├── manifest.json
    ├── training-report.json
    └── full-fasttext.model       仅 warn_full + preserve_full_model
```

`project.model` 是包含训练状态的 Word2Vec 模型，可以继续增量训练。
`baseline-vectors.npy` 是只读、可 memory-map 的项目初始化空间，用于防止适配后
的 expansion 回归。

### 7.3 Manifest

Manifest 至少包含：

```json
{
  "schema_version": 1,
  "requested_profile": "lightweight",
  "effective_profile": "lightweight",
  "status": "ready",
  "base_model_sha256": "...",
  "base_vocabulary_version": "...",
  "vector_size": 300,
  "project_corpus_fingerprint": "...",
  "project_vocabulary_size": 18342,
  "general_vocabulary_size": 47281,
  "epochs_completed": 2,
  "grounding_parameters": {
    "min_df": 2,
    "min_cosine": 0.55,
    "max_targets": 4
  }
}
```

基础模型、维度、词表规范或 profile 变化时重新初始化；仅 expansion 阈值变化时
不重新训练，只重新计算 expansion。

## 8. 项目词初始化

`lightweight` 按以下可靠性顺序初始化项目词：

```text
1. 基础模型精确命中
2. 高置信度规范形或缩写全称
3. 标识符拆分后的已知组成词
4. 紧凑字符 n-gram 初始化器
5. 已知项目上下文向量中心
6. 使用固定随机种子的随机初始化
```

例如：

```text
perms         → permission
redisTemplate → redis + template
redisson      → n-gram + redis/cache/client 上下文
```

初始化来源、置信度和参与的规范词/上下文写入 `training-report.json`，使 OOV
质量可审计。

`full_force` 使用完整 FastText 为同一批项目词产生更完整的初始向量。两种模式
的差异集中在候选覆盖和 OOV 初始化质量，最终模型结构保持一致。

## 9. 冻结与训练算法

### 9.1 项目语义错配

对每个出现在基础空间中的项目词，使用高 ICF 项目上下文计算对齐程度：

```text
alignment(t)
  = 基础向量 v(t)
    与高 ICF 项目上下文向量的加权平均余弦
```

对齐分低表示通用词义与项目用法不一致，例如 `around` 在普通英语中表示“周围”，
在 Spring 项目中可能表示 AOP `@Around`。

### 9.2 可训练程度

```text
trainability(t) = clip(1 - alignment(t) / 0.15, 0, 1)
```

- `alignment >= 0.15`：冻结；
- `alignment` 越接近 0：越允许根据项目语料移动；
- 新项目词：`trainability = 1`；
- 不在项目语料中的通用词：`trainability = 0`；
- 项目上下文证据不足：冻结，避免偶然共现导致漂移；
- 存在高置信度缩写映射时，限制最大移动程度。

Word2Vec 没有参与表示的共享 subword 参数，因此 `lockf=0` 可以真正冻结词向量。

### 9.3 默认训练参数

```text
模型：Word2Vec Skip-gram
维度：300
window：5
negative：10
epochs：2，可由调用方配置到5
初始学习率：0.005
最低学习率：0.0005
min_count：2
```

参数通过 CLI → `Project.build` → `GroundingConfig` 注入，不进入第二份配置文件，
也不在代码中写机器相关路径。

## 10. 基础空间与项目空间共同兜底

项目目录保留初始化时的 baseline。生成 expansion 时同时计算：

```text
base_score    = 基础空间中的相似度
adapted_score = 项目适配空间中的相似度
final_score   = max(base_score, adapted_score)
```

这使项目模型可以学到 `around → aspect/log/async`，同时不会破坏基础空间原本正确
的 `department → dept`。

同一映射由多个来源发现时取最高值，不相加。原因记录为 `lexical`、
`vector-base` 或 `vector-adapted`。

## 11. Expansion 生成

### 11.1 候选范围

`lightweight` 使用固定通用查询词、代码领域词、强制锚点和当前项目缩写推导的
规范形。

`full_force` 额外使用完整 FastText 中的项目相关规范词、extended candidate
清单中的低频通用词和完整 FastText OOV 初始化结果。

不执行“200 万通用词 × 全部项目词”的全矩阵比较。完整词表只为经过查询价值、
代码领域、正字法或项目上下文预筛选的候选提供向量。

### 11.2 评分顺序

```text
lexical        上限 0.75
vector-adapted 上限 0.65
vector-base    上限 0.60
```

项目适配结果进入 expansion 前必须满足：

- 项目词达到 `min_df`；
- 项目词通过与查询执行侧一致的 ICF 门禁；
- 有足够项目上下文证据；
- 相似度达到阈值；
- 初始化置信度和训练漂移未触发质量门禁。

每个通用 key 最多保留 `max_targets=4` 个项目目标。输出结构继续兼容现有
`expansion.json` 和 QL satisfier。

## 12. 流式语料和增量训练

当前 `BuildResult.sentences` 将全部句子保存在内存中。新设计将训练语料写成可
重复迭代的磁盘流：

```json
{"file":"src/service/UserService.java","hash":"...","tokens":["user","service","permission","role"]}
```

训练按批读取 `.codesense/embedding/corpus.jsonl`，不让完整语料和所有模型矩阵
同时常驻内存。

增量规则：

```text
文件未变化
  → 不训练

少量文件新增或修改
  → 只用变化文件的语料增量训练

基础模型/profile/分词规则变化
  → 重新初始化

删除或变化 token 占比 >= 20%
  → 重新初始化
```

Word2Vec 无法撤销被删除代码已经产生的梯度，因此大量删除必须重新初始化。
20% 是默认 API 参数，不是写死常量。

## 13. 资源限制

### 13.1 `lightweight`

```text
正常峰值内存：约1GB
硬上限：2GB
紧凑基础模型：不超过300MB
项目模型目标：不超过300MB
```

构建前按照词表大小、维度、输入/输出矩阵、baseline、n-gram 初始化器和训练批次
估算内存。超过预算时依次：

1. 删除不作为 expansion 目标的低频项目上下文词；
2. 删除低优先级 extended candidates；
3. 使用 memory-map 读取 baseline；
4. OOV 初始化完成后立即卸载 n-gram 初始化器；
5. 仍超限则停止微调，回退到基础向量或 lexical。

不会运行时偷偷降低向量维度，因为那会破坏模型兼容性。

### 13.2 `full_force` 与 `warn_full`

`full_force` 不承诺 2GB 初始化峰值，但通过子进程保证完整模型在紧凑训练开始前
释放。

`warn_full` 不设低资源承诺。启用完整模型持久化前必须检查磁盘空间，任何完整
模型文件都不得被纳入 Git。

## 14. API 与 CLI

### 14.1 Python API

```python
project = Project.build(
    project_root,
    strategy="finetune",
    finetune_profile="lightweight",
    model_path=compact_model_path,
    memory_budget_mb=2048,
    epochs=2,
    strict_profile=False,
)
```

完整初始化：

```python
project = Project.build(
    project_root,
    strategy="finetune",
    finetune_profile="full_force",
    model_path=full_fasttext_path,
)
```

危险完整训练：

```python
project = Project.build(
    project_root,
    strategy="finetune",
    finetune_profile="warn_full",
    model_path=full_fasttext_path,
    allow_unsafe_full=True,
    preserve_full_model=False,
)
```

### 14.2 CLI

```bash
codesense init PROJECT \
  --strategy finetune \
  --finetune-profile lightweight \
  --model COMPACT_MODEL \
  --memory-budget-mb 2048
```

`--finetune-profile` 仅在 `strategy=finetune` 时有效。`warn_full` 必须同时提供
`--allow-unsafe-full`。

### 14.3 参数与默认值

- `finetune_profile` 默认 `lightweight`；
- `preserve_full_model` 默认 `False`；
- `allow_unsafe_full` 默认 `False`；
- `strict_profile` 默认 `False`；
- `memory_budget_mb` 在 `lightweight` 默认 2048；
- 所有参数沿 CLI → 构造函数 → 组件构造器传递，不写入配置文件。

## 15. 复用、失败和原子保存

统一流程：

```text
扫描当前项目
  → 生成项目词表和流式语料
  → 检查 embedding manifest
  → 复用、增量训练或重新初始化
  → 生成 expansion
  → 原子保存全部产物
```

训练先写入 `.codesense/embedding.next/`。模型、manifest、训练报告和 expansion
全部完成后才替换正式产物。失败时保留旧模型与旧 expansion。

降级规则：

| 故障 | 默认行为 |
|---|---|
| 已有模型，本次增量训练失败 | 保留旧模型和旧 expansion |
| 紧凑基础模型损坏 | 回退 lexical，记录 degraded |
| `full_force` 提取失败 | 尝试降级到 `lightweight` |
| `warn_full` 失败 | 使用训练前 baseline 或旧模型 |
| expansion 计算失败 | 保留旧 expansion；首次构建用 lexical |
| `lightweight` 仍超过2GB预算 | 停止微调并降级 |

`strict_profile=True` 时禁止自动降级，无法完成请求的 profile 就直接报错。

Manifest 和 `meta.json` 必须同时记录请求与实际执行结果：

```json
{
  "requested_profile": "full_force",
  "effective_profile": "lightweight",
  "status": "degraded",
  "reason": "full FastText extraction failed"
}
```

## 16. 兼容与迁移

- `strategy="lexical"` 和 `strategy="vectors"` 行为保持不变；
- 新的 `strategy="finetune"` 默认进入 `lightweight`；
- 当前“完整 FastText 直接续训”迁移到 `warn_full`；
- 旧 `.codesense` 没有 embedding manifest 时仍能通过现有 expansion 搜索；
- 旧索引需要重新训练时从所选 profile 的基础模型初始化，不猜测旧训练状态；
- QL 继续接受旧的 `vector` reason，新实现新增 `vector-base` 和
  `vector-adapted`；
- 不自动复制或迁移已有完整模型文件。

## 17. 测试与评估边界

### 17.1 自动化测试

`lightweight` 必须覆盖：

- 词表分类、裁剪和内存预算收缩；
- 六级项目词初始化顺序；
- Word2Vec 精确冻结和 trainability；
- baseline 与 adapted expansion 取优；
- manifest 兼容判断；
- 增量语料差异和 20% 重建门禁；
- 原子保存、失败恢复和降级状态；
- `Project.open().search()` 不加载 embedding。

`full_force` 的公共提取协议、子进程生命周期和紧凑导出使用小型 fixture 或 fake
provider 测试，不在默认 CI 下载或加载真实 7GB 模型。

按照已确认范围，`warn_full` 的真实完整 FastText 训练不加入自动化测试和发布
门禁，也不要求 CI 验证训练效果。它只保留显式手工运行入口和运行时安全提示。

### 17.2 离线质量基准

基础模型制备和发布前使用独立留出集检查：

- 英文查询有效概念覆盖率至少 95%；
- `permission → perms` 等确认映射的 Recall@4；
- `lightweight` 搜索质量必须优于 lexical 基线；
- `lightweight` 的有效映射覆盖尽量达到 `full_force` 的 90%～95%；
- 冻结组缩写映射不能退化；
- 项目义项错配榜中的目标词应出现预期项目邻居；
- `lightweight` 实测峰值 RSS 不超过 2GB。

真实大模型、真实多项目语料和端到端 golden 属于 slow/offline 评估，不进入普通
单元测试。

## 18. 验收标准

设计实现完成需同时满足：

1. 默认 `finetune` 使用 `lightweight`，不加载完整 FastText；
2. `lightweight` 峰值 RSS 在基准项目上不超过 2GB；
3. `.codesense/embedding/project.model` 可以在项目变化后继续训练；
4. 未变化项目不会重复训练；
5. 搜索路径不加载任何 embedding 模型；
6. `full_force` 在完整模型进程退出后才开始紧凑训练；
7. `warn_full` 必须显式二次授权，默认不保存完整模型；
8. 基础空间中已有的正确 expansion 不因项目微调丢失；
9. 降级、失败和实际 profile 在 manifest/meta 中可观察；
10. 现有 lexical/vectors 和旧 expansion 读取保持兼容。

## 19. 最终结论

优化后的 `finetune` 不再等同于“把整个 FastText 放进项目里继续训练”，而是：

```text
用通用模型提供可靠起点
  + 用固定代码搜索语料构建紧凑基础空间
  + 只让当前项目真正需要的词参与训练
  + 同时保留基础空间防止回归
  + 将训练结果持久化为可增量更新的紧凑项目模型
```

`lightweight` 是默认生产路径，`full_force` 是高质量初始化路径，`warn_full` 是
显式危险的兼容/实验路径。三者共享 expansion 输出和搜索 API，因此资源档位的
变化不会侵入查询执行层。
