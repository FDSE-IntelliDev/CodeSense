# CodeSense

把一条自然语言查询编译成一段针对代码库的查询脚本，跑出带证据的结果。

```
自然语言 query  ──编译──▶  QL 脚本（Python）  ──执行──▶  带证据的结果
```

关键词搜索答不了「哪段代码在做限流」这类问题——限流的代码可能一个 `rate` 字都没有。
CodeSense 的做法不是把查询翻译成更多关键词，而是**编译成一段脚本**：脚本里的算子
统一是 `Frag -> Frag`，可以读、可以改一行再跑、可以打断点。

---

## 先跑起来

```bash
pip install tree-sitter tree-sitter-languages srctoolkit requests pyyaml
pip install pytest ruff                    # 开发

pytest                                     # 468 passed
```

建索引，然后查：

```bash
# 建索引：只需要源码，不需要编译、不需要 LSP、不需要模型文件
python scripts/build_index.py --source ~/src/netty --out ~/.codesense/netty

# 查询：没有 API key 也能跑，自动降级到不用模型的 lexical 路径
python scripts/search.py --index ~/.codesense/netty \
    --query "写缓冲积压时的背压与流量控制" --show-script
```

用 Python API：

```python
from codesense import Project

p = Project.build("~/src/netty", index_dir="~/.codesense/netty")
p = Project.open("~/.codesense/netty")          # 之后重开，秒级
print(p.search("池化缓冲区的分配与回收").explain())
```

想用 LLM 路径（召回率更高）需要一个 key：

```bash
export CODESENSE_API_KEY=sk-...
python scripts/search.py --index ~/.codesense/netty --query "..." \
    --base-url https://api.openai.com/v1 --model gpt-4o-mini
```

> ⚠️ `LlmConfig` 的默认端点是 dashscope。key 是 OpenAI 的就必须传 `--base-url`，
> 否则直接 401。

---

## 阅读顺序

1. **[HANDOFF.md](HANDOFF.md)** —— 接手要知道的事：怎么跑、哪些约定不能破、接着做什么、
   以及已经踩过的坑。**入组第一天读这份。**
2. **[docs/report-pipeline.md](docs/report-pipeline.md)** —— 现在做成了什么、量到了什么数字。
3. **[docs/design/01-motivation.md](docs/design/01-motivation.md)** —— 为什么不是关键词搜索。
   往后 02–10 是完整设计推导。
4. `codesense/ql/frag.py` —— `Frag` 是唯一的类型，所有算子都是 `Frag -> Frag`。
5. `codesense/project.py` —— 主类，各层串起来的地方。

可视化：`docs/artifacts/codesense-internals.html` —— 索引内部构成、ICF 门槛、
Viterbi 格子、查询漏斗、证据链，每个数字都是 netty 上实测的。

---

## 分层

```
codesense/
├── lang/              怎么"读"一门语言        ← 扩展点
│   ├── base.py          Language 协议 + Declaration / Invocation / AnnotationUse
│   └── java/            scanner（tree-sitter）/ annotations / frameworks
├── text/              词怎么工作（语言无关）
│   ├── split.py         Splitter：delimiter + Viterbi 复合词切分
│   └── corpus.py        声明 → 训练语料
├── indexing/          怎么"建"产物
│   ├── pipeline.py      编排（只管顺序）
│   ├── postings.py      PostingTable，声明 → (term, field)
│   ├── graph.py         GraphBuilder + TypeTable，contains / calls 边
│   └── grounding.py     词表接地（lexical / vectors / finetune）
├── ql/                查询层，**契约上只用标准库**
│   ├── frag.py          Frag：带证据的代码子图
│   ├── operators/       eval_unit / hop / reach / only / top / degree / intent
│   ├── compile/         查询规划与脚本生成
│   └── script.py        白名单闸门 + 步数预算
├── llm/               模型适配器（llm → ql 单向依赖）
├── index.py           产物"是"什么（存取 + to_context）
├── search.py          三条查询路径
└── project.py         主类

legacy/                重写前的实现（只读归档，不参与构建/lint/测试）
```

依赖方向单向：`indexing` 用 `lang` / `text` / `ql` 的类型，反过来都不认识。

---

## 加一门语言

写一个 adapter 就够了，不用碰 `codesense/indexing`：

```python
from codesense.lang import LANGUAGES, Declaration

class GoLanguage:
    name = "go"
    file_globs = ("*.go",)
    skip_parts = ("/vendor/",)
    indexed_kinds = frozenset({"function", "struct"})
    container_kinds = frozenset({"struct"})

    def scan(self, source: str) -> list[Declaration]: ...
    def expansions(self): return {}      # 没有框架关系就返回空

LANGUAGES.register(GoLanguage())
```

`tests/unit/lang/test_registry.py` 在测试文件里现场定义一个玩具语言并索引它，
**没动 indexing 一行**。这条测试哪天要改 indexing 才能过，就说明扩展点失效了。

目前内置的 adapter 只有 **Java**（tree-sitter）。

---

## 三条查询路径

| 路径 | R@100 | 说明 |
|---|---|---|
| `codegen` | 55–58% | 把算子 spec 和**带 df 的词表**给模型，它自己写脚本。唯一能表达控制流的 |
| `planned` | 50–52% | 模型只做 NLP，统计定结构与顺序。计划可在执行前检视 |
| `lexical` | — | 不用模型。查询自己的词 + 接地表。没有 key 时跑的就是它 |

任何一条失败都**降级**到 lexical 而不是抛异常——一次 401 就让搜索死掉，
比悄悄少做一点更糟。

对照基线：只用查询里的字面词，R@100 是 **0%**。

---

## 产物

索引是一个**目录**而不是单文件，因为三份产物的生命周期不同：

| 文件 | 内容 | 什么时候要重建 |
|---|---|---|
| `meta.json` | 索引了什么、什么时候、用什么建的 | 每次 |
| `index.json` | 符号表 + 倒排 + 边 | 源码变了 |
| `expansion.json` | 词表接地（通用词 → 项目实际写法） | 接地策略变了 |

分开的好处：重跑接地不用改写 58 MB 的符号表，重扫源码不用丢掉花了几分钟算出来的接地表。
`meta.json` **最后写**——它的存在标志目录完整，所以中途崩溃留下的是"加载失败"
而不是"能加载但在骗你"。

netty 实测：**21 秒**，42 221 符号、603 733 posting、120 322 边。

---

## 外部依赖

建索引**不需要**编译目标项目，不需要 LSP，也不需要模型文件——AST 里免费的类型信息
就能把调用边的唯一解析率做到 44%。

可选：

| 用途 | 依赖 |
|---|---|
| LLM 查询路径（codegen / planned / intent） | 一个 OpenAI 兼容端点 + key |
| 向量接地（`--strategy vectors`） | `gensim` + 一份 fastText `.bin`（如 `cc.en.300.bin`，7 GB，约 8 GB 内存） |
| 微调接地（`--strategy finetune`） | 同上，约 20 GB 内存 |

**向量只在构建期用**，查询时只读几百 KB 的接地表，永远不加载模型。

---

## 日常命令

```bash
pytest                          # 全部（跳过 slow）
pytest tests/unit               # 只跑快的
pytest -m slow                  # 需要重依赖或要花钱的
ruff check . --fix
ruff format .
```

提交前这三条要过：`ruff check .`、`ruff format --check .`、`pytest`。
详见 [CONTRIBUTING.md](CONTRIBUTING.md)。

**源码全英文，文档全中文**——这是刻意的。

---

## 已知问题

- **`652a37f` 里有明文 API key，且已在远端**。历史没重写，需要去控制台吊销重发。
- `tests/fixtures/ql/mini_index.json` 早于注解/修饰符抽取，导致 gap1 / gap3
  两条缺口测试对着旧产物断言——实际上这两项**已经进索引了**（netty 上
  34 228 条 annotation posting、54 546 条 modifier posting）。
- benchmark 里 netty-backpressure 六条路径、三次采样全 0%。
- 接地的 subseq 规则噪音多过信号（`abandoned → add`）。

完整清单见 [HANDOFF.md](HANDOFF.md#5-接着做什么)。
