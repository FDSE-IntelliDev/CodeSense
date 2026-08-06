"""Build expansion entries from base and project-adapted vectors."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import numpy as np


def build_vector_expansion(
    *,
    keys: Sequence[str],
    targets: Sequence[str],
    baseline: Mapping[str, np.ndarray],
    adapted: Mapping[str, np.ndarray],
    min_cosine: float,
    max_targets: int,
) -> dict[str, list[tuple[str, float, str]]]:
    """Keep the stronger baseline/adapted score for every key-target pair."""
    usable_targets = tuple(term for term in targets if term in baseline or term in adapted)
    found: dict[str, list[tuple[str, float, str]]] = {}
    for start in range(0, len(keys), 2048):
        chunk = keys[start : start + 2048]
        candidates = {key: {} for key in chunk}
        _score_space(
            chunk,
            usable_targets,
            baseline,
            min_cosine,
            0.60,
            "vector-base",
            max_targets,
            candidates,
        )
        _score_space(
            chunk,
            usable_targets,
            adapted,
            min_cosine,
            0.65,
            "vector-adapted",
            max_targets,
            candidates,
        )
        for key, entries in candidates.items():
            ranked = sorted(
                ((target, score, reason) for target, (score, reason) in entries.items()),
                key=lambda item: (-item[1], item[0]),
            )
            if ranked:
                found[key] = ranked[:max_targets]
    return found


def _score_space(
    keys: Sequence[str],
    targets: Sequence[str],
    vectors: Mapping[str, np.ndarray],
    minimum: float,
    ceiling: float,
    reason: str,
    limit: int,
    found: dict[str, dict[str, tuple[float, str]]],
) -> None:
    present_keys = [key for key in keys if key in vectors]
    present_targets = [target for target in targets if target in vectors]
    if not present_keys or not present_targets or limit <= 0:
        return
    similarity = (
        _unit_rows(np.asarray([vectors[key] for key in present_keys]))
        @ _unit_rows(np.asarray([vectors[target] for target in present_targets])).T
    )
    count = min(limit, len(present_targets))
    for row, key in enumerate(present_keys):
        scores = similarity[row]
        best = np.argpartition(-scores, count - 1)[:count]
        for index in best:
            target = present_targets[index]
            cosine = float(scores[index])
            if target == key or cosine < minimum:
                continue
            score = round(ceiling * (cosine - minimum) / max(1.0 - minimum, 1e-9), 4)
            if score < 0.05:
                continue
            current = found[key].get(target)
            if current is None or score > current[0]:
                found[key][target] = (score, reason)


def _unit_rows(matrix: np.ndarray) -> np.ndarray:
    values = np.asarray(matrix, dtype=np.float32)
    return values / np.maximum(np.linalg.norm(values, axis=1, keepdims=True), 1e-9)
