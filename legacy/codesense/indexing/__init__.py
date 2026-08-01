"""离线索引构建：源码 → 符号表、调用图、ngram 索引、倒排索引。

``codegraph/`` 子包负责把解析结果落成 SQLite 代码图库；
``codesense.codeql`` 是同一份 schema 的 CodeQL 实现，用于和 LSP 结果对比。
"""
