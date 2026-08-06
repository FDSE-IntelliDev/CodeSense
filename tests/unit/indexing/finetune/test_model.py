"""Vocabulary planning, OOV initialization and compact Word2Vec training."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from codesense.indexing.finetune.initializers import initialize_terms
from codesense.indexing.finetune.trainer import train_word2vec
from codesense.indexing.finetune.vocabulary import plan_vocabulary


def test_targets_survive_budget_pruning() -> None:
    project = {"perms": 3, **{f"context{i}": 2 for i in range(1000)}}
    plan = plan_vocabulary(
        general=("permission",),
        project_df=project,
        targets={"perms"},
        vector_size=300,
        memory_budget_mb=1,
    )

    assert "perms" in plan.project
    assert len(plan.project) < len(project)


def test_protected_vocabulary_over_budget_fails() -> None:
    with pytest.raises(MemoryError, match="protected vocabulary"):
        plan_vocabulary(
            general=tuple(f"query{i}" for i in range(1000)),
            project_df={"perms": 3},
            targets={"perms"},
            vector_size=300,
            memory_budget_mb=1,
        )


def test_canonical_precedes_random() -> None:
    permission = np.array([1.0, 0.0], dtype=np.float32)
    seeds = initialize_terms(
        terms=("perms",),
        base={"permission": permission},
        canonical={"perms": "permission"},
        components={},
        contexts={},
        subword=None,
        vector_size=2,
    )

    assert np.array_equal(seeds["perms"], permission)


def test_random_fallback_is_stable_and_normalized() -> None:
    arguments = {
        "terms": ("redisson",),
        "base": {},
        "canonical": {},
        "components": {},
        "contexts": {},
        "subword": None,
        "vector_size": 3,
    }

    first = initialize_terms(**arguments)["redisson"]
    second = initialize_terms(**arguments)["redisson"]

    assert np.array_equal(first, second)
    assert np.linalg.norm(first) == pytest.approx(1.0)


def test_general_vector_is_frozen_and_model_is_saved(tmp_path: Path) -> None:
    permission = np.array([1.0, 0.0], dtype=np.float32)
    embedding = train_word2vec(
        sentences=[["permission", "perms"]] * 10,
        seeds={
            "permission": permission,
            "perms": np.array([0.8, 0.2], dtype=np.float32),
        },
        frozen={"permission"},
        epochs=1,
        workers=1,
    )

    assert np.array_equal(embedding.vector("permission"), permission)
    embedding.save(tmp_path / "project.model")
    assert (tmp_path / "project.model").is_file()
