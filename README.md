# Code Indexer (LSP-first, AST fallback)

给定项目路径，生成：

- `symbols_index.json`
- `call_graph.json`
- `dependency_graph.json`

## 特性

- 优先使用 LSP 思路（当前版本预留了 LSP 客户端扩展空间）
- LSP 信息不足时使用 AST 兜底
  - Python: 内置 `ast`
  - JS/TS: `tree-sitter`
- 支持多文件、跨文件符号关联（基于符号名匹配）
- 分批处理文件，避免一次性加载

## 安装

### Python 依赖

```bash
python3 -m pip install -r requirements.txt
```

### 系统依赖 (LSP 支持)

如果你需要分析 Java 项目代码的调用链，请确保本地环境中安装了 JDT.LS (Java Language Server)：

- **macOS (推荐通过 Homebrew 安装)**:
  ```bash
  brew install jdtls
  ```
- **其他系统**:
  请参考 [eclipse.jdt.ls](https://github.com/eclipse/eclipse.jdt.ls) 官方页面进行下载，并将 `jdtls` 可执行文件添加到环境变量中。

## 运行

```bash
python3 code_parser.py /path/to/project --output /path/to/output
```

输出文件：
- `/path/to/output/symbols_index.json`
- `/path/to/output/call_graph.json`
- `/path/to/output/dependency_graph.json`

## 数据结构 (Schema)

### 代码元素 (symbols_index.json)
解析出的代码符号表中的每个元素都遵循以下 Schema：

```json
{
  "symbol_id": 1, 
  "name": "YouLaiBootApplication",
  "type": "class",
  "file": "/absolute/path/to/file.java",
  "range": {
    "start_line": 15,
    "end_line": 21
  },
  "signature": "class YouLaiBootApplication", 
  "language": "java",
  "doc": "包含的文档注释内容",
  "container": "com.youlai.boot"
}
```

字段说明：
- `symbol_id`: 符号的全局唯一标识。
- `name`: 符号名称（如类名、方法名、变量名）。
- `type`: 符号类型（如 `class`, `method`, `variable`, `function` 等）。
- `file`: 所属文件的绝对路径。
- `range`: 符号在文件中所在的起始行和结束行（1-based）。
- `signature`: 具体的签名或者声明文本片段。
- `language`: 所属语言（如 `java`, `python`, `javascript`）。
- `doc`: 提取到的关联文档注释信息（通常为 javadoc/docstring）。
- `container`: 所属容器，例如所在的包路径、类路径或父级作用域名称。
