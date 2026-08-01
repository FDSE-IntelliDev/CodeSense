"""关系过滤器的三种实现。

它们是「同一件事的不同做法」：都按某个结构关系约束把候选集收窄。
relation executor 在一个 clause 里按需依次施加，因此共用 :class:`RelationFilter`
接口，由注册表选实现——executor 里不该再出现任何一个具体类名。

外部依赖（代码图库）从构造函数传进来，`apply()` 只做纯计算，也不负责
打开或关闭图库——谁开谁关，那是边界层的事。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any, Optional, Tuple

from codesense.filters.base import RELATION_FILTERS, Candidate, RelationFilter
from codesense.filters.relation_filter import (
    DEFAULT_WORKER_COUNT,
    apply_call_entries,
    apply_graph_roles,
    normalize_roles,
)
from codesense.filters.relation_graph_store import RelationGraphStore

#: (文件名 | None, 符号名)。文件名是 RelationCon 给的裸文件名，不是绝对路径。
CallEntry = Tuple[Optional[str], str]


@RELATION_FILTERS.register("graph_role")
class GraphRoleFilter(RelationFilter):
    """按调用图角色过滤：entry_point / leaf / isolate。

    非可调用符号（类、字段等）没有调用图角色。评估 include 约束时它们
    保留，评估 exclude 约束时不能算作命中——由 ``preserve_non_applicable`` 控制。
    """

    name = "graph_role"

    def __init__(
        self,
        roles: Iterable[Any] = (),
        *,
        graph_store: Optional[RelationGraphStore] = None,
        preserve_non_applicable: bool = True,
    ) -> None:
        self.roles = normalize_roles(roles)
        self.graph_store = graph_store
        self.preserve_non_applicable = preserve_non_applicable

    def apply(self, candidates: Sequence[Candidate]) -> list[Candidate]:
        return apply_graph_roles(
            list(candidates),
            self.roles,
            self.graph_store,
            self.preserve_non_applicable,
        )


class _CallEntryFilter(RelationFilter):
    """caller / callee 的共同实现。

    两者除了方向没有任何区别，所以走同一段逻辑，由 :attr:`relation_kind`
    区分——这正好替掉了原来 ``if relation_kind == "caller" / elif "callee"``
    那个分支。
    """

    relation_kind: str = ""

    def __init__(
        self,
        entries: Iterable[CallEntry] = (),
        *,
        graph_store: Optional[RelationGraphStore] = None,
        layer: Optional[int] = 1,
        worker_count: int = DEFAULT_WORKER_COUNT,
        preserve_on_empty_fallback: bool = True,
    ) -> None:
        self.entries = list(entries)
        self.graph_store = graph_store
        self.layer = layer
        self.worker_count = worker_count
        self.preserve_on_empty_fallback = preserve_on_empty_fallback

    def apply(self, candidates: Sequence[Candidate]) -> list[Candidate]:
        return apply_call_entries(
            candidates=list(candidates),
            relation_entries=self.entries,
            relation_kind=self.relation_kind,
            graph_store=self.graph_store,
            layer=self.layer,
            worker_count=self.worker_count,
            preserve_on_empty_fallback=self.preserve_on_empty_fallback,
        )


@RELATION_FILTERS.register("caller")
class CallerFilter(_CallEntryFilter):
    """只保留被指定 caller 调用到的候选。"""

    name = "caller"
    relation_kind = "caller"


@RELATION_FILTERS.register("callee")
class CalleeFilter(_CallEntryFilter):
    """只保留调用了指定 callee 的候选。"""

    name = "callee"
    relation_kind = "callee"
