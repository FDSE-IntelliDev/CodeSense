"""从 Java 源码建索引。

    python scripts/build_index.py --source path/to/repo --out index.json

产出 `codesense.ql.store` 认识的四个产物里的三个（`expansion` 由别处建）：

    symbols    符号表
    postings   term → [(symbol_id, field, tf)]，**只存 id**
    edges      目前只有 contains——它是 10 章说的性价比最高的一步，
               不需要 CodeQL，实测能把图上的孤点从大多数降到极少数

这是**胶水脚本**：遍历、切词、写盘。功能逻辑在 `codesense.indexing`
和 `codesense.ql` 里，这里只负责把外部数据搬成它们认识的形状。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from codesense.indexing import Declaration, JavaDeclarationScanner, posting_terms
from codesense.indexing.java import modifier_terms

#: 跳过测试、生成代码与示例——它们会稀释 ICF，而且几乎不是查询的目标。
SKIP_PARTS = ("/test/", "/tests/", "/generated/", "/target/", "/build/", "/example/")

#: 只索引这些种类。参数（`parameter`）数量巨大且几乎不单独被查，
#: 索引它们只会让 posting 膨胀一倍。
KEEP_KINDS = frozenset(
    {"class", "interface", "enum", "record", "annotation_type", "method", "constructor", "field"}
)

#: doc 里保留的最多词数。javadoc 可以很长，全收会让常见词的 df 爆掉。
DOC_TERM_LIMIT = 40

_WORD = re.compile(r"[A-Za-z][A-Za-z0-9]*")


@dataclass
class Stats:
    files: int = 0
    failed: int = 0
    symbols: int = 0
    annotations: int = 0
    postings: int = 0
    edges: int = 0


def split_identifier(name: str) -> list[str]:
    """切标识符。优先用项目依赖的 srctoolkit（底层 Ronin）。"""
    try:
        from srctoolkit.delimiter import Delimiter

        return [t for t in Delimiter.split_camel(name).split() if t.isalpha()]
    except ImportError:
        text = re.sub(r"[^A-Za-z]+", " ", name)
        text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", text)
        return [t.lower() for t in text.split() if t]


def java_files(root: Path) -> Iterator[Path]:
    for path in sorted(root.rglob("*.java")):
        text = str(path).replace("\\", "/")
        if not any(part in text for part in SKIP_PARTS):
            yield path


def declaration_terms(declaration: Declaration) -> list[tuple[str, str]]:
    """一个声明产生的全部 (term, field) 对。

    每个域分开，因为**命中在哪个字段不一样重**：符号名里出现 `buffer`
    和 javadoc 里出现 `buffer` 是完全不同强度的证据。
    """
    terms: list[tuple[str, str]] = []
    terms += [(t, "name") for t in split_identifier(declaration.name)]
    terms += [(t, "container") for t in split_identifier(declaration.container)]
    terms += [(t, "signature") for t in split_identifier(declaration.signature)]
    terms += [(w.lower(), "doc") for w in _WORD.findall(declaration.doc)[:DOC_TERM_LIMIT]]
    terms += list(modifier_terms(declaration))
    for use in declaration.annotations:
        terms += posting_terms(use, split_identifier)
    return terms


def build(root: Path, stats: Stats) -> dict[str, Any]:
    scanner = JavaDeclarationScanner.for_java()
    symbols: list[dict[str, Any]] = []
    postings: dict[str, dict[tuple[int, str], int]] = defaultdict(lambda: defaultdict(int))
    by_qualified: dict[str, int] = {}
    containers: list[tuple[int, str]] = []

    for path in java_files(root):
        stats.files += 1
        try:
            declarations = scanner.scan(path.read_text(encoding="utf-8", errors="replace"))
        except Exception as exc:  # noqa: BLE001 —— 单个文件坏掉不该中断整次建库
            stats.failed += 1
            print(f"  跳过 {path}: {exc}", file=sys.stderr)
            continue

        for declaration in declarations:
            if declaration.kind not in KEEP_KINDS or not declaration.name:
                continue
            symbol_id = len(symbols) + 1
            symbols.append(
                {
                    "symbol_id": symbol_id,
                    "name": declaration.name,
                    "kind": declaration.kind,
                    "file": str(path.relative_to(root)),
                    "span": [declaration.line, declaration.end_line],
                    "signature": declaration.signature,
                    "container": declaration.container,
                    "doc": declaration.doc[:400],
                    "language": "java",
                    "modifiers": sorted(declaration.modifiers),
                }
            )
            stats.annotations += len(declaration.annotations)
            for term, field in declaration_terms(declaration):
                postings[term][(symbol_id, field)] += 1
            if declaration.container:
                containers.append((symbol_id, declaration.container))
            if declaration.kind in ("class", "interface", "enum", "record", "annotation_type"):
                qualified = (
                    f"{declaration.container}.{declaration.name}"
                    if declaration.container
                    else declaration.name
                )
                by_qualified.setdefault(qualified, symbol_id)

    stats.symbols = len(symbols)
    edges = [
        {
            "source_id": by_qualified[container],
            "target_id": symbol_id,
            "kind": "contains",
            "confidence": 1.0,
            "provenance": "derived_container",
        }
        for symbol_id, container in containers
        if container in by_qualified and by_qualified[container] != symbol_id
    ]
    stats.edges = len(edges)
    flat = {
        term: [
            {"symbol_id": sid, "field": field, "tf": tf}
            for (sid, field), tf in sorted(entries.items())
        ]
        for term, entries in sorted(postings.items())
    }
    stats.postings = sum(len(v) for v in flat.values())
    return {"symbols": symbols, "postings": flat, "edges": edges}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="Java 代码库根目录")
    parser.add_argument("--out", type=Path, required=True, help="索引输出路径")
    args = parser.parse_args(argv)

    if not args.source.is_dir():
        parser.error(f"{args.source} 不是目录")
    stats = Stats()
    payload = build(args.source, stats)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    size = args.out.stat().st_size / 1024 / 1024
    print(
        f"{args.source.name}: {stats.files} 文件 → {stats.symbols} 符号, "
        f"{len(payload['postings'])} term, {stats.postings} posting, "
        f"{stats.edges} contains 边, {stats.annotations} 处注解  [{size:.1f} MB]"
    )
    if stats.failed:
        print(f"  {stats.failed} 个文件解析失败")
    return 0


if __name__ == "__main__":
    sys.exit(main())
