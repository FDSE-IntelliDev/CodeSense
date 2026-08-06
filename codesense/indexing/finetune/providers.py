"""Prepare the small seed matrices used by project adaptation."""

from __future__ import annotations

import json
import multiprocessing
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

Runner = Callable[[Path, tuple[str, ...], Path], None]


@dataclass(frozen=True, slots=True)
class SeedVectors:
    vocabulary: tuple[str, ...]
    vectors_path: Path
    vector_size: int
    version: str
    adapted_path: Path | None = None

    def baseline(self) -> dict[str, np.ndarray]:
        vectors = np.load(self.vectors_path)
        return {term: vectors[index] for index, term in enumerate(self.vocabulary)}

    def adapted(self) -> dict[str, np.ndarray]:
        vectors = np.load(self.adapted_path or self.vectors_path)
        return {term: vectors[index] for index, term in enumerate(self.vocabulary)}


def prepare_compact(source: Path, terms: Iterable[str], destination: Path) -> SeedVectors:
    """Copy only requested rows from a compact base model."""
    vocabulary = tuple((source / "compact-vocabulary.txt").read_text(encoding="utf-8").splitlines())
    vectors = np.load(source / "baseline-vectors.npy", mmap_mode="r")
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    if len(vocabulary) != len(vectors):
        raise ValueError("compact vocabulary and vector row count differ")

    wanted = set(terms)
    indices = [index for index, term in enumerate(vocabulary) if term in wanted]
    selected = tuple(vocabulary[index] for index in indices)
    matrix = np.asarray(vectors[indices], dtype=np.float32)
    return _save(
        destination,
        selected,
        matrix,
        str(manifest.get("vocabulary_version", "compact")),
    )


def prepare_full_force(
    source: Path,
    terms: Iterable[str],
    destination: Path,
    *,
    runner: Runner | None = None,
) -> SeedVectors:
    """Export requested FastText rows in a disposable child process."""
    requested = tuple(dict.fromkeys(terms))
    destination.mkdir(parents=True, exist_ok=True)
    if runner is not None:
        runner(source, requested, destination)
    else:
        process = multiprocessing.get_context("spawn").Process(
            target=_export_fasttext,
            args=(source, requested, destination),
        )
        process.start()
        process.join()
        if process.exitcode:
            raise RuntimeError(f"FastText export process exited with {process.exitcode}")
    return _read_export(destination, version="full-force")


def prepare_warn_full(
    space: object,
    sentences: list[list[str]],
    terms: Iterable[str],
    destination: Path,
    *,
    config: object,
    preserve_full_model: bool = False,
) -> SeedVectors:
    """Adapt the legacy full model; this profile is intentionally unsafe."""
    requested = tuple(dict.fromkeys(terms))
    baseline = np.asarray([space.vectors[term] for term in requested], dtype=np.float32)
    seeds = _save(destination, requested, baseline, "warn-full")
    space.finetune(sentences, config=config)
    adapted = np.asarray([space.vectors[term] for term in requested], dtype=np.float32)
    adapted_path = destination / "adapted-vectors.npy"
    np.save(adapted_path, adapted)
    if preserve_full_model:
        space.save_full(destination / "full.model")
    return SeedVectors(
        seeds.vocabulary,
        seeds.vectors_path,
        seeds.vector_size,
        seeds.version,
        adapted_path,
    )


def load_subword_initializer(source: Path):  # type: ignore[no-untyped-def]
    """Load the optional compact character n-gram table."""
    path = source / "subword-initializer.npz"
    if not path.is_file():
        return None
    with np.load(path) as data:
        rows = {str(term): vector for term, vector in zip(data["ngrams"], data["vectors"])}

    def initialize(term: str) -> np.ndarray | None:
        word = term.lower()
        vectors = [
            rows[word[start : start + size]]
            for size in range(3, 7)
            for start in range(len(word) - size + 1)
            if word[start : start + size] in rows
        ]
        return np.mean(vectors, axis=0) if vectors else None

    return initialize


def _export_fasttext(source: Path, terms: tuple[str, ...], destination: Path) -> None:
    from gensim.models.fasttext import load_facebook_vectors

    vectors = load_facebook_vectors(str(source))
    selected = tuple(term for term in terms if term in vectors)
    matrix = np.asarray([vectors[term] for term in selected], dtype=np.float32)
    _save(destination, selected, matrix, "full-force")


def _save(
    destination: Path,
    vocabulary: tuple[str, ...],
    vectors: np.ndarray,
    version: str,
) -> SeedVectors:
    destination.mkdir(parents=True, exist_ok=True)
    path = destination / "baseline-vectors.npy"
    np.save(path, vectors)
    (destination / "baseline-vocabulary.json").write_text(json.dumps(vocabulary), encoding="utf-8")
    vector_size = vectors.shape[1] if vectors.ndim == 2 and len(vectors) else 0
    return SeedVectors(vocabulary, path, vector_size, version)


def _read_export(destination: Path, *, version: str) -> SeedVectors:
    vocabulary = tuple(
        json.loads((destination / "baseline-vocabulary.json").read_text(encoding="utf-8"))
    )
    vectors = np.load(destination / "baseline-vectors.npy", mmap_mode="r")
    if len(vocabulary) != len(vectors):
        raise ValueError("exported vocabulary and vector row count differ")
    vector_size = vectors.shape[1] if vectors.ndim == 2 and len(vectors) else 0
    return SeedVectors(vocabulary, destination / "baseline-vectors.npy", vector_size, version)
