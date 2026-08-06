"""Compact project-level embedding adaptation."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

import numpy as np

from codesense.indexing.finetune.artifacts import ArtifactPaths, write_manifest
from codesense.indexing.finetune.config import FINETUNE_PROFILES, FinetuneConfig
from codesense.indexing.finetune.expansion import build_vector_expansion
from codesense.indexing.finetune.initializers import initialize_terms
from codesense.indexing.finetune.providers import (
    SeedVectors,
    load_subword_initializer,
    prepare_compact,
    prepare_full_force,
    prepare_warn_full,
)
from codesense.indexing.finetune.trainer import train_word2vec
from codesense.indexing.finetune.vocabulary import plan_vocabulary

__all__ = ["FINETUNE_PROFILES", "FinetuneConfig", "FinetuneResult", "run_finetune"]


@dataclass(frozen=True, slots=True)
class FinetuneResult:
    expansion: dict[str, list[tuple[str, float, str]]]


def run_finetune(
    *,
    project_terms: Mapping[str, int],
    targets: Mapping[str, int],
    lexical: Mapping[str, Sequence[tuple[str, float, str]]],
    corpus: Iterable[Sequence[str]],
    config: FinetuneConfig,
    min_cosine: float,
    max_targets: int,
) -> FinetuneResult:
    """Prepare, adapt, persist, and compare one compact project space."""
    if config.artifact_dir is None:
        raise ValueError("finetune needs artifact_dir")
    paths = ArtifactPaths(config.artifact_dir)
    general, vector_size = _base_metadata(config, lexical)
    plan = plan_vocabulary(
        general=general,
        project_df=project_terms,
        targets=targets,
        vector_size=vector_size,
        memory_budget_mb=config.memory_budget_mb,
    )
    prepared = _prepare(config, plan.terms, paths, corpus)
    baseline = prepared.baseline()
    seeds = initialize_terms(
        terms=plan.terms,
        base=prepared.adapted(),
        canonical=_canonical_forms(plan.project, plan.general),
        components={},
        contexts=_contexts(corpus, baseline),
        subword=(
            load_subword_initializer(config.model_path) if config.profile == "lightweight" else None
        ),
        vector_size=prepared.vector_size,
    )
    np.save(paths.baseline_vectors, np.asarray([seeds[term] for term in plan.terms]))
    paths.baseline_vocabulary.write_text(json.dumps(plan.terms), encoding="utf-8")
    embedding = train_word2vec(
        sentences=corpus,
        seeds=seeds,
        frozen=set(plan.general),
        epochs=config.epochs,
        workers=config.workers,
    )
    embedding.save(paths.project_model)
    adapted = {term: embedding.vector(term) for term in plan.terms}
    expansion = build_vector_expansion(
        keys=plan.general,
        targets=tuple(targets),
        baseline=seeds,
        adapted=adapted,
        min_cosine=min_cosine,
        max_targets=max_targets,
    )
    write_manifest(
        paths,
        {
            "profile": config.profile,
            "base_version": prepared.version,
            "vector_size": prepared.vector_size,
            "vocabulary_size": len(plan.terms),
            "epochs": config.epochs,
        },
    )
    return FinetuneResult(expansion)


def _base_metadata(
    config: FinetuneConfig,
    lexical: Mapping[str, Sequence[tuple[str, float, str]]],
) -> tuple[tuple[str, ...], int]:
    assert config.model_path is not None
    if config.profile != "lightweight":
        return tuple(lexical), 300
    manifest = json.loads((config.model_path / "manifest.json").read_text(encoding="utf-8"))
    general = tuple(
        (config.model_path / "compact-vocabulary.txt").read_text(encoding="utf-8").splitlines()
    )
    return general, int(manifest["vector_size"])


def _prepare(
    config: FinetuneConfig,
    terms: tuple[str, ...],
    paths: ArtifactPaths,
    corpus: Iterable[Sequence[str]],
) -> SeedVectors:
    assert config.model_path is not None
    if config.profile == "lightweight":
        return prepare_compact(config.model_path, terms, paths.embedding)
    if config.profile == "full_force":
        return prepare_full_force(config.model_path, terms, paths.embedding)

    from codesense.indexing.grounding import GroundingConfig, VectorSpace

    legacy = GroundingConfig(
        strategy="finetune",
        model_path=config.model_path,
        epochs=config.epochs,
        workers=config.workers,
    )
    space = VectorSpace.load(config.model_path, config=legacy)
    return prepare_warn_full(
        space,
        [list(sentence) for sentence in corpus],
        terms,
        paths.embedding,
        config=legacy,
        preserve_full_model=config.preserve_full_model,
    )


def _canonical_forms(project: Sequence[str], general: Sequence[str]) -> dict[str, str]:
    found: dict[str, str] = {}
    for short in project:
        candidates = [
            long
            for long in general
            if len(short) <= 5
            and len(short) < len(long) <= len(short) * 3
            and short[0] == long[0]
            and _subsequence(short, long)
        ]
        if candidates:
            found[short] = min(candidates, key=lambda term: (len(term), term))
    return found


def _contexts(
    corpus: Iterable[Sequence[str]], base: Mapping[str, np.ndarray]
) -> dict[str, tuple[str, ...]]:
    found: dict[str, set[str]] = defaultdict(set)
    for sentence in corpus:
        known = [term for term in sentence if term in base]
        for term in sentence:
            found[term].update(other for other in known if other != term)
    return {term: tuple(sorted(context)) for term, context in found.items()}


def _subsequence(short: str, long: str) -> bool:
    letters = iter(long)
    return all(character in letters for character in short)
