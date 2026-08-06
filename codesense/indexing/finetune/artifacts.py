"""Fixed on-disk layout for the compact project model."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ArtifactPaths:
    index_dir: Path

    @property
    def embedding(self) -> Path:
        return self.index_dir / "embedding"

    @property
    def project_model(self) -> Path:
        return self.embedding / "project.model"

    @property
    def baseline_vectors(self) -> Path:
        return self.embedding / "baseline-vectors.npy"

    @property
    def baseline_vocabulary(self) -> Path:
        return self.embedding / "baseline-vocabulary.json"

    @property
    def manifest(self) -> Path:
        return self.embedding / "manifest.json"


def write_manifest(paths: ArtifactPaths, values: dict[str, object]) -> None:
    paths.embedding.mkdir(parents=True, exist_ok=True)
    paths.manifest.write_text(
        json.dumps(values, ensure_ascii=False, indent=1, sort_keys=True),
        encoding="utf-8",
    )
