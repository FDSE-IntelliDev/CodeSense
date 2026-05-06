# Embedding Dataset Scaffold

This directory contains dataset-building utilities for project-specific code search embedding training.

## Output Files

After running the builder, `embedding/data/` will contain:

- `documents.jsonl`: code-element documents for indexing/training
- `train_pairs.jsonl`: contrastive query-positive-negative training pairs
- `manifest.json`: basic dataset statistics

## Data Schema

### documents.jsonl

Each line is a JSON object:

```json
{
  "symbol_id": "src/a.py::func::10-30",
  "name": "func",
  "type": "function",
  "language": "python",
  "file": "src/a.py",
  "container": "module_or_class",
  "signature": "def func(x):",
  "doc": "description",
  "start_line": 10,
  "end_line": 30,
  "neighbors": {
    "callees": ["helper"],
    "callers": ["main"]
  },
  "deps": ["src/utils.py"],
  "text": "flattened text for embedding model input"
}
```

### train_pairs.jsonl

Each line is a JSON object:

```json
{
  "query_id": "q_000001_0",
  "query_text": "find function parse token",
  "positive_symbol_id": "src/auth.py::parse_token::8-42",
  "negative_symbol_ids": [
    "src/auth.py::verify_token::44-80",
    "src/http.py::parse_headers::5-27"
  ],
  "meta": {
    "source": "template",
    "hardness": "mixed"
  }
}
```

## Build Dataset

```bash
python -m embedding.build_dataset \
  --project-root /Users/huangzhuochen/PycharmProjects/CodeSearch \
  --symbols output/youlai-boot-master-gt/symbols_index.json \
  --calls output/youlai-boot-master-gt/call_graph.json \
  --deps output/youlai-boot-master-gt/dependency_graph.json \
  --out-dir embedding/data
```

## Validate Dataset

```bash
python -m embedding.validate_dataset --data-dir embedding/data
```
