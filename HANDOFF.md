# HANDOFF

接手这个仓库需要知道的事。按「先做什么」排序，不按重要性。

---

## 0. 立刻要做的一件事

**吊销 dashscope 的 key 并重发。**

明文 key 在 git 历史的 `652a37f:definition.py` 里，而那个 commit **已经在
`origin/main`、`origin/archive/legacy-implementation`、
`origin/refactor/cookbook-restructure` 上**。历史没有重写（重写会打断所有人的
clone），所以唯一的补救是去控制台吊销 `sk-e2a2047...` 重发一个。

在那之前，当它是公开的。

---

## 1. 跑起来

### 依赖

```bash
pip install tree-sitter tree-sitter-languages srctoolkit requests pyyaml
pip install gensim numpy          # 只有向量接地要，lexical 策略不需要
pip install pytest ruff           # 开发
```

Python 3.12。`codesense/ql/` 契约上只用标准库，所以缺依赖时那一层的测试照样能跑。

### API key

**参数走流水线，密钥走环境。** 端点、模型名、超时都是普通参数，显式传进来；
只有 key 例外（不能当命令行参数，会进 shell 历史和进程列表）：

```bash
export CODESENSE_API_KEY=sk-...
# 或写进未被跟踪的 config.yml：  LLM: { api-key: sk-... }
```

端点和模型名也可以走环境变量（`CODESENSE_BASE_URL` / `CODESENSE_MODEL`），
flag 优先。⚠️ `LlmConfig` 自身的默认端点是 **dashscope**，但 CLI 默认用
OpenAI；直接调库时注意这个差异。

### 三条命令

```bash
cd ~/src/netty
codesense init                      # 建 ./.codesense/，不需要任何模型文件
codesense query "写缓冲积压时的背压"   # 从任何子目录都能找到索引
codesense info                      # 当前作用域下是哪份索引

# benchmark
python scripts/run_benchmark.py --index-dir <索引目录> \
    --queries evaluation/benchmark/queries.json \
    --base-url https://api.openai.com/v1 --model gpt-4o-mini \
    --cache <缓存目录> --attempt 0
```

向量接地要一份 fastText `.bin`（如 `cc.en.300.bin`，7 GB）：

```bash
codesense init --strategy vectors --vectors ~/models/cc.en.300.bin --force
```

`vectors` 约需 8 GB 内存、netty 上 47 秒（不含模型加载）；`finetune` 约需 20 GB。
**两者都是构建期**，查询时不加载模型。

### 检查

```bash
ruff check . && ruff format --check . && pytest        # 492 passed
```

---

## 2. 读代码的顺序

1. `docs/design/01-motivation.md` — 为什么不是关键词搜索
2. `docs/report-pipeline.md` — 现在做成了什么、量到了什么（**先读这个**）
3. `codesense/ql/frag.py` — `Frag` 是唯一的类型，所有算子都是 `Frag -> Frag`
4. `codesense/ql/operators/` — 五个算子
5. `codesense/project.py` — 主类，串起来的地方

可视化：`docs/artifacts/codesense-internals.html`，netty 上的真实数据。

---

## 3. 几条不能破的约定

这些都有测试机械强制，不是口头约定。

| 约定 | 强制方式 |
|---|---|
| `codesense/ql/` 不导入任何其它 codesense 子包 | `tests/contract/test_ql_isolation.py` |
| `codesense/ql/` 不依赖任何第三方包 | 同上 |
| import 无副作用（不读盘、不读配置、不连网） | 同上 |
| 加语言不需要改 `codesense/indexing` | `tests/unit/lang/test_registry.py` 现场定义一个玩具语言 |
| 生成的脚本与 `Plan` 等价 | `tests/unit/ql/compile/test_emit.py` 真跑一遍两边比结果 |
| `LlmConfig.__repr__` 不打印 key | `tests/unit/llm/test_judge_parsing.py` |

破坏第一条的写法看起来都很无害（「从 indexing 借个函数」），但那一层是纯逻辑，
一旦引入第三方依赖，「能不能跑测试」就和「装没装齐环境」绑死了。

**不要提交**：密钥、`output/`、`slides/`、模型权重、`.env`、`config.yml`。

---

## 4. 现在的状态

分支 `feat/ql-rewrite`，**从未合进 main**。`main` 上还是重写前那套。
重写前的实现归档在 `legacy/`（19 935 行），不参与构建、lint、测试。

492 个测试全过，ruff 干净。源码全英文，文档全中文（这是刻意的）。

最近四个 commit：

```
docs: visualise index internals and a query trace
feat: pluggable languages, Viterbi segmentation, and a real layer split
feat: end-to-end pipeline -- a repository in, searchable results out
refactor: write all source in English, keep the docs in Chinese
```

⚠️ 只验证了**最终状态**是绿的，没有逐个 checkout 验证中间 commit。

---

## 5. 接着做什么

按性价比排：

### 高：把已有的抽取接进索引构建

`tests/integration/test_handwritten_queries.py` 里四条缺口测试，gap1（注解）和
gap3（修饰符）**代码早就写完了**，`codesense/lang/java/annotations.py` 和
`scanner.py` 都有完整单测——只是 `indexing/pipeline.py` 没去调。
接上就多两个信号域，**工作量是接线不是实现**。

补上之后那两条缺口测试会失败，删掉即可（它们就是这么设计的）。

### 高：查清 netty-backpressure 为什么全 0%

六条路径、三次采样，一条 gold 都没找到。词表里 `watermark`(df=2, icf 0.935) 和
`writability`(df=110, icf 0.559) 都在、都高度可区分，**所以问题不在索引，
在从「背压」到这两个词的那一跳**。这正是 grounding 该解决的问题。

比再刷一遍平均分有信息量得多。

### 中：收紧 subseq 接地规则

netty 上 8 321 条接地里 4 113 条来自 subseq，是占比最大也最烂的一条：
`abandoned → add`、`ability → alt`。已经加过一道闸（缩写侧最长 5 字符），还不够。

### 中：合并分支

`feat/ql-rewrite` 从没合过。越拖越难。

### 低 / 需要新能力

gap2（字段读写边）要新写；gap4（数据流边）是唯一需要**新建分析能力**的一项。

---

## 6. 踩过的坑，别再踩一遍

**跨运行比 benchmark 之前，先确认索引同源。** 我拿英文 prompt 的结果和记忆里的
旧数字比，看着掉了 9–11 个点，查下去发现旧缓存是在另一个 index build 上跑的。
缓存 key 里含 `len(vocab)`，索引变了 key 就变。

**LLM 方差和要测的效应同量级。** 同一条查询、同词表、temperature=0，两次运行
召回率能差 20 个点。不缓存派生结果、不跑多次取平均，比的就是噪音。

**ICF 门禁会杀掉领域核心词。** netty 上 `buf` 占 15.4% 的符号，一度被当成
「太泛」丢掉——而查询问的就是缓冲区。现在的规则是：**下限只管扩展词，
查询词一律保留但按 ICF 降权**。这个张力在小项目上更明显（`pool` 在 HikariCP
占 30%），还没有好答案。

**白名单和执行环境必须同源。** `set` 曾列进白名单却没放进执行环境，
正确的生成脚本全军覆没，那一路的召回读数是 0%。

**模型会把提示词里的占位符原样抄走。** spec 里写 `intent(frag, "the criterion", ...)`，
生成的脚本里就真的是 `"the criterion"`。占位符要写成明显是槽位的样子。

---

## 7. 环境里没有的东西

- 没有 conda；系统 python 缺 pytest/ruff，我用的是 scratchpad 里的独立环境
- `cc.en.300.bin` 在 scratchpad，不在仓库里（7 GB）
- benchmark 用的三个仓库（netty / HikariCP / spring-petclinic）也在 scratchpad
- 这些都是**会话临时目录**，重开会话就没了，需要自己重新准备
