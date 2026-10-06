"""Annotate whether answer files and functions exist in the pinned revision.

Trace answers come from the fix diff and the agent's final message, so they
reference symbols the fix *adds* -- e.g. ``unquote`` -- which are absent from
the ``base_commit`` tree the benchmark is retrieved against. Such phantom
functions can never be hit, so counting them in ``function_recall`` unfairly
caps the metric. This module preserves the mined answer and records existence
separately so evaluation can exclude impossible gold without losing provenance.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from codesense.lang.java.scanner import JavaDeclarationScanner

__all__ = [
    "clean_record",
    "declared_functions",
    "normalize_function_name",
]

#: Answer fields whose files and ``functions`` are annotated.
_ANSWER_FIELDS = ("answer", "trace_answer", "candidate_answers")
#: Declaration kinds that count as a callable a query could name.
_CALLABLE_KINDS = frozenset({"method", "constructor"})


def normalize_function_name(value: object) -> str:
    """Reduce a function reference to its simple name.

    Mirrors ``scripts/evaluation.py::_function_name`` so cleaned answers and
    scored hits compare on the same key: drop parameters, then any qualifier.
    """
    return str(value or "").strip().split("(", 1)[0].rsplit(".", 1)[-1]


def declared_functions(source_text: str) -> set[str]:
    """Normalized names of methods and constructors declared in one Java file."""
    result = JavaDeclarationScanner.for_java().scan(source_text)
    return {
        normalize_function_name(declaration.name)
        for declaration in result.declarations
        if declaration.kind in _CALLABLE_KINDS and declaration.name
    }


def clean_record(record: Mapping[str, object], repo_root: Path) -> dict[str, object]:
    """Return a copy annotated with per-file and per-function existence.

    Original file paths and function lists remain untouched. ``file_exist`` is
    false unless the path names a file in ``repo_root``; ``function_exist`` maps
    each original function spelling to whether the Java scanner found its
    normalized callable name in that file.
    """
    cleaned = dict(record)
    for field_name in _ANSWER_FIELDS:
        locations = cleaned.get(field_name)
        if not isinstance(locations, list):
            continue
        cleaned[field_name] = [_clean_location(loc, repo_root) for loc in locations]
    return cleaned


def _clean_location(location: object, repo_root: Path) -> object:
    if not isinstance(location, Mapping):
        return location
    file = str(location.get("file") or "")
    functions = location.get("functions")
    function_values = functions if isinstance(functions, list) else []
    path = repo_root / file
    file_exists = bool(file) and path.is_file()
    source = _read_source(path) if file_exists else None
    declared = declared_functions(source) if source is not None else set()
    return {
        **dict(location),
        "file_exist": file_exists,
        "function_exist": {
            str(function): normalize_function_name(function) in declared
            for function in function_values
        },
    }


def _read_source(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
