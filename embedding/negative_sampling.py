"""Negative sampling utilities for dual-encoder training pair construction.

This module provides easy/medium/hard negative sampling strategies over
symbol documents to build contrastive training data.
"""

import random
from collections import defaultdict
from typing import Dict, List, Set, Tuple

from schema import SymbolDocument


def _name_tokens(name: str) -> Set[str]:
    """Split identifier-like names into lowercase alnum token set."""
    parts: List[str] = []
    token = ""
    for ch in name:
        if ch.isalnum():
            token += ch.lower()
        else:
            if token:
                parts.append(token)
                token = ""
    if token:
        parts.append(token)
    return set(parts)


class NegativeSampler:
    """Sample easy/medium/hard negatives for a positive symbol document."""

    def __init__(self, docs: List[SymbolDocument], seed: int = 42):
        self.docs = docs
        self.rng = random.Random(seed)
        self.by_file: Dict[str, List[int]] = defaultdict(list)
        self.by_container: Dict[str, List[int]] = defaultdict(list)
        self.name_token_sets: List[Set[str]] = []

        for i, d in enumerate(docs):
            self.by_file[d.file].append(i)
            self.by_container[d.container or "__none__"].append(i)
            self.name_token_sets.append(_name_tokens(d.name))

    def sample(
        self,
        pos_idx: int,
        n_easy: int = 1,
        n_medium: int = 1,
        n_hard: int = 2,
    ) -> List[int]:
        """Return mixed negatives with fallback random fill if sparse."""
        selected: Set[int] = set()

        self._sample_easy(pos_idx, n_easy, selected)
        self._sample_medium(pos_idx, n_medium, selected)
        self._sample_hard(pos_idx, n_hard, selected)

        needed = n_easy + n_medium + n_hard
        if len(selected) < needed:
            pool = [i for i in range(len(self.docs)) if i != pos_idx and i not in selected]
            self.rng.shuffle(pool)
            for i in pool:
                selected.add(i)
                if len(selected) >= needed:
                    break

        return list(selected)

    def _sample_easy(self, pos_idx: int, k: int, selected: Set[int]) -> None:
        """Easy negatives: random symbols from global pool."""
        candidates = [i for i in range(len(self.docs)) if i != pos_idx and i not in selected]
        self.rng.shuffle(candidates)
        for i in candidates[:k]:
            selected.add(i)

    def _sample_medium(self, pos_idx: int, k: int, selected: Set[int]) -> None:
        """Medium negatives: symbols from the same file."""
        pos = self.docs[pos_idx]
        candidates = [i for i in self.by_file.get(pos.file, []) if i != pos_idx and i not in selected]
        self.rng.shuffle(candidates)
        for i in candidates[:k]:
            selected.add(i)

    def _sample_hard(self, pos_idx: int, k: int, selected: Set[int]) -> None:
        """Hard negatives: same container / name-token-overlap symbols."""
        pos = self.docs[pos_idx]
        pos_tokens = self.name_token_sets[pos_idx]
        scored: List[Tuple[float, int]] = []

        container_candidates = [
            i for i in self.by_container.get(pos.container or "__none__", [])
            if i != pos_idx and i not in selected
        ]
        for i in container_candidates:
            overlap = len(pos_tokens & self.name_token_sets[i])
            union = len(pos_tokens | self.name_token_sets[i]) or 1
            scored.append((overlap / union, i))

        if len(scored) < k:
            for i in range(len(self.docs)):
                if i == pos_idx or i in selected:
                    continue
                overlap = len(pos_tokens & self.name_token_sets[i])
                if overlap == 0:
                    continue
                union = len(pos_tokens | self.name_token_sets[i]) or 1
                scored.append((overlap / union, i))

        scored.sort(key=lambda x: x[0], reverse=True)

        hard_added = 0
        for _, idx in scored:
            if idx in selected:
                continue
            selected.add(idx)
            hard_added += 1
            if hard_added >= k:
                break
