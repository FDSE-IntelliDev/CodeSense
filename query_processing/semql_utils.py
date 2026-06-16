"""Helpers for reading SemQL fields from old and grouped schemas."""

from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Union


FieldSelector = Optional[Union[str, Sequence[str]]]


def _as_tuple(value: FieldSelector, default: Sequence[str]) -> Sequence[str]:
    if value is None:
        return tuple(default)
    if isinstance(value, str):
        return (value,)
    return tuple(value)


def _append_unique(out: List[Any], seen: Set[str], value: Any) -> None:
    if value is None:
        return
    key = repr(value)
    if key in seen:
        return
    seen.add(key)
    out.append(value)


def _flatten_terms(value: Any) -> Iterable[Any]:
    if value is None:
        return
    if isinstance(value, list):
        for item in value:
            yield from _flatten_terms(item)
        return
    if isinstance(value, dict):
        for key in ("term", "keywords", "synonyms", "concept", "relation", "action", "object"):
            if key in value:
                yield from _flatten_terms(value.get(key))
        return
    yield value


def _extract_named_value(condition: Dict[str, Any], term_name: str) -> Iterable[Any]:
    value = condition.get(term_name)
    if term_name == "intent":
        yield from _flatten_terms(value)
    elif isinstance(value, list):
        for item in value:
            yield item
    elif value is not None:
        yield value


def extract_semql_terms(
    keyword_payload: Dict[str, Any],
    properties: FieldSelector = ("include",),
    condition_type: FieldSelector = None,
    term_name: FieldSelector = None,
) -> List[Any]:
    """
    Extract field values from SemQL.

    - properties selects include/exclude groups.
    - condition_type selects surface/intention/relation groups.
    - term_name selects the field name inside each condition. When omitted,
      common keyword-like fields are extracted.

    The helper also falls back to legacy/top-level fields with the same
    term_name so older DSL payloads and exact_code payloads can share this API.
    """
    if not isinstance(keyword_payload, dict):
        return []

    properties_tuple = _as_tuple(properties, ("include",))
    condition_types = _as_tuple(condition_type, ("surface", "intention", "relation"))
    term_names = _as_tuple(term_name, ("keywords", "synonyms", "term"))

    out: List[Any] = []
    seen: Set[str] = set()

    conditions = keyword_payload.get("conditions")
    if isinstance(conditions, dict):
        for ctype in condition_types:
            condition_group = conditions.get(ctype, {})
            if not isinstance(condition_group, dict):
                continue
            for prop in properties_tuple:
                items = condition_group.get(prop, [])
                if not isinstance(items, list):
                    continue
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    for name in term_names:
                        for value in _extract_named_value(item, name):
                            _append_unique(out, seen, value)

    for name in term_names:
        if name not in keyword_payload:
            continue
        value = keyword_payload.get(name)
        if name in {"keywords", "filters", "intent"}:
            values = _flatten_terms(value)
        elif isinstance(value, list):
            values = value
        else:
            values = (value,)
        for item in values:
            _append_unique(out, seen, item)

    return out


def extract_semql_text_terms(
    keyword_payload: Dict[str, Any],
    properties: FieldSelector = ("include",),
    condition_type: FieldSelector = None,
    term_name: FieldSelector = None,
) -> List[str]:
    """Extract SemQL terms and coerce them to non-empty strings."""
    terms = extract_semql_terms(
        keyword_payload,
        properties=properties,
        condition_type=condition_type,
        term_name=term_name,
    )
    return [str(term).strip() for term in terms if str(term).strip()]
