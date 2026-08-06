# Finetune Runnable Demo Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用尽量少的代码实现 `lightweight`、`full_force`、`warn_full` 三种 finetune profile，并把可搜索的 expansion 和项目 Word2Vec 保存到 `.codesense`。

**Architecture:** 保留 `grounding.py` 的 lexical/vectors 逻辑，只把项目级训练放入 `codesense/indexing/finetune/`。紧凑基础包是外部输入；本次不实现离线制备、增量训练、事务恢复、资源报告或通用插件框架。

**Tech Stack:** Python 3.10、NumPy、Gensim Word2Vec/FastText、JSON、pytest、ruff。

## Global Constraints

- 默认 profile 是 `lightweight`，不得加载完整 Facebook FastText。
- 默认内存预算是 2048MB；词表估算超限时直接缩减上下文或报错。
- `warn_full` 必须显式设置 `allow_unsafe_full=True`。
- 第一版只支持英语查询词。
- 项目模型保存在 `.codesense/embedding/project.model`。
- 普通 `Project.open().search()` 不加载 embedding 模型。
- 参数沿 CLI → `Project.build` → finetune config 注入。
- 不提交模型权重或运行产物。

---

### Task 1: 删除重型原型并建立最小数据契约

**Files:**
- Modify: `codesense/indexing/finetune/config.py`
- Modify: `codesense/indexing/finetune/corpus.py`
- Modify: `codesense/indexing/finetune/artifacts.py`
- Delete: `scripts/build_compact_grounding_model.py`
- Delete: `resources/grounding/projects.json`
- Delete: `resources/grounding/queries.jsonl`
- Test: `tests/unit/indexing/finetune/test_config.py`

**Interfaces:**
- Produces: `FinetuneConfig`、`CorpusStore`、`ArtifactPaths`、`write_manifest(...)`。
- Consumes: CLI/`Project.build` 参数和扫描阶段句子。

- [ ] **Step 1: 回退已提交的重型原型代码，保留收缩后的 spec**

运行：

```bash
git revert --no-commit bced070^..fcc917e
```

删除未提交的旧 integration fixture。确认 `git diff --stat` 不再包含离线 builder、
固定项目资源、增量 corpus diff、原子 transaction 和训练 report。

- [ ] **Step 2: 写最小配置测试**

```python
def test_warn_full_requires_confirmation():
    with pytest.raises(ValueError, match="allow_unsafe_full"):
        FinetuneConfig(profile="warn_full", model_path=Path("cc.en.bin"))

def test_default_budget_is_two_gibibytes():
    assert FinetuneConfig(model_path=Path("compact")).memory_budget_mb == 2048
```

- [ ] **Step 3: 实现最小数据类型**

`FinetuneConfig` 只保留 `profile`、`model_path`、`artifact_dir`、`memory_budget_mb`、
`epochs`、`workers`、`allow_unsafe_full`、`preserve_full_model`、`strict_profile`。

`CorpusStore` 只实现 `append(file, sentences)` 和可重复的 `sentences()`；不计算 hash、
delta 或 fingerprint。

`ArtifactPaths` 只暴露 `embedding`、`project_model`、`baseline_vectors`、
`baseline_vocabulary`、`manifest`；`write_manifest` 直接写 JSON。

- [ ] **Step 4: 运行测试并提交**

```bash
conda run -n codesearch pytest tests/unit/indexing/finetune/test_config.py -v
git add codesense/indexing/finetune docs resources scripts tests
git commit -m "refactor: reduce finetune to demo contracts"
```

---

### Task 2: 词表、初始化和紧凑 Word2Vec

**Files:**
- Create: `codesense/indexing/finetune/vocabulary.py`
- Create: `codesense/indexing/finetune/initializers.py`
- Create: `codesense/indexing/finetune/trainer.py`
- Test: `tests/unit/indexing/finetune/test_model.py`

**Interfaces:**
- Produces: `plan_vocabulary(...) -> VocabularyPlan`。
- Produces: `initialize_terms(...) -> dict[str, np.ndarray]`。
- Produces: `train_word2vec(...) -> ProjectEmbedding`。

- [ ] **Step 1: 写词表预算和初始化顺序测试**

```python
def test_targets_survive_budget_pruning():
    plan = plan_vocabulary(
        general=("permission",), project_df={"perms": 3, "typo": 1},
        targets={"perms"}, vector_size=300, memory_budget_mb=1,
    )
    assert "perms" in plan.project

def test_canonical_precedes_random():
    seeds = initialize_terms(
        terms=("perms",), base={"permission": np.array([1., 0.])},
        canonical={"perms": "permission"}, components={}, contexts={},
        subword=None, vector_size=2,
    )
    assert np.array_equal(seeds["perms"], np.array([1., 0.], dtype=np.float32))
```

- [ ] **Step 2: 实现直接函数，不建立策略类**

`plan_vocabulary` 使用 `(词数 × 维度 × 4 × 3 × 1.35)` 估算内存；保留通用词和目标
词，按 df 从低到高删除非目标项目词。

`initialize_terms` 按精确命中、规范形、组成词、subword、上下文、固定随机顺序返回
归一化向量；不返回置信度或审计对象。

- [ ] **Step 3: 写并实现冻结回归**

```python
def test_general_vector_is_frozen(tmp_path):
    embedding = train_word2vec(
        sentences=[["permission", "perms"]] * 10,
        seeds={"permission": np.array([1., 0.]), "perms": np.array([.8, .2])},
        frozen={"permission"}, epochs=1, workers=1,
    )
    assert np.array_equal(embedding.vector("permission"), np.array([1., 0.]))
    embedding.save(tmp_path / "project.model")
```

实现只使用 `build_vocab_from_freq`、seed copy、`vectors_lockf` 和一次 `train`；不实现
alignment、连续 trainability、resume 或增量 vocab update。

- [ ] **Step 4: 运行测试并提交**

```bash
conda run -n codesearch pytest tests/unit/indexing/finetune/test_model.py -v
git add codesense/indexing/finetune tests/unit/indexing/finetune/test_model.py
git commit -m "feat: train minimal compact project model"
```

---

### Task 3: 三种向量来源和双空间 expansion

**Files:**
- Create: `codesense/indexing/finetune/providers.py`
- Create: `codesense/indexing/finetune/expansion.py`
- Test: `tests/unit/indexing/finetune/test_providers.py`
- Test: `tests/unit/indexing/finetune/test_expansion.py`

**Interfaces:**
- Produces: `SeedVectors` 和 `prepare_vectors(...)`。
- Produces: `build_vector_expansion(...)`。

- [ ] **Step 1: 写 lightweight 和 full_force 边界测试**

```python
def test_compact_provider_selects_requested_rows(tmp_path):
    seeds = prepare_compact(compact_dir, ("permission",), tmp_path)
    assert seeds.vocabulary == ("permission",)

def test_full_force_parent_reads_after_runner_returns(tmp_path):
    events = []
    prepare_full_force(model, ("permission",), tmp_path, runner=FakeRunner(events))
    assert events == ["run", "return", "read"]
```

- [ ] **Step 2: 实现三个直接分支**

`prepare_compact` 读取 `compact-vocabulary.txt`、`baseline-vectors.npy` 和 manifest 中的
版本/维度，只检查文件存在和矩阵行数。

`prepare_full_force` 默认用 `spawn` 子进程加载 `load_facebook_vectors`，写出请求词的
紧凑矩阵，父进程在 `join` 后读取。可注入 runner 只用于测试。

`warn_full` 直接复用 `grounding.VectorSpace.load(...).finetune(...)`；不增加 provider
基类，不实现自动 fallback。保存完整模型只由 `preserve_full_model` 控制。

- [ ] **Step 3: 写并实现双空间取最大值**

```python
def test_baseline_survives_adapted_regression():
    result = build_vector_expansion(
        keys=("department",), targets=("dept",),
        baseline={"department": [1., 0.], "dept": [.9, .1]},
        adapted={"department": [1., 0.], "dept": [0., 1.]},
        min_cosine=.55, max_targets=4,
    )
    assert result["department"][0][2] == "vector-base"
```

实现按 2048 个 key 分块计算余弦；baseline 上限 0.60，adapted 上限 0.65；同一目标
取较高分并按 `(-score, target)` 排序。

- [ ] **Step 4: 运行测试并提交**

```bash
conda run -n codesearch pytest tests/unit/indexing/finetune/test_providers.py tests/unit/indexing/finetune/test_expansion.py -v
git add codesense/indexing/finetune tests/unit/indexing/finetune
git commit -m "feat: add finetune vector profiles"
```

---

### Task 4: 接入 Project.build 并完成 Demo

**Files:**
- Modify: `codesense/indexing/finetune/__init__.py`
- Modify: `codesense/indexing/grounding.py`
- Modify: `codesense/indexing/pipeline.py`
- Modify: `codesense/project.py`
- Modify: `codesense/index.py`
- Modify: `codesense/cli.py`
- Test: `tests/integration/indexing/test_finetune_demo.py`

**Interfaces:**
- Produces: `run_finetune(...) -> FinetuneResult`。
- Persists: `.codesense/embedding/project.model`、baseline、manifest 和 `expansion.json`。

- [ ] **Step 1: 写 lightweight 端到端失败测试**

构建 tiny toy 项目和二维 compact fixture，monkeypatch 两个 Facebook loader 为抛错，
然后断言：

```python
project = Project.build(source, index_dir=index_dir, strategy="finetune", model_path=compact)
assert project.index.meta.grounding_profile == "lightweight"
assert "perms" in [t for t, _, _ in project.index.expansion["permission"]]
assert (index_dir / "embedding" / "project.model").is_file()
```

- [ ] **Step 2: 实现最短构建数据流**

扫描阶段对 finetune 使用临时 `CorpusStore`，避免保留全部句子列表。
`run_finetune` 顺序固定为：规划词表 → 准备基础向量 → 初始化 → Word2Vec 训练 →
保存四个 embedding 产物 → 生成 vector expansion。

`grounding.py` 只在 finetune 分支调用该函数，并通过现有 `_merge` 合并 lexical。
非 strict 失败返回 lexical 和 `degraded` 状态；strict 模式重新抛出。

- [ ] **Step 3: 接通 CLI/API 参数**

保留：

```text
--finetune-profile
--memory-budget-mb
--epochs
--allow-unsafe-full
--preserve-full-model
--strict-profile
```

删除 `--compact-vectors`、`rebuild_ratio` 等 Demo 不需要的参数。

- [ ] **Step 4: 验证并提交**

```bash
conda run -n codesearch pytest tests/integration/indexing/test_finetune_demo.py -v
conda run -n codesearch ruff check .
conda run -n codesearch ruff format --check .
conda run -n codesearch pytest
git add codesense tests docs
git commit -m "feat: integrate lightweight finetune demo"
```

最终用 `git diff --stat main...HEAD` 核对代码规模；生产实现目标不超过约 1200 行，
测试只保留能证明关键边界的用例。
