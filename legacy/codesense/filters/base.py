"""过滤器的抽象接口。

这是 filters 层的「契约层」：先在这里把接口定清楚，再去写实现。

    候选集  ──Filter──>  更小的候选集

新增一种过滤方式 = 继承对应的 ABC + 注册。不要绕过接口直接在 executor 里
写具体逻辑，也不要为了选实现而在 executor 里加 if/elif。

实现必须满足的约定写在各 ABC 的 docstring 里，并由 tests/contract/ 对
**所有注册实现**自动检查——加一条组内约定就往那里加一条测试，别只写进文档。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any

from codesense.registry import Registry

#: 候选代码元素。当前仍是 dict（历史产物的形状），
#: 迁到 frozen dataclass 是下一步的事，见 ARCHITECTURE.md 待办。
Candidate = dict[str, Any]


class RelationFilter(ABC):
    """按结构关系约束收窄候选集。纯逻辑，不碰 IO。

    实现必须满足（见 tests/contract/test_filters.py，所有注册的实现都会被自动检查）：

    1. ``name`` 非空，且与注册名一致；
    2. ``apply`` 返回的是输入的**子集**，不新增元素、不改元素内容；
    3. 同样的输入调两次结果相同（确定性）；
    4. 不修改传入的 candidates（原地改会让上游拿到被篡改的数据）；
    5. 空输入返回空列表，不返回 None、不抛异常。

    所有外部依赖（代码图库、符号表）从**构造函数**传进来，不要在
    ``apply`` 里现去打开——那样测试就必须准备真实产物目录。
    """

    name: str = ""

    @abstractmethod
    def apply(self, candidates: Sequence[Candidate]) -> list[Candidate]:
        """收窄候选集。没有命中就返回空列表，不要返回 None。"""


#: 关系过滤器注册表。实现文件通过装饰器往里登记。
RELATION_FILTERS: Registry[RelationFilter] = Registry("relation_filter")
