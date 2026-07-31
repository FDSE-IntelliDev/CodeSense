"""CodeSense —— 基于语义查询语言（SemQL）的代码搜索系统。

包内分两段（详见仓库根目录 ARCHITECTURE.md）：

    离线索引  indexing/ + codeql/ + parsers/  源码 → 符号表 / 调用图 / 倒排索引
    在线查询  query/ → executors/ → filters/  自然语言 → SemCon → SemQL → 候选集

配置统一从 ``codesense.config`` 取，不要在模块里写死路径或超参。
"""

__version__ = "0.1.0"
