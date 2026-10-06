"""Behavior tests for the shared repository checkout cache."""

from __future__ import annotations

from pathlib import Path

import pytest

from evaluation.repo_cache import resolve_repo


def _fake_run(calls: list[list[str]]):
    def run(command: list[str], *, check: bool) -> None:
        assert check is True
        calls.append(command)
        if command[1] == "clone":
            Path(command[-1]).mkdir(parents=True)

    return run


def test_manual_mapping_wins_and_is_never_checked_out(tmp_path: Path) -> None:
    manual = tmp_path / "manual"
    manual.mkdir()
    calls: list[list[str]] = []

    found = resolve_repo(
        "owner/repo",
        "abc123",
        tmp_path / "projects",
        manual_paths={"owner/repo": str(manual)},
        run=_fake_run(calls),
    )

    assert found == manual.resolve()
    assert calls == []


def test_invalid_repository_name_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        resolve_repo("not-a-repo", None, tmp_path / "projects")


def test_missing_repo_without_commit_is_shallow_cloned_plain_name(tmp_path: Path) -> None:
    projects = tmp_path / "projects"
    calls: list[list[str]] = []

    found = resolve_repo("owner/repo", None, projects, run=_fake_run(calls))

    assert found == (projects / "owner__repo").resolve()
    assert calls == [
        [
            "git",
            "clone",
            "--depth",
            "1",
            "https://github.com/owner/repo.git",
            str(projects / "owner__repo"),
        ]
    ]


def test_missing_repo_with_commit_is_full_cloned_then_checked_out(tmp_path: Path) -> None:
    projects = tmp_path / "projects"
    target = projects / "owner__repo__abc123"
    calls: list[list[str]] = []

    found = resolve_repo("owner/repo", "abc123", projects, run=_fake_run(calls))

    assert found == target.resolve()
    assert calls == [
        ["git", "clone", "https://github.com/owner/repo.git", str(target)],
        ["git", "-C", str(target), "checkout", "abc123"],
    ]


def test_existing_commit_target_is_reused_without_git_commands(tmp_path: Path) -> None:
    projects = tmp_path / "projects"
    target = projects / "owner__repo__abc123"
    target.mkdir(parents=True)
    calls: list[list[str]] = []

    found = resolve_repo("owner/repo", "abc123", projects, run=_fake_run(calls))

    assert found == target.resolve()
    assert calls == []


def test_two_commits_of_one_repo_get_distinct_directories(tmp_path: Path) -> None:
    projects = tmp_path / "projects"
    calls: list[list[str]] = []
    run = _fake_run(calls)

    first = resolve_repo("owner/repo", "c1", projects, run=run)
    second = resolve_repo("owner/repo", "c2", projects, run=run)

    assert first != second
    assert first == (projects / "owner__repo__c1").resolve()
    assert second == (projects / "owner__repo__c2").resolve()
