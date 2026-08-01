"""Utilities to fetch source text by file path and line range."""

from pathlib import Path
from typing import Dict, List, Optional


def read_file_lines(file_path: str, encoding: str = "utf-8") -> List[str]:
    """
    Read all lines from a source file. Returns [] if file does not exist.
    """
    p = Path(file_path)
    if not p.exists() or not p.is_file():
        return []
    try:
        return p.read_text(encoding=encoding).splitlines()
    except UnicodeDecodeError:
        # Fallback for mixed encodings in real repositories.
        return p.read_text(encoding="utf-8", errors="replace").splitlines()


def get_code_by_line_range(
    file_path: str,
    start_line: int,
    end_line: int,
    encoding: str = "utf-8",
    keep_trailing_newline: bool = False,
) -> str:
    """
    Get source snippet by 1-based inclusive line range.

    - start_line/end_line are clamped into valid file bounds.
    - returns empty string if file missing or invalid range.
    """
    if start_line <= 0 or end_line <= 0 or end_line < start_line:
        return ""

    lines = read_file_lines(file_path, encoding=encoding)
    if not lines:
        return ""

    n = len(lines)
    s = max(1, min(start_line, n))
    e = max(1, min(end_line, n))
    if e < s:
        return ""

    snippet = "\n".join(lines[s - 1 : e])
    if keep_trailing_newline:
        snippet += "\n"
    return snippet


def get_symbol_code(
    project_root: str,
    symbol_item: Dict,
    encoding: str = "utf-8",
) -> str:
    """
    Fetch code snippet directly from one symbol record in symbols_index.json.

    Expected symbol_item fields:
      - file: absolute path
      - range.start_line
      - range.end_line
    """
    rel_file = symbol_item.get("file", "")
    rng = symbol_item.get("range", {}) or {}
    start_line = int(rng.get("start_line", 0) or 0)
    end_line = int(rng.get("end_line", 0) or 0)
    if not rel_file or start_line <= 0 or end_line <= 0:
        return ""

    abs_file = str(Path(project_root) / rel_file)
    return get_code_by_line_range(
        file_path=abs_file,
        start_line=start_line,
        end_line=end_line,
        encoding=encoding,
    )
