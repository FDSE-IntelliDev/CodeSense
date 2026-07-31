"""A registry mapping names to swappable implementations.

It exists to remove ``if kind == "a": ... elif kind == "b": ...`` dispatch
chains: adding an implementation should touch one place (the registration),
not the dispatch code.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from typing import Generic, TypeVar

__all__ = ["Registry"]

_T = TypeVar("_T")


class Registry(Generic[_T]):
    """Name to implementation.

    A duplicate registration raises rather than silently overwriting. Two
    implementations under one name is nearly always a bug, and overwriting
    quietly turns "why is mine not being used" into a hard thing to trace.
    """

    def __init__(self, what: str) -> None:
        self._what = what
        self._items: dict[str, _T] = {}

    def register(self, name: str, item: _T) -> _T:
        if name in self._items:
            raise ValueError(f"{self._what} already has an implementation named {name!r}")
        self._items[name] = item
        return item

    def decorator(self, name: str) -> Callable[[_T], _T]:
        """Use as a decorator: ``@REGISTRY.decorator("noisy_or")``."""

        def wrap(item: _T) -> _T:
            self.register(name, item)
            return item

        return wrap

    def get(self, name: str) -> _T:
        try:
            return self._items[name]
        except KeyError:
            known = ", ".join(sorted(self._items)) or "(none)"
            raise KeyError(f"unknown {self._what}: {name!r}; registered: {known}") from None

    def names(self) -> Mapping[str, _T]:
        return dict(self._items)

    def __contains__(self, name: object) -> bool:
        return name in self._items

    def __iter__(self) -> Iterator[str]:
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)
