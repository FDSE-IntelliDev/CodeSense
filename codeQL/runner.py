"""Small, testable wrapper around the CodeQL CLI."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Optional, Sequence, Tuple


class CodeQLError(RuntimeError):
    """Base error for CodeQL setup and command failures."""


class CodeQLNotFoundError(CodeQLError):
    """Raised when the CodeQL CLI cannot be located."""


class CodeQLCommandError(CodeQLError):
    """Raised when a CodeQL subprocess exits unsuccessfully."""


@dataclass(frozen=True)
class QuerySpec:
    name: str
    file_name: str
    columns: Tuple[str, ...]


JAVA_QUERY_SPECS: Tuple[QuerySpec, ...] = (
    QuerySpec("files", "Files.ql", ("file",)),
    QuerySpec(
        "symbols",
        "Symbols.ql",
        (
            "stable_key",
            "name",
            "type",
            "file",
            "start_line",
            "end_line",
            "start_col",
            "signature",
            "container",
            "qualified_name",
            "language",
        ),
    ),
    QuerySpec(
        "dependencies",
        "Dependencies.ql",
        ("source_file", "target", "type"),
    ),
    QuerySpec(
        "calls",
        "Calls.ql",
        (
            "source_key",
            "target_key",
            "source_name",
            "target_name",
            "source_file",
            "call_line",
            "call_col",
            "code",
        ),
    ),
    QuerySpec(
        "implementations",
        "Implementations.ql",
        (
            "abstract_key",
            "implementation_key",
            "abstract_owner_key",
            "implementation_owner_key",
            "relation_kind",
        ),
    ),
)


def find_codeql(explicit_path: Optional[str] = None) -> str:
    """Resolve the CodeQL executable without invoking a shell."""
    if explicit_path:
        candidate = Path(explicit_path).expanduser().resolve()
        if candidate.is_file():
            return str(candidate)
        raise CodeQLNotFoundError(f"CodeQL executable does not exist: {candidate}")

    discovered = shutil.which("codeql")
    if discovered:
        return discovered
    raise CodeQLNotFoundError(
        "CodeQL CLI was not found. Install the official CodeQL bundle and add "
        "its directory to PATH, or pass --codeql-bin. See codeQL/README.md."
    )


class CodeQLRunner:
    """Create a CodeQL database and decode a fixed set of bulk queries."""

    def __init__(
        self,
        codeql_bin: Optional[str] = None,
        threads: int = 0,
        ram_mb: Optional[int] = None,
    ) -> None:
        self.codeql_bin = find_codeql(codeql_bin)
        self.threads = int(threads)
        self.ram_mb = int(ram_mb) if ram_mb else None

    def _resource_args(self) -> list[str]:
        args = [f"--threads={self.threads}"]
        if self.ram_mb:
            args.append(f"--ram={self.ram_mb}")
        return args

    def _run(self, args: Sequence[str], cwd: Optional[Path] = None) -> str:
        command = [self.codeql_bin, *args]
        completed = subprocess.run(
            command,
            cwd=str(cwd) if cwd else None,
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise CodeQLCommandError(
                f"CodeQL command failed ({completed.returncode}): "
                f"{' '.join(command)}\n{detail}"
            )
        return completed.stdout.strip()

    def version(self) -> str:
        return self._run(("version", "--format=terse"))

    def create_database(
        self,
        project_root: Path,
        database_path: Path,
        language: str = "java-kotlin",
        build_mode: str = "none",
        build_command: Optional[str] = None,
    ) -> None:
        """Extract one project into a reusable CodeQL database directory."""
        args = [
            "database",
            "create",
            str(database_path),
            f"--language={language}",
            f"--source-root={project_root}",
            "--overwrite",
            *self._resource_args(),
        ]
        if build_command:
            args.append(f"--command={build_command}")
        else:
            args.append(f"--build-mode={build_mode}")
        self._run(args, cwd=project_root)

    def run_queries(
        self,
        database_path: Path,
        query_dir: Path,
        result_dir: Path,
        specs: Iterable[QuerySpec] = JAVA_QUERY_SPECS,
        keep_bqrs: bool = False,
    ) -> Dict[str, Path]:
        """Run each set-valued query once and decode primitive tuples as CSV."""
        result_dir.mkdir(parents=True, exist_ok=True)
        decoded: Dict[str, Path] = {}

        for spec in specs:
            query_path = query_dir / spec.file_name
            if not query_path.is_file():
                raise CodeQLError(f"Missing CodeQL query: {query_path}")

            bqrs_path = result_dir / f"{spec.name}.bqrs"
            csv_path = result_dir / f"{spec.name}.csv"
            bqrs_path.unlink(missing_ok=True)
            csv_path.unlink(missing_ok=True)

            self._run(
                (
                    "query",
                    "run",
                    f"--database={database_path}",
                    f"--output={bqrs_path}",
                    *self._resource_args(),
                    str(query_path),
                )
            )
            self._run(
                (
                    "bqrs",
                    "decode",
                    "--format=csv",
                    "--no-titles",
                    f"--output={csv_path}",
                    str(bqrs_path),
                )
            )
            if not keep_bqrs:
                bqrs_path.unlink(missing_ok=True)
            decoded[spec.name] = csv_path
        return decoded
