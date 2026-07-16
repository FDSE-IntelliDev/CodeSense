"""Small normalization helpers shared by query planners."""

from __future__ import annotations

from typing import Any, Iterable, List, Optional


def normalize_property(value: Any) -> str:
    return "exclude" if str(value or "").strip().lower() == "exclude" else "include"


def normalize_optional_string(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"none", "null"}:
        return None
    return text


def normalize_string_list(value: Any) -> List[str]:
    if value is None:
        return []
    values: Iterable[Any]
    if isinstance(value, (list, tuple, set)):
        values = value
    else:
        values = (value,)

    result: List[str] = []
    seen = set()
    for item in values:
        text = normalize_optional_string(item)
        if text is None:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


def extend_unique(target: List[str], values: Iterable[str]) -> None:
    seen = {item.casefold() for item in target}
    for value in values:
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        target.append(value)
