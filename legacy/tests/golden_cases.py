"""Golden 测试的用例构造，测试与录制脚本共用。

Golden 测试回答的是一个别的测试回答不了的问题：**这次改动有没有悄悄改变
数值结果？** 单元测试只能验证你想到的情况，而重构真正会毁掉的往往是你
没想到的那些。

用例的输入尽量取真实数据（真实符号名、真实检索候选），因为分词、缩写、
过滤这些逻辑对输入形态极其敏感，编出来的玩具输入测不出问题。

期望值存在 ``tests/fixtures/golden/`` 下，用 ``python -m scripts.record_golden``
重录。**重录前务必确认当前行为是对的**——golden 只保证「和上次一样」，
不保证「对」。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
GOLDEN_DIR = Path(__file__).resolve().parent / "fixtures" / "golden"

#: 分词与缩写的输入。取自真实项目符号表，但已固化在这里，
#: 所以这组用例**不依赖 output/**，任何人 clone 下来装齐依赖就能跑。
TOKENIZER_INPUT = GOLDEN_DIR / "tokenizer_input.json"

#: 下面两组需要 output/ 下的离线产物，那些不进版本库。
#: 缺了就跳过，不是失败——见各测试的 skip 条件。
PROJECT_OUTPUT = REPO_ROOT / "output" / "youlai-boot-master"
QUERY_OUTPUT = PROJECT_OUTPUT / "query_1"


def _ids(rows: Any) -> list:
    return [r.get("symbol_id") if isinstance(r, dict) else r for r in rows]


# ---------------------------------------------------------------- 分词与缩写


def tokenizer_cases() -> dict:
    """CodeTokenizer 与 AbbreviationGenerator 在固定输入上的输出。"""
    from codesense.expansion.abbreviate import abbreviate, normalize_entity
    from codesense.tokenizer.tokenizer_core import tokenizer

    names = json.loads(TOKENIZER_INPUT.read_text(encoding="utf-8"))
    cases: dict[str, Any] = {}
    for split_type in ("bpe", "unigram"):
        cases[f"tokenizer/{split_type}"] = {n: tokenizer(n, split_type) for n in names}
    # abbreviate 的输出是集合，排序后才稳定；只取前若干个，否则期望值会很大
    cases["abbreviate"] = {n: sorted(abbreviate(n)) for n in names[:12]}
    cases["normalize_entity"] = {n: sorted(normalize_entity(n)) for n in names[:40]}
    return cases


# ---------------------------------------------------------------- 关系过滤器


def relation_filter_cases(graph_store: Any) -> dict:
    """三个 RelationFilter 在真实候选集上的输出。

    只记 symbol_id 序列——那就是过滤器全部的可观察行为。
    """
    from codesense.filters.relation_filters import (
        CalleeFilter,
        CallerFilter,
        GraphRoleFilter,
    )

    candidates = json.loads((QUERY_OUTPUT / "filtered_by_type.json").read_text(encoding="utf-8"))
    cases: dict[str, Any] = {}

    for role in ("entry_point", "leaf", "isolate", "not_a_role"):
        for preserve in (True, False):
            cases[f"roles/{role}/preserve={preserve}"] = _ids(
                GraphRoleFilter(
                    [role], graph_store=graph_store, preserve_non_applicable=preserve
                ).apply(candidates)
            )
    cases["roles/multi"] = _ids(
        GraphRoleFilter(["entry_point", "leaf"], graph_store=graph_store).apply(candidates)
    )
    cases["roles/empty_candidates"] = _ids(
        GraphRoleFilter(["entry_point"], graph_store=graph_store).apply([])
    )

    names = [c["name"] for c in candidates[:6]]
    for cls, label in ((CallerFilter, "caller"), (CalleeFilter, "callee")):
        for name in names:
            for layer in (1, 2):
                cases[f"{label}/{name}/layer={layer}"] = _ids(
                    cls(
                        [(None, name)],
                        graph_store=graph_store,
                        layer=layer,
                        worker_count=1,
                    ).apply(candidates)
                )
    return cases


# ---------------------------------------------------------------- intention 阶段


def intention_cases() -> dict:
    """cluster 与 embedding 两个阶段在真实候选集上的输出。

    只记各桶的 symbol_id 序列和整数型 stats。``tiers`` 那类中间数据体积
    高达几百 KB，且随实现细节变化，不适合当回归基准。
    """
    from codesense.filters.cluster_pipeline import (
        CodeEmbedder,
        FiltrationDispatcher,
        SymbolClusterer,
    )
    from codesense.filters.embedding_filter import run_embedding_filter

    plan = json.loads((QUERY_OUTPUT / "intention_semql.json").read_text(encoding="utf-8"))
    profile = plan.get("query_profile", {})
    exec_plan = plan.get("execution_plan", {})

    def shape(result: dict) -> dict:
        out = {}
        for key, value in sorted(result.items()):
            if key == "tiers":
                continue
            if isinstance(value, list):
                out[key] = _ids(value)
            elif isinstance(value, dict):
                out[key] = {
                    k: v for k, v in sorted(value.items()) if isinstance(v, (int, str, bool))
                }
        return out

    type_cands = json.loads((QUERY_OUTPUT / "filtered_by_type.json").read_text(encoding="utf-8"))
    cluster_policy = exec_plan.get("cluster", {})
    dispatcher = FiltrationDispatcher(
        CodeEmbedder(),
        SymbolClusterer(distance_threshold=float(cluster_policy.get("distance_threshold", 0.3))),
    )
    query_text = str(profile.get("semantic_text") or "").strip()

    cluster_cands = json.loads(
        (QUERY_OUTPUT / "filtered_by_cluster.json").read_text(encoding="utf-8")
    )
    emb_policy = exec_plan.get("embedding", {})
    return {
        "cluster/real_policy": shape(
            dispatcher.run_pipeline(type_cands, query_text, cluster_policy)
        ),
        "cluster/empty": shape(dispatcher.run_pipeline([], query_text, cluster_policy)),
        "embedding/real_policy": shape(run_embedding_filter(cluster_cands, profile, emb_policy)),
        "embedding/empty": shape(run_embedding_filter([], profile, emb_policy)),
        "embedding/on_type_candidates": shape(
            run_embedding_filter(type_cands, profile, emb_policy)
        ),
    }
