"""索引构建。

与 `codesense.ql` 的分工：这里**建**索引产物，那里**读**。
依赖方向单向——indexing 可以用 ql 的数据类型，ql 不认识 indexing。

这一层允许第三方依赖（tree-sitter 等），`codesense.ql` 不允许，
所以解析类的东西都放这边。
"""

from codesense.indexing.expansion import build_expansion_table
from codesense.indexing.annotations import AnnotationUse, JavaAnnotationExtractor, arg_tokens
from codesense.indexing.meta_annotations import (
    META_ANNOTATIONS,
    expansions_for,
    meta_expansion_table,
)

__all__ = [
    "META_ANNOTATIONS",
    "AnnotationUse",
    "JavaAnnotationExtractor",
    "arg_tokens",
    "build_expansion_table",
    "expansions_for",
    "meta_expansion_table",
]
