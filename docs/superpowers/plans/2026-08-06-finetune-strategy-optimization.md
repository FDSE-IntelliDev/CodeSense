# Finetune Strategy Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 CodeSense 默认 `finetune` 改造成峰值内存不超过 2GB 的项目级紧凑 Word2Vec 适配流程，同时保留 `full_force` 初始化和显式危险的 `warn_full` 完整 FastText 训练入口。

**Architecture:** 保留 `codesense/indexing/grounding.py` 作为现役 lexical/vectors 实现和 finetune 调度入口；新增 `codesense/indexing/finetune/` 子包承载配置、词表、provider、初始化、训练、语料、产物和双空间 expansion。离线基础模型制备与用户项目适配共用同一 manifest 契约，搜索阶段继续只读取 `expansion.json`。

**Tech Stack:** Python 3.10、NumPy、Gensim Word2Vec/FastText、pytest、ruff、JSON/JSONL、`multiprocessing` spawn 子进程。

## Global Constraints

- `lightweight` 正常峰值目标约 1GB，硬上限 2GB；不得加载 `cc.en.300.bin`。
- 默认 profile 是 `lightweight`；`full_force` 只用完整 FastText 做候选提取和初始化。
- `warn_full` 直接训练完整 FastText，必须同时显式设置 `allow_unsafe_full=True`。
- `warn_full` 默认不保存完整模型；只有 `preserve_full_model=True` 才保存。
- 第一版只支持英语查询词。
- 用户项目构建只扫描当前项目，不重新扫描离线基础模型使用的开源项目。
- 当前 `grounding.py` 不拆分；新实现只放入 `codesense/indexing/finetune/`。
- 普通 `Project.open().search()` 不得加载 embedding 模型。
- 参数必须沿 CLI → `Project.build` → 构造函数注入，不进入配置文件，不硬编码本机绝对路径。
- 模型权重、训练产物和大型语料不得提交到 Git。
- `warn_full` 的真实完整训练不加入自动化测试或发布门禁；其余公共逻辑必须测试。

## File Map

**Create:**

- `codesense/indexing/finetune/__init__.py`：公开 finetune 运行入口与稳定类型。
- `codesense/indexing/finetune/config.py`：profile、资源预算、训练参数和校验。
- `codesense/indexing/finetune/vocabulary.py`：基础/项目词表分类、裁剪和内存预估。
- `codesense/indexing/finetune/providers.py`：compact、full-force、unsafe-full provider 契约。
- `codesense/indexing/finetune/initializers.py`：项目 OOV 六级初始化。
- `codesense/indexing/finetune/trainer.py`：Word2Vec 构造、lockf、训练和恢复。
- `codesense/indexing/finetune/corpus.py`：JSONL 流式语料、fingerprint 和增量差异。
- `codesense/indexing/finetune/artifacts.py`：manifest/report、路径布局和原子保存。
- `codesense/indexing/finetune/expansion.py`：baseline/adapted 相似度与结果合并。
- `scripts/build_compact_grounding_model.py`：离线基础模型制备的薄入口。
- `resources/grounding/projects.json`：固定开源项目及固定 commit 清单。
- `resources/grounding/queries.jsonl`：固定英文代码搜索查询基准。
- `tests/unit/indexing/finetune/`：纯逻辑和 fake provider 测试。
- `tests/integration/indexing/test_finetune_pipeline.py`：小模型端到端测试。

**Modify:**

- `codesense/indexing/grounding.py`：保留现有实现，只增加 finetune 调度和结果合并。
- `codesense/indexing/pipeline.py`：增加可选 corpus observer，finetune 时不保留完整句子列表。
- `codesense/project.py`：注入 profile 参数、选择 artifact 目录并记录实际执行状态。
- `codesense/cli.py`：增加 profile、预算、危险确认和持久化参数。
- `codesense/index.py`：为 grounding profile/status/reason 增加向后兼容的 meta 字段。
- `scripts/debug_search.py`：增加 profile 和基础模型类型的可编辑常量。
- `tests/unit/indexing/test_grounding.py`：验证旧策略兼容和新调度边界。
- `tests/unit/test_cli.py`、`tests/unit/test_index.py`、`tests/unit/test_debug_search_script.py`：覆盖参数和持久化契约。
- `docs/superpowers/specs/2026-08-06-finetune-strategy-optimization-design.md`：实现后标记状态并补充实测数据。

---

### Task 1: Profile 配置与公开参数流水线

**Files:**
- Create: `codesense/indexing/finetune/__init__.py`
- Create: `codesense/indexing/finetune/config.py`
- Modify: `codesense/indexing/grounding.py:101-136`
- Modify: `codesense/project.py:77-147`
- Modify: `codesense/cli.py:63-92,198-231`
- Test: `tests/unit/indexing/finetune/test_config.py`
- Test: `tests/unit/test_cli.py`

**Interfaces:**
- Produces: `FINETUNE_PROFILES`, `FinetuneConfig`。
- Produces: `Project.build(..., finetune_profile, memory_budget_mb, allow_unsafe_full, preserve_full_model, strict_profile, rebuild_ratio)`。
- Consumes: existing `GroundingConfig.strategy` and `GroundingConfig.model_path`。

- [ ] **Step 1: Write failing configuration tests**

```python
from pathlib import Path

import pytest

from codesense.indexing.finetune.config import FinetuneConfig


def test_lightweight_is_the_default() -> None:
    config = FinetuneConfig(model_path=Path("compact-base"))
    assert config.profile == "lightweight"
    assert config.memory_budget_mb == 2048


def test_warn_full_requires_explicit_unsafe_permission() -> None:
    with pytest.raises(ValueError, match="allow_unsafe_full"):
        FinetuneConfig(profile="warn_full", model_path=Path("cc.en.300.bin"))


def test_preserve_full_is_only_valid_for_warn_full() -> None:
    with pytest.raises(ValueError, match="preserve_full_model"):
        FinetuneConfig(
            profile="full_force",
            model_path=Path("cc.en.300.bin"),
            preserve_full_model=True,
        )
```

- [ ] **Step 2: Run the tests and verify they fail because the package does not exist**

Run: `conda run -n codesearch pytest tests/unit/indexing/finetune/test_config.py -v`

Expected: FAIL with `ModuleNotFoundError: codesense.indexing.finetune`.

- [ ] **Step 3: Implement the immutable configuration contract**

```python
# codesense/indexing/finetune/config.py
from dataclasses import dataclass
from pathlib import Path

FINETUNE_PROFILES = ("lightweight", "full_force", "warn_full")


@dataclass(frozen=True, slots=True)
class FinetuneConfig:
    profile: str = "lightweight"
    model_path: Path | None = None
    artifact_dir: Path | None = None
    memory_budget_mb: int = 2048
    epochs: int = 2
    workers: int = 8
    allow_unsafe_full: bool = False
    preserve_full_model: bool = False
    strict_profile: bool = False
    rebuild_ratio: float = 0.20

    def __post_init__(self) -> None:
        if self.profile not in FINETUNE_PROFILES:
            raise ValueError(f"profile must be one of {FINETUNE_PROFILES}")
        if self.model_path is None:
            raise ValueError("finetune needs model_path")
        if self.memory_budget_mb <= 0:
            raise ValueError("memory_budget_mb must be positive")
        if not 0.0 <= self.rebuild_ratio <= 1.0:
            raise ValueError("rebuild_ratio must be between 0 and 1")
        if self.profile == "warn_full" and not self.allow_unsafe_full:
            raise ValueError("warn_full needs allow_unsafe_full=True")
        if self.preserve_full_model and self.profile != "warn_full":
            raise ValueError("preserve_full_model is only valid for warn_full")
```

Export `FinetuneConfig` and `FINETUNE_PROFILES` from `finetune/__init__.py`.

- [ ] **Step 4: Thread explicit arguments through API and CLI**

Add to `Project.build`:

```python
finetune_profile: str = "lightweight",
memory_budget_mb: int = 2048,
allow_unsafe_full: bool = False,
preserve_full_model: bool = False,
strict_profile: bool = False,
rebuild_ratio: float = 0.20,
```

Construct `FinetuneConfig` only when `strategy == "finetune"`. Add CLI flags:

```text
--finetune-profile {lightweight,full_force,warn_full}
--memory-budget-mb 2048
--allow-unsafe-full
--preserve-full-model
--strict-profile
--rebuild-ratio 0.20
```

Keep `--vectors` as the external spelling for this compatibility phase; update its help so
`lightweight` expects a compact base directory and the other profiles expect a FastText `.bin`.

- [ ] **Step 5: Verify configuration and CLI tests pass**

Run: `conda run -n codesearch pytest tests/unit/indexing/finetune/test_config.py tests/unit/test_cli.py -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add codesense/indexing/finetune/__init__.py codesense/indexing/finetune/config.py codesense/indexing/grounding.py codesense/project.py codesense/cli.py tests/unit/indexing/finetune/test_config.py tests/unit/test_cli.py
git commit -m "feat: add finetune resource profiles"
```

---

### Task 2: Manifest、训练报告与原子产物目录

**Files:**
- Create: `codesense/indexing/finetune/artifacts.py`
- Modify: `codesense/index.py:49-73`
- Test: `tests/unit/indexing/finetune/test_artifacts.py`
- Test: `tests/unit/test_index.py`

**Interfaces:**
- Consumes: `FinetuneConfig.profile` and `FinetuneConfig.artifact_dir`。
- Produces: `EmbeddingManifest`, `TrainingReport`, `ArtifactPaths`, `ArtifactTransaction`。
- Produces: backward-compatible `IndexMeta.grounding_profile/status/reason` fields。

- [ ] **Step 1: Write failing round-trip and rollback tests**

```python
from pathlib import Path

from codesense.indexing.finetune.artifacts import (
    ArtifactPaths,
    ArtifactTransaction,
    EmbeddingManifest,
)


def test_manifest_round_trips(tmp_path: Path) -> None:
    paths = ArtifactPaths(tmp_path / ".codesense")
    manifest = EmbeddingManifest(
        requested_profile="full_force",
        effective_profile="lightweight",
        status="degraded",
        reason="extractor failed",
        base_model_sha256="abc",
        base_vocabulary_version="v1",
        vector_size=300,
        project_corpus_fingerprint="corpus",
        project_vocabulary_size=10,
        general_vocabulary_size=20,
        epochs_completed=2,
    )
    paths.write_manifest(manifest)
    assert paths.read_manifest() == manifest


def test_failed_transaction_keeps_the_old_embedding(tmp_path: Path) -> None:
    paths = ArtifactPaths(tmp_path / ".codesense")
    paths.embedding.mkdir(parents=True)
    (paths.embedding / "project.model").write_text("old")
    with ArtifactTransaction(paths) as transaction:
        (transaction.embedding / "project.model").write_text("new")
    assert (paths.embedding / "project.model").read_text() == "old"
```

- [ ] **Step 2: Run tests and verify missing artifact types**

Run: `conda run -n codesearch pytest tests/unit/indexing/finetune/test_artifacts.py -v`

Expected: FAIL importing `codesense.indexing.finetune.artifacts`.

- [ ] **Step 3: Implement frozen serialized records and path layout**

Use frozen dataclasses. `ArtifactPaths` must expose exact paths:

```python
embedding = index_dir / "embedding"
staging = index_dir / "embedding.next"
backup = index_dir / "embedding.prev"
manifest = embedding / "manifest.json"
report = embedding / "training-report.json"
project_model = embedding / "project.model"
baseline_vectors = embedding / "baseline-vectors.npy"
baseline_vocabulary = embedding / "baseline-vocabulary.json"
corpus = embedding / "corpus.jsonl"
full_model = embedding / "full-fasttext.model"
```

Serialize dataclasses with `asdict`; parse all fields explicitly rather than accepting unknown
JSON keys. Write files through a same-directory temporary file and `os.replace`.

- [ ] **Step 4: Implement commit/rollback and recovery marker**

`ArtifactTransaction.commit()` must:

1. validate that staged manifest and required model files exist;
2. rename current `embedding/` to `embedding.prev/`;
3. rename `embedding.next/` to `embedding/`;
4. delete `embedding.prev/` only after success.

Entering a new transaction must recover an interrupted previous commit by preferring a valid
`embedding/`, otherwise restoring `embedding.prev/`. Never recursively delete an unresolved
directory whose role has not been validated.

- [ ] **Step 5: Add backward-compatible metadata fields**

```python
grounding_profile: str = ""
grounding_status: str = "ready"
grounding_reason: str = ""
```

Do not bump `FORMAT_VERSION`; defaults allow old `meta.json` files to load.

- [ ] **Step 6: Run tests**

Run: `conda run -n codesearch pytest tests/unit/indexing/finetune/test_artifacts.py tests/unit/test_index.py -v`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add codesense/indexing/finetune/artifacts.py codesense/index.py tests/unit/indexing/finetune/test_artifacts.py tests/unit/test_index.py
git commit -m "feat: persist finetune artifacts atomically"
```

---

### Task 3: 流式项目语料和增量差异

**Files:**
- Create: `codesense/indexing/finetune/corpus.py`
- Modify: `codesense/indexing/pipeline.py:68-157`
- Modify: `codesense/project.py:108-145`
- Test: `tests/unit/indexing/finetune/test_corpus.py`
- Test: `tests/unit/indexing/test_pipeline.py`

**Interfaces:**
- Produces: `CorpusRecord`, `CorpusSnapshot`, `CorpusDelta`, `CorpusStore`，包括仅供测试和
  离线工具复用的 `CorpusStore.from_records(path, records)` 构造器。
- Produces: `CorpusStore.observe(file, digest, sentences)` callback accepted by `build_index`。
- Consumes: per-file sentences already produced by `corpus_sentences()`。

- [ ] **Step 1: Write failing streaming and delta tests**

```python
from pathlib import Path

from codesense.indexing.finetune.corpus import CorpusStore


def test_corpus_is_reiterable_without_materialising_all_sentences(tmp_path: Path) -> None:
    store = CorpusStore(tmp_path / "corpus.jsonl")
    store.observe("a.py", "hash-a", [["retry", "request"], ["backoff", "delay"]])
    store.close()
    assert list(store.sentences()) == [
        ["retry", "request"],
        ["backoff", "delay"],
    ]
    assert list(store.sentences()) == list(store.sentences())


def test_removing_twenty_percent_requests_reinitialisation(tmp_path: Path) -> None:
    old = CorpusStore.from_records(
        tmp_path / "old.jsonl",
        [("a.py", "a", [["one"]] * 8), ("b.py", "b", [["two"]] * 2)],
    )
    new = CorpusStore.from_records(
        tmp_path / "new.jsonl",
        [("a.py", "a", [["one"]] * 8)],
    )
    assert new.diff(old, rebuild_ratio=0.20).requires_rebuild
```

- [ ] **Step 2: Run and confirm failure**

Run: `conda run -n codesearch pytest tests/unit/indexing/finetune/test_corpus.py -v`

Expected: FAIL importing `CorpusStore`.

- [ ] **Step 3: Implement JSONL corpus storage**

Each line has this stable shape:

```json
{"file":"src/a.py","hash":"sha256","tokens":["retry","request"]}
```

`CorpusStore.sentences(files: frozenset[str] | None = None)` must open and stream the file on
every iteration. `snapshot()` returns corpus fingerprint, per-file hashes, token counts and total
sentence count without exposing mutable dictionaries.

- [ ] **Step 4: Add an optional observer to the indexing pipeline**

Extend `build_index` with:

```python
corpus_observer: Callable[[str, str, Sequence[Sequence[str]]], None] | None = None,
```

Read source text once, hash its UTF-8 bytes, build that file's sentence list, and call the observer.
When an observer is supplied, do not extend `BuildResult.sentences`; lexical/vector builds retain
the current list behavior for compatibility.

- [ ] **Step 5: Implement delta selection**

`CorpusDelta` contains `changed_files`, `removed_files`, `changed_token_ratio` and
`requires_rebuild`. New/modified files form the incremental training iterator. Any removal or
combined changed token ratio at/above `rebuild_ratio` requests full reinitialisation.

- [ ] **Step 6: Run focused tests**

Run: `conda run -n codesearch pytest tests/unit/indexing/finetune/test_corpus.py tests/unit/indexing/test_pipeline.py -v`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add codesense/indexing/finetune/corpus.py codesense/indexing/pipeline.py codesense/project.py tests/unit/indexing/finetune/test_corpus.py tests/unit/indexing/test_pipeline.py
git commit -m "feat: stream finetune corpus by source file"
```

---

### Task 4: 项目词表规划和 2GB 预算裁剪

**Files:**
- Create: `codesense/indexing/finetune/vocabulary.py`
- Test: `tests/unit/indexing/finetune/test_vocabulary.py`

**Interfaces:**
- Consumes: base manifest, all project term dfs, discriminating target set, lexical expansion and corpus snapshot。
- Produces: `VocabularyEntry`, `VocabularyPlan`, `MemoryEstimate`,
  `MemoryBudgetExceeded`, `plan_vocabulary(...)`。

- [ ] **Step 1: Write failing classification and pruning tests**

```python
from codesense.indexing.finetune.vocabulary import plan_vocabulary


def test_plan_keeps_general_anchors_and_discriminating_project_targets() -> None:
    plan = plan_vocabulary(
        base_terms={"permission": "general-query", "cache": "code-domain"},
        project_terms={"perms": 8, "typo": 1},
        targets={"perms"},
        lexical={"permission": [("perms", 0.75, "abbrev")]},
        vector_size=300,
        memory_budget_mb=2048,
    )
    assert "permission" in plan.general_terms
    assert "perms" in plan.project_terms
    assert "typo" not in plan.project_terms


def test_plan_drops_context_before_targets_when_over_budget() -> None:
    plan = plan_vocabulary(
        base_terms={"permission": "general-query"},
        project_terms={"perms": 8, "context": 2},
        targets={"perms"},
        lexical={},
        vector_size=300,
        memory_budget_mb=1,
    )
    assert "perms" in plan.project_terms
    assert "context" in plan.dropped_terms
```

- [ ] **Step 2: Run and confirm failure**

Run: `conda run -n codesearch pytest tests/unit/indexing/finetune/test_vocabulary.py -v`

Expected: FAIL importing `plan_vocabulary`.

- [ ] **Step 3: Implement immutable vocabulary records**

`VocabularyEntry` records term, groups, df, initializer hint, keep priority and whether it is an
expansion target. `VocabularyPlan` has these exact fields and deterministic ordering:

```python
@dataclass(frozen=True, slots=True)
class VocabularyPlan:
    general_terms: tuple[str, ...]
    project_terms: tuple[str, ...]
    context_terms: tuple[str, ...]
    extended_candidates: tuple[str, ...]
    dropped_terms: tuple[str, ...]
    estimated_bytes: int
```

- [ ] **Step 4: Implement conservative memory estimation**

Use this lower-bound formula and add a 35% Python/Gensim overhead margin:

```python
matrix_bytes = vocabulary_size * vector_size * 4
estimated_bytes = int((matrix_bytes * 2 + baseline_bytes + subword_bytes) * 1.35)
```

Prune in this exact order: low-frequency non-target project context, low-priority extended
candidates, then non-forced code-domain terms. Never prune lexical targets, forced anchors or the
fixed general-query core. If the protected core exceeds budget, raise `MemoryBudgetExceeded` so
the orchestrator can degrade honestly.

- [ ] **Step 5: Run tests**

Run: `conda run -n codesearch pytest tests/unit/indexing/finetune/test_vocabulary.py -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add codesense/indexing/finetune/vocabulary.py tests/unit/indexing/finetune/test_vocabulary.py
git commit -m "feat: plan finetune vocabulary within budget"
```

---

### Task 5: Compact provider 和六级项目词初始化

**Files:**
- Create: `codesense/indexing/finetune/providers.py`
- Create: `codesense/indexing/finetune/initializers.py`
- Test: `tests/unit/indexing/finetune/test_providers.py`
- Test: `tests/unit/indexing/finetune/test_initializers.py`

**Interfaces:**
- Consumes: `VocabularyPlan`, compact-base manifest/vector files, lexical canonical mapping and project contexts。
- Produces: `SeedSpace`, `VectorProvider` protocol, `CompactProvider`, `InitializedTerm`, `ProjectTermInitializer`。

- [ ] **Step 1: Write failing initializer precedence tests**

```python
import numpy as np

from codesense.indexing.finetune.initializers import ProjectTermInitializer


def test_canonical_form_beats_subwords_and_context() -> None:
    permission = np.array([1.0, 0.0], dtype=np.float32)
    initializer = ProjectTermInitializer(
        base={"permission": permission},
        canonical={"perms": "permission"},
        components={},
        subwords=lambda term: np.array([0.0, 1.0], dtype=np.float32),
        contexts={"perms": ["role"]},
    )
    result = initializer.initialize("perms")
    assert result.reason == "canonical"
    assert np.allclose(result.vector, permission)


def test_unknown_term_falls_back_to_seeded_random() -> None:
    initializer = ProjectTermInitializer.empty(vector_size=3, random_seed=7)
    first = initializer.initialize("unknown").vector
    second = initializer.initialize("unknown").vector
    assert np.array_equal(first, second)
```

- [ ] **Step 2: Run and confirm failure**

Run: `conda run -n codesearch pytest tests/unit/indexing/finetune/test_initializers.py tests/unit/indexing/finetune/test_providers.py -v`

Expected: FAIL importing the new modules.

- [ ] **Step 3: Define provider and seed-space contracts**

```python
class VectorProvider(Protocol):
    def prepare(self, plan: VocabularyPlan, destination: Path) -> SeedSpace: ...


@dataclass(frozen=True, slots=True)
class SeedSpace:
    vocabulary: tuple[str, ...]
    vectors_path: Path
    vector_size: int
    base_model_sha256: str
    vocabulary_version: str
```

`CompactProvider` validates compact manifest version and SHA, memory-maps baseline `.npy`, selects
only the planned rows and writes a project-local baseline. It must never import or call
`load_facebook_model`/`load_facebook_vectors`.

- [ ] **Step 4: Implement exact initializer precedence**

Use: exact base → canonical form → known identifier components → compact n-gram → known context
centroid → seeded random. Return `InitializedTerm(term, vector, reason, confidence)` and include
which components/canonical term were used. Normalize every non-zero output vector.

- [ ] **Step 5: Verify tests**

Run: `conda run -n codesearch pytest tests/unit/indexing/finetune/test_initializers.py tests/unit/indexing/finetune/test_providers.py -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add codesense/indexing/finetune/providers.py codesense/indexing/finetune/initializers.py tests/unit/indexing/finetune/test_providers.py tests/unit/indexing/finetune/test_initializers.py
git commit -m "feat: initialize project terms from compact vectors"
```

---

### Task 6: Word2Vec 训练、精确冻结和恢复

**Files:**
- Create: `codesense/indexing/finetune/trainer.py`
- Test: `tests/unit/indexing/finetune/test_trainer.py`

**Interfaces:**
- Consumes: `VocabularyPlan`, `SeedSpace`, initialized project vectors, `CorpusStore`, `FinetuneConfig`。
- Produces: `ProjectEmbedding`, `compute_alignment`, `compute_trainability`, `Word2VecTrainer`。
  `Word2VecTrainer.train(sentences, frequencies, seeds, trainability)` returns a
  `ProjectEmbedding` exposing `vector(term)`, `save(path)`, `epochs_completed` and
  `trained_sentences`。

- [ ] **Step 1: Write failing pure trainability tests**

```python
import pytest

from codesense.indexing.finetune.trainer import compute_trainability


@pytest.mark.parametrize(
    ("alignment", "expected"),
    [(0.0, 1.0), (0.075, 0.5), (0.15, 0.0), (0.8, 0.0)],
)
def test_trainability_is_inverse_alignment(alignment: float, expected: float) -> None:
    assert compute_trainability(alignment, threshold=0.15) == pytest.approx(expected)
```

- [ ] **Step 2: Write a tiny-model freeze regression test**

```python
@pytest.mark.slow
def test_frozen_word_vector_does_not_move(tmp_path: Path) -> None:
    from collections import Counter

    corpus = [["permission", "role"], ["perms", "role"]] * 20
    seeds = {
        "permission": np.array([1.0, 0.0], dtype=np.float32),
        "perms": np.array([0.8, 0.2], dtype=np.float32),
        "role": np.array([0.0, 1.0], dtype=np.float32),
    }
    embedding = Word2VecTrainer(
        vector_size=2,
        window=2,
        negative=2,
        epochs=2,
        workers=1,
        random_seed=7,
    ).train(
        sentences=corpus,
        frequencies=Counter(word for sentence in corpus for word in sentence),
        seeds=seeds,
        trainability={"permission": 0.0, "perms": 1.0, "role": 0.0},
    )
    assert np.array_equal(embedding.vector("permission"), seeds["permission"])
```

- [ ] **Step 3: Run both tests and verify failure**

Run: `conda run -n codesearch pytest tests/unit/indexing/finetune/test_trainer.py -v -m 'not slow'`

Expected: FAIL importing trainer functions.

- [ ] **Step 4: Implement alignment and Word2Vec construction**

Build vocabulary with `Word2Vec.build_vocab_from_freq` so frozen general anchors that do not occur
in the project still exist. Copy seed vectors into `model.wv.vectors`, set
`model.wv.vectors_lockf` to one float per word, and train with:

```text
sg=1, vector_size=300, window=5, negative=10,
epochs=2, start_alpha=0.005, end_alpha=0.0005, min_count=2
```

Compute context alignment only when at least three known high-ICF context words exist; otherwise
freeze existing base words. New project-only words use trainability 1.0.

- [ ] **Step 5: Add resume behavior**

If manifest/base/profile are compatible and corpus delta does not request rebuild, load
`project.model`, call `build_vocab(update=True)` for new words, initialize only those rows, and
train only changed-file sentences. A zero-file delta must not call `model.train`.

- [ ] **Step 6: Run fast and slow focused tests**

Run: `conda run -n codesearch pytest tests/unit/indexing/finetune/test_trainer.py -v -m 'not slow'`

Run: `conda run -n codesearch pytest tests/unit/indexing/finetune/test_trainer.py -v -m slow`

Expected: PASS, including exact frozen-vector equality.

- [ ] **Step 7: Commit**

```bash
git add codesense/indexing/finetune/trainer.py tests/unit/indexing/finetune/test_trainer.py
git commit -m "feat: train compact project word2vec"
```

---

### Task 7: Baseline 与 adapted 双空间 expansion

**Files:**
- Create: `codesense/indexing/finetune/expansion.py`
- Test: `tests/unit/indexing/finetune/test_expansion.py`

**Interfaces:**
- Consumes: baseline vectors, adapted project vectors, planned general keys, discriminating project targets。
- Produces: `build_vector_expansion(...) -> dict[str, list[tuple[str, float, str]]]`。

- [ ] **Step 1: Write failing score-source tests**

```python
import numpy as np

from codesense.indexing.finetune.expansion import build_vector_expansion


def test_adapted_mapping_wins_when_it_improves_the_pair() -> None:
    table = build_vector_expansion(
        keys=("permission",),
        targets=("perms",),
        baseline={
            "permission": np.array([1.0, 0.0], dtype=np.float32),
            "perms": np.array([0.6, 0.8], dtype=np.float32),
        },
        adapted={
            "permission": np.array([1.0, 0.0], dtype=np.float32),
            "perms": np.array([0.9, 0.1], dtype=np.float32),
        },
        min_cosine=0.55,
        max_targets=4,
    )
    assert table["permission"][0][2] == "vector-adapted"


def test_baseline_mapping_survives_adapted_regression() -> None:
    table = build_vector_expansion(
        keys=("department",),
        targets=("dept",),
        baseline={
            "department": np.array([1.0, 0.0], dtype=np.float32),
            "dept": np.array([0.9, 0.1], dtype=np.float32),
        },
        adapted={
            "department": np.array([1.0, 0.0], dtype=np.float32),
            "dept": np.array([0.0, 1.0], dtype=np.float32),
        },
        min_cosine=0.55,
        max_targets=4,
    )
    assert table["department"][0][2] == "vector-base"
```

- [ ] **Step 2: Run and confirm failure**

Run: `conda run -n codesearch pytest tests/unit/indexing/finetune/test_expansion.py -v`

Expected: FAIL importing `build_vector_expansion`.

- [ ] **Step 3: Implement chunked cosine scoring**

Normalize matrices once. Score general keys in chunks of 2048 against project targets. Rescale
baseline cosine to ceiling 0.60 and adapted cosine to ceiling 0.65. Apply the same `min_cosine`,
`MIN_ENTRY_SCORE`, `min_df`, ICF and `max_targets` gates as `grounding.py`.

- [ ] **Step 4: Merge by target without summing evidence**

For each `(key, target)`, retain the higher of baseline/adapted scores and its reason. Sort by
`(-score, target)` for deterministic output. Do not merge lexical here; `grounding.py` remains the
single place that combines lexical and vector tables.

- [ ] **Step 5: Run tests**

Run: `conda run -n codesearch pytest tests/unit/indexing/finetune/test_expansion.py -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add codesense/indexing/finetune/expansion.py tests/unit/indexing/finetune/test_expansion.py
git commit -m "feat: preserve baseline grounding after finetune"
```

---

### Task 8: 统一 finetune 编排并接入 `grounding.py`/`Project.build`

**Files:**
- Modify: `codesense/indexing/finetune/__init__.py`
- Modify: `codesense/indexing/grounding.py:138-174`
- Modify: `codesense/project.py:77-167,224-228`
- Modify: `codesense/index.py:49-73`
- Test: `tests/unit/indexing/finetune/test_pipeline.py`
- Test: `tests/unit/indexing/test_grounding.py`
- Test: `tests/unit/test_debug_search_script.py`

**Interfaces:**
- Consumes: all runtime components from Tasks 1–7。
- Produces: `FinetuneRequest`, `FinetuneResult`, `run_finetune(request)`。
- Produces: actual profile/status/reason returned to `Project.build` and persisted in meta。

- [ ] **Step 1: Write a failing fake-provider orchestration test**

```python
from pathlib import Path
from unittest.mock import Mock

from codesense.indexing.finetune import FinetuneRequest, run_finetune
from codesense.indexing.finetune.artifacts import ArtifactPaths
from codesense.indexing.finetune.config import FinetuneConfig
from codesense.indexing.finetune.corpus import CorpusStore


def request_for_test(tmp_path: Path) -> FinetuneRequest:
    model_path = tmp_path / "compact-base"
    model_path.mkdir()
    corpus = CorpusStore.from_records(
        tmp_path / "corpus.jsonl",
        [("service.py", "hash", [["permission", "perms"]])],
    )
    return FinetuneRequest(
        project_terms={"permission": 4, "perms": 4},
        total_symbols=100,
        targets=("permission", "perms"),
        lexical={"permission": [("perms", 0.75, "abbrev")]},
        corpus=corpus,
        config=FinetuneConfig(
            profile="lightweight",
            model_path=model_path,
            artifact_dir=tmp_path,
        ),
        artifacts=ArtifactPaths(tmp_path),
    )


def test_lightweight_pipeline_persists_model_and_returns_only_vector_entries(tmp_path: Path) -> None:
    request = request_for_test(tmp_path)
    provider = Mock()
    provider.prepare.return_value = compact_seed_fixture(tmp_path)
    trainer = Mock()
    trainer.train.return_value = project_embedding_fixture(tmp_path)
    result = run_finetune(
        request,
        provider=provider,
        trainer=trainer,
    )
    assert result.effective_profile == "lightweight"
    assert result.status == "ready"
    assert result.expansion["permission"][0][2] in {"vector-base", "vector-adapted"}
    assert (tmp_path / "embedding.next" / "project.model").is_file()
```

- [ ] **Step 2: Run and confirm failure**

Run: `conda run -n codesearch pytest tests/unit/indexing/finetune/test_pipeline.py -v`

Expected: FAIL because `run_finetune` is not defined.

- [ ] **Step 3: Implement request/result and orchestration**

```python
@dataclass(frozen=True, slots=True)
class FinetuneRequest:
    project_terms: Mapping[str, int]
    total_symbols: int
    targets: tuple[str, ...]
    lexical: Mapping[str, Sequence[tuple[str, float, str]]]
    corpus: CorpusStore
    config: FinetuneConfig
    artifacts: ArtifactPaths
```

The test module defines `compact_seed_fixture(tmp_path) -> SeedSpace` and
`project_embedding_fixture(tmp_path) -> ProjectEmbedding` by writing two-row NumPy fixtures with
the terms `permission` and `perms`; they are concrete test factories, not production defaults.

```python
@dataclass(frozen=True, slots=True)
class FinetuneResult:
    expansion: dict[str, list[tuple[str, float, str]]]
    requested_profile: str
    effective_profile: str
    status: str
    reason: str = ""
```

The orchestrator loads old artifacts, computes corpus delta, plans vocabulary, obtains seeds,
initializes terms, trains/resumes, builds vector expansion, writes staged artifacts, and returns
the table without merging lexical entries.

- [ ] **Step 4: Dispatch only the finetune branch from `grounding.py`**

Keep lexical and vectors branches unchanged. For finetune:

1. build lexical table with existing code;
2. call `run_finetune` with project terms, filtered targets, lexical table and corpus store;
3. merge `FinetuneResult.expansion` through existing `_merge`;
4. on a non-strict failure retain lexical output and set status `degraded`.

Do not move `VectorSpace`, lexical helpers or scoring helpers out of `grounding.py` in this change.

- [ ] **Step 5: Persist effective status and keep search model-free**

Extend `_with_grounding` to set `grounding_profile`, `grounding_status`, and `grounding_reason`.
Add a regression test that monkeypatches Gensim loaders to raise if `Project.open().search()` tries
to import or load them.

- [ ] **Step 6: Update the debug script profile constants**

Add:

```python
FINETUNE_PROFILE = "lightweight"
MEMORY_BUDGET_MB = 2048
ALLOW_UNSAFE_FULL = False
PRESERVE_FULL_MODEL = False
```

Pass them to `Project.build`; update its unit test fake signature and expected call.

- [ ] **Step 7: Run focused integration tests**

Run: `conda run -n codesearch pytest tests/unit/indexing/finetune/test_pipeline.py tests/unit/indexing/test_grounding.py tests/unit/test_debug_search_script.py -v`

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add codesense/indexing/finetune/__init__.py codesense/indexing/grounding.py codesense/project.py codesense/index.py scripts/debug_search.py tests/unit/indexing/finetune/test_pipeline.py tests/unit/indexing/test_grounding.py tests/unit/test_debug_search_script.py
git commit -m "feat: integrate compact finetune grounding"
```

---

### Task 9: `full_force` 提取子进程

**Files:**
- Modify: `codesense/indexing/finetune/providers.py`
- Test: `tests/unit/indexing/finetune/test_full_force_provider.py`

**Interfaces:**
- Consumes: full FastText `.bin`, `VocabularyPlan`, compact staging directory。
- Produces: `ProviderFailure`, `FullForceProvider.prepare(...) -> SeedSpace` with the same output
  as `CompactProvider`。

- [ ] **Step 1: Write a failing child-process lifecycle test with an injected worker**

```python
def test_parent_loads_compact_output_only_after_worker_exits(tmp_path: Path) -> None:
    events: list[str] = []
    provider = FullForceProvider(worker=FakeExtractionWorker(events))
    sample_plan = VocabularyPlan(
        general_terms=("permission",),
        project_terms=("perms",),
        context_terms=(),
        extended_candidates=(),
        dropped_terms=(),
        estimated_bytes=4096,
    )
    seed = provider.prepare(sample_plan, tmp_path)
    assert events == ["worker-start", "worker-write", "worker-exit", "parent-load"]
    assert seed.vectors_path.name == "baseline-vectors.npy"
```

- [ ] **Step 2: Run and confirm failure**

Run: `conda run -n codesearch pytest tests/unit/indexing/finetune/test_full_force_provider.py -v`

Expected: FAIL because `FullForceProvider` is absent.

- [ ] **Step 3: Implement a top-level spawn-safe extraction worker**

Use `multiprocessing.get_context("spawn")`. The child loads `load_facebook_vectors`, resolves only
compact core + extended candidates selected by `VocabularyPlan` + project OOV, writes `.npy` and
JSON vocabulary files, closes file handles and exits. It must not return a Gensim model through a
pipe or queue.

- [ ] **Step 4: Validate child output before loading**

The parent checks exit code, expected shapes, finite float values, vocabulary/vector row equality,
and base-model SHA. On failure, raise `ProviderFailure`; orchestration downgrades to lightweight
unless `strict_profile=True`.

- [ ] **Step 5: Run tests**

Run: `conda run -n codesearch pytest tests/unit/indexing/finetune/test_full_force_provider.py -v`

Expected: PASS without downloading a real model.

- [ ] **Step 6: Commit**

```bash
git add codesense/indexing/finetune/providers.py tests/unit/indexing/finetune/test_full_force_provider.py
git commit -m "feat: isolate full force vector extraction"
```

---

### Task 10: `warn_full` 兼容路径和完整模型持久化开关

**Files:**
- Modify: `codesense/indexing/finetune/providers.py`
- Modify: `codesense/indexing/grounding.py:311-387`
- Modify: `codesense/indexing/finetune/artifacts.py`
- Modify: `codesense/cli.py`

**Interfaces:**
- Consumes: existing full FastText `VectorSpace` behavior and `FinetuneConfig` unsafe flags。
- Produces: `UnsafeFullProvider`, compact export, optional `full-fasttext.model` persistence。

- [ ] **Step 1: Add the unsafe provider without moving existing grounding code**

`UnsafeFullProvider` receives an injected legacy `VectorSpace` factory from `grounding.py`, invokes
the current full-model fine-tune path, and exports the selected project/general rows into the same
compact baseline/project model shape used by other profiles. This preserves `grounding.py` while
putting new profile orchestration in `finetune/providers.py`.

- [ ] **Step 2: Add preflight warnings and disk-space validation**

Before training print/log the 15–25GB RAM warning. When `preserve_full_model=True`, calculate
required bytes as `source_model_size * 1.25` and compare with `shutil.disk_usage(index_dir).free`.
Fail before training when insufficient.

- [ ] **Step 3: Implement compact-by-default persistence**

After exporting `project.model`, discard the full trained object unless
`preserve_full_model=True`. When preservation is requested, save only to the validated
`embedding.next/full-fasttext.model` path and let `ArtifactTransaction` commit it.

- [ ] **Step 4: Run existing non-warn tests only**

Per the approved scope, do not add a real or fake `warn_full` training correctness test. Run the
existing configuration, grounding and artifact suites to ensure this wiring did not regress shared
paths:

Run: `conda run -n codesearch pytest tests/unit/indexing/finetune/test_config.py tests/unit/indexing/finetune/test_artifacts.py tests/unit/indexing/test_grounding.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add codesense/indexing/finetune/providers.py codesense/indexing/finetune/artifacts.py codesense/indexing/grounding.py codesense/cli.py
git commit -m "feat: retain unsafe full fasttext profile"
```

---

### Task 11: 离线紧凑基础模型制备

**Files:**
- Modify: `codesense/indexing/finetune/vocabulary.py`
- Modify: `codesense/indexing/finetune/providers.py`
- Create: `scripts/build_compact_grounding_model.py`
- Create: `resources/grounding/projects.json`
- Create: `resources/grounding/queries.jsonl`
- Test: `tests/unit/indexing/finetune/test_base_builder.py`

**Interfaces:**
- Consumes: fixed query JSONL, fixed local repository checkouts described by project manifest, full `cc.en.300.bin`。
- Produces: the exact `compact-base/` artifact contract consumed by `CompactProvider`。

- [ ] **Step 1: Add fixed corpus manifests**

`projects.json` records repository URL, immutable commit and language. Start with two repositories
per family so one project cannot dominate: Netty/Spring Framework, Django/Flask,
Kubernetes/Prometheus, VS Code/Nest, Tokio/Serde, Redis/gRPC. The builder consumes local checkout
paths supplied on the CLI; it does not clone during a normal CodeSense project build.

`queries.jsonl` records `id`, `intent`, `query`, and `concepts`. Include the confirmed intent groups:
feature location, calls, data flow, errors, performance, concurrency, cache, security,
persistence, configuration, networking, observability, tests, lifecycle and resources.

- [ ] **Step 2: Write failing deterministic-selection tests**

```python
def test_query_scaffolding_is_removed_but_concepts_survive() -> None:
    selected = select_general_query_terms(
        [{"query": "find code that applies backpressure", "concepts": ["backpressure"]}]
    )
    assert "find" not in selected
    assert "code" not in selected
    assert "backpressure" in selected


def test_large_repository_cannot_dominate_repository_df() -> None:
    scores = score_code_terms(
        {"large": {"cache": 10000}, "small": {"cache": 1, "retry": 1}}
    )
    assert scores["cache"].repository_df == 2
    assert scores["retry"].repository_df == 1
```

- [ ] **Step 3: Implement selection and manifest generation**

Select 25k–30k query concepts, 15k–20k code-domain terms and 1k–3k forced anchors, then deduplicate
to a 40k–50k compact vocabulary. Store per-word groups, query df, repository df, language count,
forced flag and selection score in `manifest.json`.

- [ ] **Step 4: Implement the thin CLI script**

The script only parses:

```text
--queries
--projects
--checkout-root
--fasttext-model
--output
--vector-size 300
--max-vocabulary 50000
```

Business logic remains in `finetune/vocabulary.py` and provider helpers. The output contains
`model.model`, `baseline-vectors.npy`, `compact-vocabulary.txt`,
`extended-candidates.txt`, `subword-initializer.npz`, and `manifest.json`.

- [ ] **Step 5: Test with tiny local fixtures**

Run: `conda run -n codesearch pytest tests/unit/indexing/finetune/test_base_builder.py -v`

Expected: PASS and byte-identical manifests across two runs with the same inputs.

- [ ] **Step 6: Commit source and small manifests, not generated weights**

```bash
git add codesense/indexing/finetune/vocabulary.py codesense/indexing/finetune/providers.py scripts/build_compact_grounding_model.py resources/grounding/projects.json resources/grounding/queries.jsonl tests/unit/indexing/finetune/test_base_builder.py
git commit -m "feat: build compact grounding base model"
```

---

### Task 12: 端到端资源、质量和兼容验收

**Files:**
- Create: `tests/integration/indexing/test_finetune_pipeline.py`
- Modify: `tests/unit/test_index.py`
- Modify: `docs/superpowers/specs/2026-08-06-finetune-strategy-optimization-design.md`
- Modify: `docs/TODO.md`

**Interfaces:**
- Consumes: completed runtime and compact-base fixture。
- Produces: verified end-to-end profile behavior and measured resource report。

- [ ] **Step 1: Write an end-to-end lightweight test**

Build a tiny toy project with a compact fixture containing `permission`, train `perms` beside
`role/scopes`, save the index, reopen it, and assert:

```python
assert project.index.meta.grounding_profile == "lightweight"
assert "perms" in [target for target, _, _ in project.index.expansion["permission"]]
assert (index_dir / "embedding" / "project.model").is_file()
```

Monkeypatch all Facebook FastText loaders to raise so this test proves lightweight never touches
the full model.

- [ ] **Step 2: Test no-op and incremental rebuilds**

Run the same build twice and assert the second training report says `trained_sentences == 0`.
Modify one file and assert only that file appears in `changed_files`. Remove at least 20% of tokens
and assert `reinitialized is True`.

- [ ] **Step 3: Add a subprocess RSS probe for the benchmark fixture**

Use `resource.getrusage(resource.RUSAGE_SELF).ru_maxrss` inside a subprocess and normalize macOS
bytes versus Linux KiB. The CI fixture must stay under 512MB; the manual representative-project
gate is 2048MB. Record both model bytes and peak RSS in `training-report.json`.

- [ ] **Step 4: Run all required gates**

Run:

```bash
conda run -n codesearch ruff check .
conda run -n codesearch ruff format --check .
conda run -n codesearch pytest
conda run -n codesearch pytest -m slow -rs
```

Expected: all commands exit 0. Inspect `-rs` so missing heavy fixtures are reported rather than
mistaken for executed tests.

- [ ] **Step 5: Update design status and TODO with measured facts**

Change the design status from “尚未实现” to “已实现” only after the gates pass. Record compact
model size, representative peak RSS, effective vocabulary sizes, query concept coverage and any
slow-test skips. Do not claim `warn_full` quality was tested.

- [ ] **Step 6: Commit**

```bash
git add tests/integration/indexing/test_finetune_pipeline.py tests/unit/test_index.py docs/superpowers/specs/2026-08-06-finetune-strategy-optimization-design.md docs/TODO.md
git commit -m "test: verify compact finetune pipeline"
```

## Final Verification Checklist

- [ ] `grounding.py` still owns existing lexical/vectors logic and only dispatches new finetune work.
- [ ] All new runtime modules live under `codesense/indexing/finetune/`.
- [ ] `lightweight` never imports a Facebook FastText loader.
- [ ] `lightweight` rejects or degrades before estimated memory exceeds 2048MB.
- [ ] `full_force` child exits before Word2Vec training begins.
- [ ] `warn_full` cannot run without `allow_unsafe_full=True`.
- [ ] `warn_full` does not preserve the full model by default.
- [ ] Project model, baseline, corpus, manifest and report are present in `.codesense/embedding/`.
- [ ] Unchanged projects do not retrain; small changes train only delta sentences.
- [ ] Search opens and executes without loading any embedding model.
- [ ] Old indexes and existing lexical/vectors strategies still work.
- [ ] Generated model weights and local corpora are ignored by Git.
- [ ] `ruff check .`, `ruff format --check .`, `pytest`, and the applicable slow suite pass.
