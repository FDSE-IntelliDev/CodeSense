"""注册表：把「有哪些实现」和「用哪个实现」解耦。

没有注册表时，选实现要靠一长串 if/elif：

    if name == "todo":
        d = TodoDetector()
    elif name == "long_line":
        d = LongLineDetector()
    ...                          # 每加一个实现就要回来改这里

有了注册表，新增实现只需要在自己的文件里加一行装饰器，
调用方完全不用改。这是本项目最重要的一条约定，
写法与理由见组内 DEV-COOKBOOK 的 ARCHITECTURE.md 规则 4。
本文件直接取自那份手册的参考实现，没有改动，便于对照。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Generic, TypeVar

T = TypeVar("T")


class Registry(Generic[T]):
    """名字 → 类 的映射表。"""

    def __init__(self, kind: str) -> None:
        self._kind = kind
        self._items: dict[str, type[T]] = {}

    def register(self, name: str) -> Callable[[type[T]], type[T]]:
        """装饰器：把一个类登记进来。

        @DETECTORS.register("todo")
        class TodoDetector(Detector):
            ...
        """

        def decorator(cls: type[T]) -> type[T]:
            if name in self._items:
                raise ValueError(
                    f"{self._kind} 名字重复：{name!r} 已被 {self._items[name].__name__} 占用"
                )
            self._items[name] = cls
            return cls

        return decorator

    def create(self, name: str, **kwargs: object) -> T:
        """按名字造一个实例。"""
        if name not in self._items:
            raise KeyError(
                f"未知的 {self._kind}：{name!r}。可选：{', '.join(self.names()) or '（空）'}"
            )
        return self._items[name](**kwargs)  # type: ignore[call-arg]

    def get(self, name: str) -> type[T]:
        if name not in self._items:
            raise KeyError(f"未知的 {self._kind}：{name!r}")
        return self._items[name]

    def names(self) -> list[str]:
        return sorted(self._items)

    def __contains__(self, name: object) -> bool:
        return name in self._items

    def __len__(self) -> int:
        return len(self._items)
