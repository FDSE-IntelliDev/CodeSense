# `_cohere` 结构邻近分数加权 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `_cohere()` 为满足结构邻近条件的已有候选追加 `原分数 × boost` 的可解释证据，使默认最终分数真实变为原分数的 `1.6` 倍。

**Architecture:** 保留现有 `top → reach → 与原候选取交集` 的邻域判断，只把当前会丢失的临时排序改为 Evidence 增量。最终 `_rank()`、`Hit.score` 和 `result.explain()` 继续只消费 `Frag`，不改变公共接口。

**Tech Stack:** Python 3.12、现有 QL `Frag`/`Evidence`/`UnitHit` 数据模型、pytest、ruff。

## Global Constraints

- 运行命令前激活 conda 环境 `codesearch`。
- 不改变候选节点、边或 witness 集合。
- 不改变 `reach()`、planned/codegen 的状态模型或默认 `seeds=20`、`boost=0.6`。
- 先观察回归测试在旧实现上因分数仍为 `0.7` 而失败，再修改生产代码。
- 只修改当前缺陷直接涉及的 `tests/unit/test_search.py` 和 `codesense/search.py`。
- 不覆盖或提交工作区中已有的其它未提交文件；除非用户另行要求，不创建 Git commit。

---

### Task 1：用搜索结果行为锁定结构加权

**Files:**
- Modify: `tests/unit/test_search.py`
- Modify: `codesense/search.py:382-398`
- Reference: `docs/superpowers/specs/2026-08-05-cohere-score-boost-design.md`

**Interfaces:**
- Consumes: `_cohere(frag: Frag, ctx: EvalContext, seeds: int = 20, boost: float = 0.6) -> Frag`、`score_of(frag, symbol_id) -> float`、`Evidence.merge(other) -> Evidence`。
- Produces: 对 boosted 节点追加 `UnitHit(unit="coherence", signal="structural", field="graph", score=base_score * boost)` 的 `_cohere()`；其它节点和 fragment 结构保持不变。

- [ ] **Step 1：添加失败测试**

在 `tests/unit/test_search.py` 中导入 `_cohere`、`_rank`、`Evidence`、`Frag`、`UnitHit` 和 `score_of`，并添加：

```python
class TestStructuralCoherence:
    def test_boosts_existing_candidate_near_the_strongest_hit(self, ctx) -> None:
        def scored(value: float) -> Evidence:
            return Evidence(
                unit_hits=(
                    UnitHit(
                        unit="query",
                        signal=Evidence.COMBINED,
                        detail="query score",
                        score=value,
                    ),
                )
            )

        frag = Frag(
            nodes=ctx.symbols.get_many((1, 2, 3)),
            evidence={1: scored(0.9), 2: scored(0.7), 3: scored(0.8)},
        )

        result = _cohere(frag, ctx, seeds=1, boost=0.6)

        assert set(result.nodes) == {1, 2, 3}
        assert score_of(result, 1) == pytest.approx(0.9)
        assert score_of(result, 2) == pytest.approx(1.12)
        assert score_of(result, 3) == pytest.approx(0.8)
        assert [hit.symbol_id for hit in _rank(result, 3)] == [2, 1, 3]
        structural = [
            hit for hit in result.evidence_for(2).unit_hits if hit.signal == "structural"
        ]
        assert len(structural) == 1
        assert structural[0].field == "graph"
```

- [ ] **Step 2：运行测试并确认 RED**

Run:

```bash
conda run -n codesearch pytest tests/unit/test_search.py::TestStructuralCoherence::test_boosts_existing_candidate_near_the_strongest_hit -v
```

Expected: FAIL，`score_of(result, 2)` 仍为 `0.7`，证明测试捕获的是结构 boost 未进入 Evidence 的既有缺陷。

- [ ] **Step 3：实现最小 Evidence 加权**

在 `codesense/search.py` 中给现有 frag import 增加 `UnitHit`。将 `_cohere()` 末尾的临时排序替换为：

```python
    evidence = dict(frag.evidence)
    for symbol_id in boosted:
        structural = UnitHit(
            unit="coherence",
            signal="structural",
            detail=f"within 1-2 calls/contains hops of a top-{seeds} hit",
            field="graph",
            score=score_of(frag, symbol_id) * boost,
        )
        evidence[symbol_id] = frag.evidence_for(symbol_id).merge(
            Evidence(unit_hits=(structural,))
        )
    return Frag(
        nodes=frag.nodes,
        edges=frag.edges,
        evidence=evidence,
        witnesses=frag.witnesses,
    )
```

加权始终从原始 `frag` 读取 base score，避免同一次循环中先处理的节点影响后处理节点。

- [ ] **Step 4：运行目标测试并确认 GREEN**

Run:

```bash
conda run -n codesearch pytest tests/unit/test_search.py::TestStructuralCoherence::test_boosts_existing_candidate_near_the_strongest_hit -v
```

Expected: PASS，邻近节点为 `1.12`，最终顺序为 `[2, 1, 3]`。

- [ ] **Step 5：运行搜索单元测试**

Run:

```bash
conda run -n codesearch pytest tests/unit/test_search.py -v
```

Expected: 全部 PASS，既有 lexical route、fallback、Hit 和 SearchResult 行为不回归。

- [ ] **Step 6：运行完整质量门禁**

Run:

```bash
conda run -n codesearch ruff check .
conda run -n codesearch ruff format --check .
conda run -n codesearch pytest
```

Expected: Ruff、格式检查和完整 pytest 全部通过。
