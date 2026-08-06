"""The lightweight profile builds a reusable search index without FastText."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from codesense import Project
from codesense.lang import LANGUAGES
from tests.unit.lang.test_registry import ToyLanguage


def test_lightweight_builds_project_model_and_searchable_expansion(
    tmp_path: Path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    source = tmp_path / "source"
    source.mkdir()
    names = ["permsRole", "permsScope"] + [f"unrelated{i}Thing" for i in range(30)]
    (source / "a.toy").write_text("\n".join(names), encoding="utf-8")

    compact = tmp_path / "compact"
    compact.mkdir()
    (compact / "compact-vocabulary.txt").write_text("permission\nrole\nscope\n", encoding="utf-8")
    np.save(
        compact / "baseline-vectors.npy",
        np.array([[1.0, 0.0], [0.0, 1.0], [0.2, 0.8]], dtype=np.float32),
    )
    (compact / "manifest.json").write_text(
        json.dumps({"vector_size": 2, "vocabulary_version": "test-v1"}),
        encoding="utf-8",
    )

    def fail(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("lightweight must not load Facebook FastText")

    monkeypatch.setattr("gensim.models.fasttext.load_facebook_model", fail)
    monkeypatch.setattr("gensim.models.fasttext.load_facebook_vectors", fail)
    registered = "toy" in LANGUAGES.names()
    if not registered:
        LANGUAGES.register(ToyLanguage())
    index_dir = tmp_path / "index"
    try:
        project = Project.build(
            source,
            index_dir=index_dir,
            strategy="finetune",
            model_path=compact,
            epochs=1,
            workers=1,
            verbose=False,
        )
        assert project.index.meta.grounding_profile == "lightweight"
        assert project.index.meta.grounding_status == "ready"
        assert project.index.expansion["permission"][0][0] == "perms"
        assert (index_dir / "embedding" / "project.model").is_file()
        assert (index_dir / "embedding" / "manifest.json").is_file()
        baseline_vocabulary = json.loads(
            (index_dir / "embedding" / "baseline-vocabulary.json").read_text(encoding="utf-8")
        )
        assert "perms" in baseline_vocabulary

        reopened = Project.open(index_dir)
        assert reopened.search("permission", route="lexical").hits
    finally:
        if not registered:
            LANGUAGES._by_name.pop("toy", None)
