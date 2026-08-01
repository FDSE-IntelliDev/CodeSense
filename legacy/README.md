# legacy —— 重写前的实现（只读归档）

这里是 SemCon → SemQL → 三执行器那套实现。**已冻结，不再维护。**

现在的实现是 `codesense/ql/`，按 `docs/design/` 从头写的，
不复用这里的执行层代码——隔离由 `tests/contract/test_ql_isolation.py` 机械强制。

## 为什么留着而不是删掉

git 里当然还有（tag `pre-ql-rewrite`、分支 `archive/legacy-implementation`），
留在工作区里是因为有三样东西还要用：

| 位置 | 用途 |
|---|---|
| `codesense/codeql/queries/java/*.ql` | 会按 [10 章](../docs/design/10-graph.md)扩展：补 `getAPossibleImplementation`、字段读写、注解 |
| `codesense/parsers/` | tree-sitter 的注解抽取要在这里加（[09 章第八节](../docs/design/09-grounding.md)） |
| `tests/` 里的 golden 产物 | 新旧行为对照的基准 |

## 状态

- **不打包**：`pyproject.toml` 只 include `codesense*`，而本目录不在其下
- **不 lint、不格式化**：`ruff` 的 `extend-exclude` 里
- **不跑测试**：`pytest` 的 `testpaths` 只有 `tests/`

要跑这里的测试得显式指定路径，且需要 conda 环境装齐
tree-sitter / gensim / spacy / javalang。

## 别在这里改代码

需要某个能力就照设计文档在 `codesense/ql/` 里重新实现。
在这里打补丁只会让归档变成第二套活代码。
