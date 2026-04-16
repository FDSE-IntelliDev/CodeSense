# Query Processing

This package extracts ranked search keywords from natural-language queries before retrieval.

## Features

- Model-only extraction (no rule fallback)
- Default `KeyBERT` mode
- Optional `LLM` mode and `hybrid` score fusion
- Output format compatible with downstream retrieval

## Output Format

```json
[
  {"keyword": "read ahead", "score": 0.92},
  {"keyword": "disk", "score": 0.65},
  {"keyword": "function", "score": 0.10}
]
```

## Quick Start

```bash
python -m query_processing.run_keyword_extraction
```
