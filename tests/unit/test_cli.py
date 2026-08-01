"""Tests for the command line.

Two things matter here and neither is about output formatting:

**Discovery.** ``query`` takes one argument only because it finds the index by
walking up, the way git finds ``.git``. If that breaks, every command needs
``--index`` again and the whole shape of the CLI is gone.

**Exit codes.** A CLI that prints an error and exits 0 is worse than one that
crashes -- scripts wrapping it will not notice.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from codesense.cli import INDEX_DIR, find_index, main
from tests.unit.test_search import make_searchable_index


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A directory holding a saved index, plus a nested path to search from.

    The index needs filler symbols: `icf_ratio` is ``log(N/df)/log(N)``, so in
    a two-symbol index every term scores exactly zero and no query can hit.
    """
    make_searchable_index().save(tmp_path / INDEX_DIR)
    (tmp_path / "src" / "main" / "deep").mkdir(parents=True)
    return tmp_path


class TestFindIndex:
    def test_finds_an_index_in_the_directory_itself(self, repo: Path) -> None:
        assert find_index(repo) == repo / INDEX_DIR

    def test_walks_up_from_a_nested_directory(self, repo: Path) -> None:
        """This is what makes `query` a one-argument command."""
        assert find_index(repo / "src" / "main" / "deep") == repo / INDEX_DIR

    def test_returns_none_when_there_is_none(self, tmp_path: Path) -> None:
        """None rather than raising, so the caller can say `run codesense
        init` instead of printing a traceback."""
        assert find_index(tmp_path) is None

    def test_ignores_a_directory_without_meta(self, tmp_path: Path) -> None:
        """meta.json is written last, so its absence means an incomplete
        write -- not an index."""
        (tmp_path / INDEX_DIR).mkdir()
        assert find_index(tmp_path) is None


class TestExitCodes:
    def test_no_subcommand_prints_help_and_fails(self, capsys) -> None:  # type: ignore[no-untyped-def]
        assert main([]) == 2
        assert "init" in capsys.readouterr().out

    def test_query_without_an_index_is_actionable(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:  # type: ignore[no-untyped-def]
        monkeypatch.chdir(tmp_path)
        assert main(["query", "anything"]) == 2
        assert "codesense init" in capsys.readouterr().err

    def test_query_finds_something(self, repo: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        monkeypatch.chdir(repo)
        assert main(["query", "alloc", "--route", "lexical"]) == 0

    def test_query_finding_nothing_exits_1(self, repo: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        """Distinct from 2: the index was found, the query simply missed."""
        monkeypatch.chdir(repo)
        assert main(["query", "zzzz qqqq", "--route", "lexical"]) == 1

    def test_init_refuses_to_clobber(self, repo: Path, capsys) -> None:  # type: ignore[no-untyped-def]
        assert main(["init", str(repo)]) == 1
        assert "--force" in capsys.readouterr().err

    def test_init_rejects_a_non_directory(self, tmp_path: Path) -> None:
        target = tmp_path / "file.txt"
        target.write_text("x")
        assert main(["init", str(target)]) == 2

    def test_a_vector_strategy_needs_the_model(self, tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
        code = main(["init", str(tmp_path), "--strategy", "vectors"])
        assert code == 2
        assert "--vectors" in capsys.readouterr().err

    def test_info_without_an_index_fails(self, tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        monkeypatch.chdir(tmp_path)
        assert main(["info"]) == 2


class TestQueryOutput:
    def test_explicit_index_beats_discovery(self, repo: Path, tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        monkeypatch.chdir(tmp_path)
        assert main(["query", "alloc", "--route", "lexical", "--index", str(repo / INDEX_DIR)]) == 0

    def test_limit_is_honoured(self, repo: Path, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
        monkeypatch.chdir(repo)
        main(["query", "alloc", "--route", "lexical", "-n", "1"])
        hits = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip().startswith("1.")]
        assert len(hits) == 1

    def test_script_flag_prints_the_trace(self, repo: Path, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
        monkeypatch.chdir(repo)
        main(["query", "alloc", "--route", "lexical", "--script"])
        assert "route :" in capsys.readouterr().out

    def test_why_prints_the_evidence(self, repo: Path, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
        monkeypatch.chdir(repo)
        main(["query", "alloc", "--route", "lexical", "--why"])
        assert "@name" in capsys.readouterr().out


class TestInfo:
    def test_reports_the_index_it_found(self, repo: Path, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
        monkeypatch.chdir(repo)
        assert main(["info"]) == 0
        assert "demo" in capsys.readouterr().out

    def test_terms_prints_the_vocabulary(self, repo: Path, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
        monkeypatch.chdir(repo)
        main(["info", "--terms", "2"])
        assert "alloc" in capsys.readouterr().out


class TestLlmConfiguration:
    def test_the_lexical_route_never_builds_a_config(self, repo: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        """No key, no network, no import of the llm package."""
        monkeypatch.delenv("CODESENSE_API_KEY", raising=False)
        monkeypatch.chdir(repo)
        assert main(["query", "alloc", "--route", "lexical"]) == 0

    def test_a_missing_key_degrades_rather_than_failing(
        self, repo: Path, tmp_path: Path, monkeypatch, capsys
    ) -> None:  # type: ignore[no-untyped-def]
        """A search that works without a key is worth more than one that
        refuses."""
        monkeypatch.delenv("CODESENSE_API_KEY", raising=False)
        monkeypatch.chdir(tmp_path)  # away from any config.yml
        assert main(["query", "alloc", "--index", str(repo / INDEX_DIR)]) == 0
        assert "lexical" in capsys.readouterr().err

    def test_the_environment_supplies_endpoint_and_model(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        """Retyping them on every query is how people end up writing a config
        file, and a config file becomes a second source of truth."""
        import argparse

        from codesense.cli import _llm

        monkeypatch.setenv("CODESENSE_API_KEY", "sk-test")
        monkeypatch.setenv("CODESENSE_BASE_URL", "https://example.invalid/v1")
        monkeypatch.setenv("CODESENSE_MODEL", "some-model")
        args = argparse.Namespace(route="codegen", base_url=None, model=None)
        config = _llm(args)
        assert config.base_url == "https://example.invalid/v1"
        assert config.model == "some-model"

    def test_a_flag_beats_the_environment(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        import argparse

        from codesense.cli import _llm

        monkeypatch.setenv("CODESENSE_API_KEY", "sk-test")
        monkeypatch.setenv("CODESENSE_MODEL", "from-env")
        args = argparse.Namespace(route="codegen", base_url=None, model="from-flag")
        assert _llm(args).model == "from-flag"


class TestInitBuilds:
    def test_indexes_a_repository_and_makes_it_queryable(self, tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        """The whole point of the CLI: init once, then query."""
        from codesense.lang import LANGUAGES
        from tests.unit.lang.test_registry import ToyLanguage

        # 30 unrelated names so `buffer` is rare enough to score
        names = ["readBuffer", "writeBuffer"] + [f"unrelated{i}Thing" for i in range(30)]
        (tmp_path / "a.toy").write_text("\n".join(names) + "\n")
        registered = "toy" in LANGUAGES.names()
        if not registered:
            LANGUAGES.register(ToyLanguage())
        try:
            monkeypatch.chdir(tmp_path)
            assert main(["init"]) == 0
            assert (tmp_path / INDEX_DIR / "meta.json").is_file()
            assert main(["query", "buffer", "--route", "lexical"]) == 0
        finally:
            if not registered:
                LANGUAGES._by_name.pop("toy", None)

    def test_out_overrides_the_default_location(self, tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        from codesense.lang import LANGUAGES
        from tests.unit.lang.test_registry import ToyLanguage

        source = tmp_path / "src"
        source.mkdir()
        (source / "a.toy").write_text("readBuffer\nwriteBuffer\n")  # names only; not queried
        registered = "toy" in LANGUAGES.names()
        if not registered:
            LANGUAGES.register(ToyLanguage())
        try:
            elsewhere = tmp_path / "elsewhere"
            assert main(["init", str(source), "--out", str(elsewhere)]) == 0
            assert json.loads((elsewhere / "meta.json").read_text())["project"] == "src"
        finally:
            if not registered:
                LANGUAGES._by_name.pop("toy", None)
