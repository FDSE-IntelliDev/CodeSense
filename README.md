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

```bash
python3 -m pip install -r requirements.txt
```

## 运行

```bash
python3 code_parser.py /path/to/project --output /path/to/output
```

输出文件：
- `/path/to/output/symbols_index.json`
- `/path/to/output/call_graph.json`
- `/path/to/output/dependency_graph.json`
