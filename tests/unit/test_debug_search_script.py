"""Behavior tests for the Python API debugging entry point."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "debug_search.py"


def _load_script() -> ModuleType:
    """Load the real script so tests exercise its argument and branch logic."""
    assert SCRIPT.is_file(), "scripts/debug_search.py must provide the debugging entry point"
    spec = importlib.util.spec_from_file_location("debug_search", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Result:
    hits = (object(),)

    def explain(self) -> str:
        return "search trace"


class _NoLlm:
    @classmethod
    def load(cls, **params: object) -> object:
        raise AssertionError(f"lexical search must not load an LLM config: {params}")


def _configure(module: ModuleType, monkeypatch, root: Path, *, route: str = "lexical") -> None:  # type: ignore[no-untyped-def]
    """Replace the script's editable constants without touching a real project."""
    monkeypatch.setattr(module, "PROJECT_ROOT", str(root))
    monkeypatch.setattr(module, "QUERY", "buffer allocation")
    monkeypatch.setattr(module, "ROUTE", route)
    monkeypatch.setattr(module, "GROUNDING_STRATEGY", "finetune", raising=False)
    monkeypatch.setattr(module, "FASTTEXT_MODEL", root / "model" / "cc.en.300.bin", raising=False)
    monkeypatch.setattr(module, "LIMIT", 7)
    monkeypatch.setattr(module, "BASE_URL", "https://example.invalid/v1")
    monkeypatch.setattr(module, "MODEL", "demo-model")
    monkeypatch.setattr(module, "TIMEOUT", 12.5)


def test_existing_index_is_opened_and_searched(tmp_path: Path, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    """Removing the meta check would rebuild and make iterative debugging slow."""
    module = _load_script()
    index_dir = tmp_path / ".codesense"
    index_dir.mkdir()
    (index_dir / "meta.json").write_text("{}", encoding="utf-8")
    calls: dict[str, object] = {}

    class FakeProject:
        @classmethod
        def open(cls, path: Path, *, llm: object = None) -> FakeProject:
            calls["open"] = (path, llm)
            return cls()

        @classmethod
        def build(cls, *args: object, **kwargs: object) -> FakeProject:
            raise AssertionError(f"an existing index must not be rebuilt: {args}, {kwargs}")

        def search(self, query: str, *, route: str, limit: int) -> _Result:
            calls["search"] = (query, route, limit)
            return _Result()

    monkeypatch.setattr(module, "Project", FakeProject)
    monkeypatch.setattr(module, "LlmConfig", _NoLlm)
    _configure(module, monkeypatch, tmp_path)

    code = module.main()

    assert code == 0
    assert calls == {
        "open": (index_dir, None),
        "search": ("buffer allocation", "lexical", 7),
    }
    assert "search trace" in capsys.readouterr().out


def test_missing_index_is_built_before_search(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Removing the build branch would make first-run debugging unusable."""
    module = _load_script()
    calls: dict[str, object] = {}

    class FakeProject:
        @classmethod
        def open(cls, *args: object, **kwargs: object) -> FakeProject:
            raise AssertionError(f"a missing index must not be opened: {args}, {kwargs}")

        @classmethod
        def build(
            cls,
            root: Path,
            *,
            index_dir: Path,
            strategy: str,
            model_path: Path,
            llm: object = None,
        ) -> FakeProject:
            calls["build"] = (root, index_dir, strategy, model_path, llm)
            return cls()

        def search(self, query: str, *, route: str, limit: int) -> _Result:
            calls["search"] = (query, route, limit)
            return _Result()

    monkeypatch.setattr(module, "Project", FakeProject)
    monkeypatch.setattr(module, "LlmConfig", _NoLlm)
    _configure(module, monkeypatch, tmp_path)

    code = module.main()

    assert code == 0
    assert calls == {
        "build": (
            tmp_path,
            tmp_path / ".codesense",
            "finetune",
            tmp_path / "model" / "cc.en.300.bin",
            None,
        ),
        "search": ("buffer allocation", "lexical", 7),
    }


def test_codegen_parameters_are_injected_into_the_llm(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Ignoring endpoint/model constants would silently query the wrong model."""
    module = _load_script()
    index_dir = tmp_path / ".codesense"
    index_dir.mkdir()
    (index_dir / "meta.json").write_text("{}", encoding="utf-8")
    calls: dict[str, object] = {}
    configured = object()

    class FakeLlmConfig:
        @classmethod
        def load(cls, **params: object) -> object:
            calls["llm"] = params
            return configured

    class FakeProject:
        @classmethod
        def open(cls, path: Path, *, llm: object = None) -> FakeProject:
            calls["open"] = (path, llm)
            return cls()

        def search(self, query: str, *, route: str, limit: int) -> _Result:
            calls["search"] = (query, route, limit)
            return _Result()

    monkeypatch.setattr(module, "Project", FakeProject)
    monkeypatch.setattr(module, "LlmConfig", FakeLlmConfig)
    _configure(module, monkeypatch, tmp_path, route="codegen")

    code = module.main()

    assert code == 0
    assert calls == {
        "llm": {
            "base_url": "https://example.invalid/v1",
            "model": "demo-model",
            "timeout": 12.5,
        },
        "open": (index_dir, configured),
        "search": ("buffer allocation", "codegen", 7),
    }
