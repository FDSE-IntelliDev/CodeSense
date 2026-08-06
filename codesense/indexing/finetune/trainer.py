"""Seed and train one compact project Word2Vec model."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np


@dataclass(slots=True)
class ProjectEmbedding:
    model: Any
    epochs: int

    def vector(self, term: str) -> np.ndarray:
        return np.asarray(self.model.wv[term], dtype=np.float32)

    def save(self, path: Path | str) -> None:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        self.model.save(str(destination))


def train_word2vec(
    *,
    sentences: Iterable[Sequence[str]],
    seeds: Mapping[str, np.ndarray],
    frozen: set[str],
    epochs: int = 2,
    workers: int = 8,
) -> ProjectEmbedding:
    """Train once; frozen input rows are restored exactly after training."""
    from gensim.models import Word2Vec

    corpus = list(sentences) if isinstance(sentences, Iterator) else sentences
    frequencies = Counter(word for sentence in corpus for word in sentence)
    for term in seeds:
        frequencies[term] = max(2, frequencies[term])
    vector_size = len(next(iter(seeds.values())))
    model = Word2Vec(
        sg=1,
        vector_size=vector_size,
        window=5,
        negative=10,
        min_count=2,
        workers=workers,
        seed=0,
    )
    model.build_vocab_from_freq(dict(frequencies))
    for term, vector in seeds.items():
        model.wv.vectors[model.wv.key_to_index[term]] = vector
    lockf = np.ones(len(model.wv), dtype=np.float32)
    frozen_rows = {}
    for term in frozen & set(model.wv.key_to_index):
        index = model.wv.key_to_index[term]
        lockf[index] = 0.0
        frozen_rows[index] = model.wv.vectors[index].copy()
    model.wv.vectors_lockf = lockf
    if corpus:
        model.train(
            corpus_iterable=corpus,
            total_examples=len(corpus),  # type: ignore[arg-type]
            epochs=epochs,
            start_alpha=0.005,
            end_alpha=0.0005,
        )
    for index, vector in frozen_rows.items():
        model.wv.vectors[index] = vector
    return ProjectEmbedding(model, epochs)
