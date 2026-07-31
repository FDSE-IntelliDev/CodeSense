"""从 Java 源码建索引。

    python scripts/build_index.py --source path/to/repo --out index.json

产出 `codesense.ql.store` 认识的四个产物里的三个（`expansion` 由别处建）：

    symbols    符号表
    postings   term → [(symbol_id, field, tf)]，**只存 id**
    edges      contains（从容器字段推出，置信度 1.0）
               + calls（按名字匹配，**置信度低**，见下）

`calls` 分两档解析：

    类型感知   this.m() / field.m() / local.m() / Type.m()
               —— 接收者的声明类型就在 AST 里，不需要类型检查器
    名字兜底   接收者是表达式（链式调用等）时退化成按名字匹配

CodeQL 当然更准，但它要跑完整构建（netty 是 `mvn compile`）。
先把 AST 里免费的类型信息用足，实测能把唯一解析率从 13% 拉到高得多的水平。
虚方法分派、泛型、跨库调用仍然处理不了——那些确实要 CodeQL。

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
from dataclasses import dataclass, field
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

#: 一个被调名字最多允许对应几个候选。`get` 这类名字在大项目里能对上几百个，
#: 全连上只会把图变成噪音——**宁可漏一条边，不要连出一张假图**。
MAX_CALL_CANDIDATES = 8

#: 按名字匹配出来的调用边的置信度上限。它不是真调用图，
#: 所以永远低于 `contains`（1.0）。歧义越多越低。
NAME_CALL_CONFIDENCE = 0.6

#: 接收者类型解析出来的调用边。仍不到 1.0——虚方法分派没处理。
TYPED_CALL_CONFIDENCE = 0.9

_WORD = re.compile(r"[A-Za-z][A-Za-z0-9]*")


@dataclass
class TypeTable:
    """项目内的类型信息，供接收者解析用。"""

    fields: dict[str, dict[str, str]] = field(default_factory=lambda: defaultdict(dict))
    methods: dict[str, dict[str, list[int]]] = field(
        default_factory=lambda: defaultdict(lambda: defaultdict(list))
    )
    supertypes: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def lookup(self, type_name: str, method: str, depth: int = 3) -> list[int]:
        """在类型及其父类型里找方法。"""
        simple = type_name.split("<")[0].split(".")[-1].strip()
        seen: set[str] = set()
        queue = [(simple, depth)]
        while queue:
            current, left = queue.pop(0)
            if current in seen or left < 0:
                continue
            seen.add(current)
            found = self.methods.get(current, {}).get(method)
            if found:
                return found
            queue.extend((parent, left - 1) for parent in self.supertypes.get(current, ()))
        return []


@dataclass
class Stats:
    files: int = 0
    failed: int = 0
    symbols: int = 0
    annotations: int = 0
    postings: int = 0
    edges: int = 0
    contains: int = 0
    calls: int = 0
    typed: int = 0
    ambiguous: int = 0
    unresolved: int = 0


def _receiver_type(
    receiver: str, scope: dict[str, str], owner: str, table: TypeTable
) -> str | None:
    """把接收者表达式解析成类型名。解析不了返回 None。"""
    if not receiver or receiver == "this":
        return owner
    if not receiver.isidentifier():
        return None  # 链式调用等表达式，没有类型检查器就认不出来
    declared = scope.get(receiver) or table.fields.get(owner, {}).get(receiver)
    if declared:
        return declared
    # `Foo.bar()` 这种静态调用，接收者本身就是类型名
    return receiver if receiver[:1].isupper() else None


def _call_edges(
    invocations: Sequence[tuple[int, str, dict[str, str], Sequence[Any]]],
    by_name: dict[str, list[int]],
    table: TypeTable,
    stats: Stats,
) -> list[dict[str, Any]]:
    """两档解析：能定到类型的走类型，定不到的退回名字匹配。"""
    edges: list[dict[str, Any]] = []
    for caller, owner, scope, calls in invocations:
        for call in calls:
            type_name = _receiver_type(call.receiver, scope, owner, table)
            targets = table.lookup(type_name, call.name) if type_name else []
            provenance, ceiling = "typed_receiver", TYPED_CALL_CONFIDENCE
            if not targets:
                targets = by_name.get(call.name, [])
                provenance, ceiling = "name_match", NAME_CALL_CONFIDENCE
                if not targets:
                    stats.unresolved += 1
                    continue
                if len(targets) > MAX_CALL_CANDIDATES:
                    stats.ambiguous += 1
                    continue
            else:
                stats.typed += 1
            confidence = ceiling / len(targets)
            edges.extend(
                {
                    "source_id": caller,
                    "target_id": target,
                    "kind": "calls",
                    "confidence": round(confidence, 4),
                    "provenance": provenance,
                }
                for target in targets
                if target != caller
            )
    stats.calls = len(edges)
    return edges


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
    by_simple_name: dict[str, list[int]] = defaultdict(list)
    containers: list[tuple[int, str]] = []
    invocations: list[tuple[int, str, dict[str, str], Sequence[Any]]] = []
    table = TypeTable()

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
            for term, index_field in declaration_terms(declaration):
                postings[term][(symbol_id, index_field)] += 1
            owner = declaration.container.split(".")[-1]
            if declaration.kind in ("method", "constructor"):
                by_simple_name[declaration.name].append(symbol_id)
                table.methods[owner][declaration.name].append(symbol_id)
                if declaration.calls:
                    invocations.append(
                        (symbol_id, owner, dict(declaration.local_types), declaration.calls)
                    )
            elif declaration.kind == "field" and declaration.signature:
                table.fields[owner][declaration.name] = declaration.signature
            if declaration.container:
                containers.append((symbol_id, declaration.container))
            if declaration.kind in ("class", "interface", "enum", "record", "annotation_type"):
                if declaration.supertypes:
                    table.supertypes[declaration.name] = declaration.supertypes
                qualified = (
                    f"{declaration.container}.{declaration.name}"
                    if declaration.container
                    else declaration.name
                )
                by_qualified.setdefault(qualified, symbol_id)

    stats.symbols = len(symbols)
    edges: list[dict[str, Any]] = [
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
    stats.contains = len(edges)
    edges += _call_edges(invocations, by_simple_name, table, stats)
    stats.edges = len(edges)
    flat = {
        term: [
            {"symbol_id": sid, "field": index_field, "tf": tf}
            for (sid, index_field), tf in sorted(entries.items())
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
        f"{stats.annotations} 处注解  [{size:.1f} MB]"
    )
    print(
        f"  边: contains {stats.contains}, calls {stats.calls}"
        f"（类型解析 {stats.typed} 处，名字太泛跳过 {stats.ambiguous}，项目外 {stats.unresolved}）"
    )
    if stats.failed:
        print(f"  {stats.failed} 个文件解析失败")
    return 0


if __name__ == "__main__":
    sys.exit(main())
