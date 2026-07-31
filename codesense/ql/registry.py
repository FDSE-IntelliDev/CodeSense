"""按名字注册可替换实现的注册表。

用途是消掉 ``if kind == "a": ... elif kind == "b": ...`` 这类分发链——
加一种实现只该动一处（注册），不该改分发代码。
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from typing import Generic, TypeVar

__all__ = ["Registry"]

_T = TypeVar("_T")


class Registry(Generic[_T]):
    """名字 → 实现。

    重复注册直接报错而不是静默覆盖：同名两份实现几乎总是 bug，
    静默覆盖会让「为什么用的不是我写的那个」变成一个很难查的问题。
    """

    def __init__(self, what: str) -> None:
        self._what = what
        self._items: dict[str, _T] = {}

    def register(self, name: str, item: _T) -> _T:
        if name in self._items:
            raise ValueError(f"{self._what} 已注册过同名实现: {name!r}")
        self._items[name] = item
        return item

    def decorator(self, name: str) -> Callable[[_T], _T]:
        """当装饰器用：``@REGISTRY.decorator("noisy_or")``。"""

        def wrap(item: _T) -> _T:
            self.register(name, item)
            return item

        return wrap

    def get(self, name: str) -> _T:
        try:
            return self._items[name]
        except KeyError:
            known = ", ".join(sorted(self._items)) or "（空）"
            raise KeyError(f"未知的{self._what}: {name!r}。已注册: {known}") from None

    def names(self) -> Mapping[str, _T]:
        return dict(self._items)

    def __contains__(self, name: object) -> bool:
        return name in self._items

    def __iter__(self) -> Iterator[str]:
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)
