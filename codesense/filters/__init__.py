"""候选集过滤。

接口与注册表在 :mod:`codesense.filters.base`，实现分散在各自的模块里。

下面那行 import 是**必须的**：注册装饰器只有在实现模块被加载时才执行，
漏了它 ``RELATION_FILTERS.create("caller")`` 会报「未知的 relation_filter」，
而代码看起来完全正常。这是这套模式唯一的坑。
"""

from codesense.filters import relation_filters  # noqa: F401  触发注册，别删
from codesense.filters.base import RELATION_FILTERS, RelationFilter

__all__ = ["RELATION_FILTERS", "RelationFilter"]
