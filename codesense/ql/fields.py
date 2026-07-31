"""索引域（field）及其权重。

命中在哪个字段不一样重：符号名里出现 `buffer` 和 javadoc 里出现 `buffer`
是完全不同强度的证据。域也是 `structural` / `semantic` satisfier 的落地点。

设计依据见 ``docs/design/09-grounding.md`` 第五节。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType

__all__ = ["DEFAULT_FIELD_WEIGHTS", "FieldWeights", "IndexField"]


class IndexField(str, Enum):
    """倒排表里一条 posting 命中的位置。

    继承 ``str``，所以可以直接当字符串用（`UnitHit.field`、JSON 序列化），
    同时又有枚举的取值约束。
    """

    NAME = "name"
    SIGNATURE = "signature"
    CONTAINER = "container"
    DOC = "doc"
    ANNOTATION = "annotation"
    ANNOTATION_ARG = "annotation_arg"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class FieldWeights:
    """各域的权重。

    默认值是设计文档给的初值，**不是标定过的结果**——
    按 ``docs/design/08-open-questions.md`` 的评测集调。
    """

    name: float = 1.0
    annotation: float = 0.9
    annotation_arg: float = 0.7
    signature: float = 0.6
    container: float = 0.5
    doc: float = 0.3

    def weight(self, field: IndexField | str) -> float:
        """取某个域的权重。未知的域记 0，不静默当成 1。"""
        return self.as_mapping().get(str(field), 0.0)

    def as_mapping(self) -> Mapping[str, float]:
        return MappingProxyType(
            {
                IndexField.NAME.value: self.name,
                IndexField.ANNOTATION.value: self.annotation,
                IndexField.ANNOTATION_ARG.value: self.annotation_arg,
                IndexField.SIGNATURE.value: self.signature,
                IndexField.CONTAINER.value: self.container,
                IndexField.DOC.value: self.doc,
            }
        )


#: 共享的默认权重。要改就构造新的 `FieldWeights` 注入，别改这个。
DEFAULT_FIELD_WEIGHTS = FieldWeights()
