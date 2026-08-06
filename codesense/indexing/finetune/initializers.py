"""Initialize project words from the strongest available compact evidence."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping, Sequence

import numpy as np

Subword = Callable[[str], np.ndarray | None]


def initialize_terms(
    *,
    terms: Sequence[str],
    base: Mapping[str, np.ndarray],
    canonical: Mapping[str, str],
    components: Mapping[str, Sequence[str]],
    contexts: Mapping[str, Sequence[str]],
    subword: Subword | None,
    vector_size: int,
    random_seed: int = 0,
) -> dict[str, np.ndarray]:
    """Apply exact, canonical, components, subword, context, random order."""
    found: dict[str, np.ndarray] = {}
    for term in terms:
        vector = base.get(term)
        if vector is None and canonical.get(term) in base:
            vector = base[canonical[term]]
        if vector is None:
            vector = _centroid(components.get(term, ()), base)
        if vector is None and subword is not None:
            vector = subword(term)
        if vector is None:
            vector = _centroid(contexts.get(term, ()), base)
        if vector is None:
            digest = hashlib.sha256(f"{random_seed}:{term}".encode()).digest()
            seed = int.from_bytes(digest[:8], "big")
            vector = np.random.default_rng(seed).standard_normal(vector_size)
        found[term] = _unit(np.asarray(vector, dtype=np.float32))
    return found


def _centroid(terms: Sequence[str], base: Mapping[str, np.ndarray]) -> np.ndarray | None:
    vectors = [base[term] for term in terms if term in base]
    return np.mean(vectors, axis=0) if vectors else None


def _unit(vector: np.ndarray) -> np.ndarray:
    return np.asarray(vector / max(float(np.linalg.norm(vector)), 1e-9), dtype=np.float32)
