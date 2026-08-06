"""Baseline and adapted vector expansion keep the stronger evidence."""

from __future__ import annotations

import numpy as np

from codesense.indexing.finetune.expansion import build_vector_expansion


def test_baseline_survives_adapted_regression() -> None:
    result = build_vector_expansion(
        keys=("department",),
        targets=("dept",),
        baseline={
            "department": np.array([1.0, 0.0]),
            "dept": np.array([0.9, 0.1]),
        },
        adapted={
            "department": np.array([1.0, 0.0]),
            "dept": np.array([0.0, 1.0]),
        },
        min_cosine=0.55,
        max_targets=4,
    )

    assert result["department"][0][2] == "vector-base"


def test_adapted_pair_wins_when_it_improves() -> None:
    result = build_vector_expansion(
        keys=("permission",),
        targets=("perms",),
        baseline={
            "permission": np.array([1.0, 0.0]),
            "perms": np.array([0.6, 0.8]),
        },
        adapted={
            "permission": np.array([1.0, 0.0]),
            "perms": np.array([0.9, 0.1]),
        },
        min_cosine=0.55,
        max_targets=4,
    )

    assert result["permission"][0][2] == "vector-adapted"
