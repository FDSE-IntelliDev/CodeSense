# 贡献指南

组内协作的通用规范见 [DEV-COOKBOOK](https://github.com/FDSE-IntelliDev/DEV-COOKBOOK)
和[组内 Wiki](https://github.com/FDSE-IntelliDev/TEAM-WIKI)，这里只写与本项目相关的部分。

---

## 环境

```bash
conda activate codesearch
pip install -e ".[dev]"
export CODESENSE_API_KEY=sk-...      # 或者 cp .env.example .env 后 source
```

---

## 提交前自查

```bash
ruff check .            # 静态检查
ruff format --check .   # 格式
pytest                  # 测试（默认跳过 slow）
```

三条都过再提 PR。

`pytest` 默认跳过标了 `slow` 的测试——那些要装齐 tree-sitter / gensim / spacy
才跑得动。装齐了跑 `pytest -m slow` 补上。

**改动了检索、分词、缩写、过滤这些会影响结果的逻辑时，务必跑一次
`pytest -m slow`。** 那里面是 golden 测试，比对的是真实输入下的完整输出——
它专门抓「代码还能跑，但结果悄悄变了」这种最难发现的回归。
缺离线产物或重型依赖时它会 skip 而不是 fail，所以绿灯不等于跑过了，
看一眼 `-rs` 的跳过原因。

### ruff 现在拦得住什么

这个仓库刚从「脚本堆」整理过来，老模块里积压着约 1900 条风格问题。
`pyproject.toml` 里为它们挂了**逐条列出的规则号豁免**，所以：

- **新写的代码**（`codesense/config.py`、`scripts/`、`tests/`、`evaluation/`）
  受全套规则约束，`ruff check` 对它们是真门禁；
- **老模块**只豁免了名单上那些条目，`F821` 未定义名这类真 bug 照样拦得住。

改到哪个老模块，就顺手把它涉及的那几条清掉，从豁免名单里删掉对应路径。
**别整体放着不管，也别一次性全修**——后者会产生一个覆盖全仓库的 diff，
把你真正的改动淹掉。

---

## 代码自查清单

写完新代码，对照过一遍：

**结构**
- [ ] 有配置或状态的逻辑写成了类，而不是「一排函数 + 一个 cfg 字典透传」
- [ ] 没有出现全是 `@staticmethod` 的类（那是伪装成类的模块，直接写函数）
- [ ] 新增的可替换组件（filter / executor / parser）有共同接口，不是自成一派
- [ ] 没有为了选实现而新增 `if/elif` 分支

**数据**
- [ ] 在模块之间流动的数据结构是 `frozen=True` 的 dataclass，不是裸 dict
- [ ] 函数签名里没有「万能 cfg 字典」，只收自己真正需要的参数
- [ ] 没有在函数里原地修改传入的对象

**边界**
- [ ] Executor 读的是 `*_semql.json` 计划，没有回头去解析 SemCon 原始字段
- [ ] 新增的可调参数走构造函数注入，没写死在代码里、也没塞进配置文件
- [ ] 模块顶层没有执行逻辑（只有定义），`import` 它不会读盘、不会 print
- [ ] 没有 `/Users/...`、`/home/xxx/...` 这类只在你机器上成立的绝对路径
- [ ] `codesense/` 没有反向 import `evaluation/`

**实验**
- [ ] 实验目录有 README，问题/假设是**跑之前**写的
- [ ] 消融实验与对照组只差一个变量
- [ ] 跑之前工作区是干净的（带着未提交改动跑出来的结果没法复现）

**其他**
- [ ] 新逻辑有对应测试；纯逻辑放 `tests/unit/`，要 IO 的放 `tests/integration/`
- [ ] 改了会影响结果的逻辑，跑过 `pytest -m slow`；若 golden 变了，
      确认那是**有意**的行为变更再重录，并在 commit 里说明
- [ ] 需要重依赖或真实 LLM 调用的测试标了 `@pytest.mark.slow`
- [ ] 脚本里没有业务逻辑（值得测试的代码不该待在 `scripts/`）
- [ ] **没有提交密钥**、数据、模型权重、`output/` 产物、`slides/` 素材

---

## 绝对不要提交的东西

```
.env、任何 sk- 开头的字符串
output/          索引与检索产物，几十 MB
slides/          答辩 PPT 与素材
runs/            实验归档
```

这些都已经在 `.gitignore` 里。**密钥泄露到 git 历史里就洗不掉了**——
本仓库已经发生过一次（`definition.py` 里的 dashscope key），
补救只能是去控制台吊销重发。提交前扫一眼 `git diff --cached`。

---

## Commit message

用 [Conventional Commits](https://www.conventionalcommits.org/)：

```
feat: 新增基于调用链距离的候选补齐
fix: 修正 surface executor 的 exclude 集合减法
refactor: 把 filters 抽到统一的 Filter 接口
test: 补充 SemQL 字段抽取的测试
docs: 更新架构说明
exp: 消融实验 - 去掉聚类过滤
chore: 把产物目录移出版本库
```

---

## 记 CHANGELOG 的时机

只有在**完整实现一个功能、或确定一个功能的实现方案之后**才写 `CHANGELOG.md`，
不用每做一次细节修改就同步。
