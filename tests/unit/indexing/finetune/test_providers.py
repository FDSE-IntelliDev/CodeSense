"""Compact and full-force vector preparation boundaries."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from codesense.indexing.finetune.providers import (
    load_subword_initializer,
    prepare_compact,
    prepare_full_force,
)


def _compact_fixture(path: Path) -> None:
    path.mkdir()
    (path / "compact-vocabulary.txt").write_text("permission\nrole\nunused\n")
    np.save(
        path / "baseline-vectors.npy",
        np.array([[1.0, 0.0], [0.0, 1.0], [-1.0, 0.0]], dtype=np.float32),
    )
    (path / "manifest.json").write_text(
        json.dumps({"vector_size": 2, "vocabulary_version": "demo-v1"})
    )


def test_compact_provider_selects_requested_rows(tmp_path: Path) -> None:
    compact = tmp_path / "compact"
    _compact_fixture(compact)

    seeds = prepare_compact(compact, ("permission", "missing", "role"), tmp_path / "out")

    assert seeds.vocabulary == ("permission", "role")
    assert np.array_equal(
        np.load(seeds.vectors_path),
        np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32),
    )


def test_full_force_reads_files_written_by_runner(tmp_path: Path) -> None:
    model = tmp_path / "cc.en.bin"
    model.write_bytes(b"fixture")
    events: list[str] = []

    def runner(source: Path, terms: tuple[str, ...], destination: Path) -> None:
        assert source == model
        events.append("run")
        np.save(destination / "baseline-vectors.npy", np.array([[1.0, 0.0]], np.float32))
        (destination / "baseline-vocabulary.json").write_text(json.dumps(terms))
        events.append("return")

    seeds = prepare_full_force(model, ("permission",), tmp_path / "out", runner=runner)

    assert events == ["run", "return"]
    assert seeds.vocabulary == ("permission",)


def test_optional_subword_file_initializes_known_ngrams(tmp_path: Path) -> None:
    np.savez(
        tmp_path / "subword-initializer.npz",
        ngrams=np.array(["cac", "ach", "che"]),
        vectors=np.array([[1.0, 0.0], [1.0, 0.0], [0.0, 1.0]], dtype=np.float32),
    )

    initializer = load_subword_initializer(tmp_path)

    assert initializer is not None
    assert initializer("cacheable") is not None
