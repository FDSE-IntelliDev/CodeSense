"""跑一次关键词扩展（Step A 检索 + Step B 扩展）。

脚本只做参数解析和调用，扩展逻辑在 codesense.query.keyword_expansion。
"""

from __future__ import annotations

import argparse
import json

from codesense.config import load_config
from codesense.query.keyword_expansion import ExpansionConfig, KeywordExpander


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--query", default="function that performs security check")
    p.add_argument(
        "--keyword",
        action="append",
        default=None,
        help="种子关键词，可重复给；格式 name:score，如 security:0.91",
    )
    p.add_argument("--seed-top-k", type=int, default=20)
    p.add_argument("--expansion-top-n", type=int, default=10)
    p.add_argument("--temperature", type=float, default=0.0)
    return p


def parse_keyword(spec: str) -> dict:
    name, _, score = spec.partition(":")
    return {"keyword": name, "score": float(score) if score else 1.0}


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    cfg = load_config()
    out_dir = cfg.project_output_dir

    expander = KeywordExpander(
        ExpansionConfig(
            symbols_index_path=str(out_dir / "symbols_index.json"),
            ngram_index_path=str(out_dir / "ngramed_symbol.json"),
            seed_top_k=args.seed_top_k,
            expansion_top_n=args.expansion_top_n,
            model=cfg.llm.model,
            temperature=args.temperature,
        )
    )

    specs = args.keyword or ["security:0.91"]
    base_keywords = [parse_keyword(s) for s in specs]

    out = expander.search_and_expansion(args.query, base_keywords)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
