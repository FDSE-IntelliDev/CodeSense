"""Minimal contracts shared by the finetune demo."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from codesense.indexing.finetune.artifacts import ArtifactPaths, write_manifest
from codesense.indexing.finetune.config import FinetuneConfig
from codesense.indexing.finetune.corpus import CorpusStore


def test_warn_full_requires_confirmation() -> None:
    with pytest.raises(ValueError, match="allow_unsafe_full"):
        FinetuneConfig(profile="warn_full", model_path=Path("cc.en.bin"))


def test_default_budget_is_two_gibibytes() -> None:
    assert FinetuneConfig(model_path=Path("compact")).memory_budget_mb == 2048


def test_corpus_is_reiterable(tmp_path: Path) -> None:
    corpus = CorpusStore(tmp_path / "corpus.jsonl")
    corpus.append("a.py", [["permission", "role"], ["perms", "role"]])

    assert list(corpus.sentences()) == [
        ["permission", "role"],
        ["perms", "role"],
    ]
    assert list(corpus.sentences()) == list(corpus.sentences())
    assert list(corpus) == list(corpus.sentences())


def test_manifest_uses_fixed_embedding_path(tmp_path: Path) -> None:
    paths = ArtifactPaths(tmp_path / ".codesense")
    write_manifest(paths, {"profile": "lightweight", "epochs": 2})

    assert json.loads(paths.manifest.read_text()) == {
        "epochs": 2,
        "profile": "lightweight",
    }
    assert paths.project_model == tmp_path / ".codesense" / "embedding" / "project.model"
