"""Index fields and their weights.

Where a hit lands matters: `buffer` in a symbol name and `buffer` in a
javadoc are evidence of very different strength. Fields are also where the
`structural` and `semantic` satisfiers attach.

Design: ``docs/design/09-grounding.md``, section 5.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType

__all__ = ["DEFAULT_FIELD_WEIGHTS", "FieldWeights", "IndexField"]


class IndexField(str, Enum):
    """Where a posting hit lands.

    Subclasses ``str`` so it serialises and compares like one (`UnitHit.field`,
    JSON) while still constraining the set of values.
    """

    NAME = "name"
    SIGNATURE = "signature"
    CONTAINER = "container"
    DOC = "doc"
    ANNOTATION = "annotation"
    ANNOTATION_ARG = "annotation_arg"
    MODIFIER = "modifier"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class FieldWeights:
    """Per-field weights.

    The defaults are the design's starting values, **not calibrated ones** --
    tune them against the evaluation set in
    ``docs/design/08-open-questions.md``.
    """

    name: float = 1.0
    annotation: float = 0.9
    annotation_arg: float = 0.7
    modifier: float = 0.6
    signature: float = 0.6
    container: float = 0.5
    doc: float = 0.3

    def weight(self, field: IndexField | str) -> float:
        """Weight of one field. Unknown fields weigh 0 rather than silently 1."""
        return self.as_mapping().get(str(field), 0.0)

    def as_mapping(self) -> Mapping[str, float]:
        return MappingProxyType(
            {
                IndexField.NAME.value: self.name,
                IndexField.ANNOTATION.value: self.annotation,
                IndexField.ANNOTATION_ARG.value: self.annotation_arg,
                IndexField.MODIFIER.value: self.modifier,
                IndexField.SIGNATURE.value: self.signature,
                IndexField.CONTAINER.value: self.container,
                IndexField.DOC.value: self.doc,
            }
        )


#: Shared defaults. To change them, construct and inject a new `FieldWeights`
#: rather than mutating this one.
DEFAULT_FIELD_WEIGHTS = FieldWeights()
