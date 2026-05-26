import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT_DIR = Path(__file__).resolve().parent.parent
OUTPUT_ROOT = ROOT_DIR / "output"


@lru_cache(maxsize=1)
def _load_symbol_lookup() -> Dict[Tuple[str, str], Dict[str, Any]]:
    """Load all `symbols_index.json` files under `output/` into a lookup map."""
    lookup: Dict[Tuple[str, str], Dict[str, Any]] = {}
    if not OUTPUT_ROOT.exists():
        return lookup

    for index_path in OUTPUT_ROOT.rglob("symbols_index.json"):
        try:
            with index_path.open("r", encoding="utf-8") as f:
                rows = json.load(f)
        except Exception:
            continue

        if not isinstance(rows, list):
            continue

        for sym in rows:
            if not isinstance(sym, dict):
                continue
            file_path = os.path.abspath(sym.get("file", ""))
            name = sym.get("name", "")
            if file_path and name and (file_path, name) not in lookup:
                lookup[(file_path, name)] = sym

    return lookup


def _position_from_symbol(symbol: Dict[str, Any]) -> Optional[Tuple[int, int]]:
    """Convert a symbol record to a 0-based (line, character) position."""
    name_pos = symbol.get("name_pos")
    if isinstance(name_pos, list) and len(name_pos) >= 2:
        try:
            line = max(int(name_pos[0]) - 1, 0)
            character = max(int(name_pos[1]), 0)
            return line, character
        except Exception:
            pass

    line_range = symbol.get("range") or {}
    start_line = line_range.get("start_line")
    if isinstance(start_line, int) and start_line > 0:
        return start_line - 1, 0

    return None


def _find_function_on_lines(lines: List[str], function_name: str, candidate_lines: List[int]) -> Optional[Tuple[int, int]]:
    """Try to locate the function name on a small set of candidate lines first."""
    for line_idx in candidate_lines:
        if not (0 <= line_idx < len(lines)):
            continue
        char_idx = lines[line_idx].find(function_name)
        if char_idx >= 0:
            return line_idx, char_idx
    return None


def get_function_position(filepath: str, function_name: str) -> Optional[Tuple[int, int]]:
    """
    Finds the 0-based line and character (column) of a function definition in a file.
    Preference order:
      1) exact coordinates from `symbols_index.json` (name_pos)
      2) symbol range line from `symbols_index.json`
      3) fallback regex scan of the source file
    """
    abs_filepath = os.path.abspath(filepath)

    # 1) Prefer the precise position stored in symbols_index.json
    symbol = _load_symbol_lookup().get((abs_filepath, function_name))
    if symbol:
        precise_pos = _position_from_symbol(symbol)
        if precise_pos is not None:
            line_idx, char_idx = precise_pos
            try:
                with open(abs_filepath, "r", encoding="utf-8", errors="ignore") as f:
                    lines = f.readlines()
            except Exception as e:
                print(f"Error reading {filepath}: {e}")
                return None

            # If we have an exact line/char from the index, trust it.
            if 0 <= line_idx < len(lines):
                return line_idx, char_idx

    # 2) If the index exists but only gives a range line, try to find the name on that line.
    try:
        with open(abs_filepath, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
    except Exception as e:
        print(f"Error reading {filepath}: {e}")
        return None

    if symbol:
        line_range = symbol.get("range") or {}
        start_line = line_range.get("start_line")
        if isinstance(start_line, int) and start_line > 0:
            exact = _find_function_on_lines(lines, function_name, [start_line - 1, start_line - 2, start_line, start_line + 1])
            if exact:
                return exact

    # 3) Fallback regex to find a method definition-like signature.
    pattern = re.compile(r'\b' + re.escape(function_name) + r'\s*\(')
    for line_idx, line in enumerate(lines):
        match = pattern.search(line)
        if match:
            return line_idx, match.start()

    # 4) Final fallback: exact word anywhere in the file.
    word_pattern = re.compile(r'\b' + re.escape(function_name) + r'\b')
    for line_idx, line in enumerate(lines):
        match = word_pattern.search(line)
        if match:
            return line_idx, match.start()

    return None
