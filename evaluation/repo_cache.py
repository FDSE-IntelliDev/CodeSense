"""Shared repository checkout cache for the evaluation pipeline.

Both the miner and the evaluator need a working tree pinned to a specific
``base_commit``. Clones are cached under ``evaluation/projects`` and named
``{owner}__{repo}__{commit}`` so two revisions of one repository never share
(and overwrite) a single working tree.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path

__all__ = ["DEFAULT_PROJECTS_DIR", "resolve_repo"]

#: Repository root is the parent of this package directory.
_REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROJECTS_DIR = _REPO_ROOT / "evaluation" / "projects"

_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def resolve_repo(
    repo: str,
    base_commit: str | None = None,
    projects_dir: Path = DEFAULT_PROJECTS_DIR,
    *,
    manual_paths: Mapping[str, str] | None = None,
    run: Callable[..., object] = subprocess.run,
) -> Path:
    """Return a working tree for ``repo`` pinned to ``base_commit``.

    A manual path wins and is never checked out (moving a user-managed HEAD is
    destructive). Otherwise a commit-specific cached clone under
    ``projects_dir`` is reused as-is; when absent the repository is cloned -- in
    full when a commit is required, shallow otherwise. The directory name carries
    the commit so distinct revisions stay isolated without repeated Git work.
    """
    manual = (manual_paths or {}).get(repo)
    if manual:
        path = Path(manual).expanduser().resolve()
        if not path.is_dir():
            raise NotADirectoryError(path)
        return path
    if not _REPOSITORY.fullmatch(repo):
        raise ValueError(f"invalid GitHub repository name: {repo!r}")

    projects_dir.mkdir(parents=True, exist_ok=True)
    name = repo.replace("/", "__")
    if base_commit:
        name = f"{name}__{base_commit}"
    target = projects_dir / name
    url = f"https://github.com/{repo}.git"
    if target.is_dir():
        return target.resolve()
    if base_commit:
        # A shallow clone of the default branch cannot reach an arbitrary
        # commit, so clone in full and then check the exact revision out.
        run(["git", "clone", url, str(target)], check=True)
        run(["git", "-C", str(target), "checkout", base_commit], check=True)
    else:
        run(["git", "clone", "--depth", "1", url, str(target)], check=True)
    if not target.is_dir():
        raise RuntimeError(f"git clone did not create {target}")
    return target.resolve()
