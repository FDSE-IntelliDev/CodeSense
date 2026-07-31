"""从真实索引产物抽一个小 fixture，给 QL 的手写查询验证用。

阶段 A 的验收是「手写几段 QL，看算子集合够不够」。要让这个练习有意义，
数据必须来自真实项目而不是编造的拓扑——真实数据里的稀疏、命名习惯、
缺失字段，才是算子会真正碰到的东西。

用法::

    python scripts/build_ql_fixture.py \\
        --index output/youlai-boot-master \\
        --out tests/fixtures/ql/mini_index.json

这是**胶水脚本**：读盘、切词、写盘。功能逻辑在 ``codesense.ql`` 里，
这里只负责把外部数据搬成它认识的形状。
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any

#: 抽多少个文件。**必须跨领域抽**，不能只挑一个模块——
#: ICF 是相对于被索引语料算的，只收 user/auth 的文件会让 `user`
#: 出现在 75% 的符号上，ICF 判定它毫无区分度，词法查询直接查空。
#: 真实项目里 `user` 是 125/1718（7%），fixture 的词分布要接近这个。
FILE_SAMPLE = 40

#: 必须包含的文件——它们在调用图上连通性最好，撑得起图类查询。
ANCHOR_FILES = (
    "RedisTokenManager.java",
    "SecurityUtils.java",
    "UserController.java",
    "UserServiceImpl.java",
    "Result.java",
)


def split_identifier(name: str) -> list[str]:
    """切标识符。优先用项目依赖的 srctoolkit（底层 Ronin）。

    Ronin 的频率表挖自 GitHub Java 项目，在 Java 上表现好；
    退化路径只在没装依赖时用，仅供 fixture 构建，不进生产路径。
    """
    try:
        from srctoolkit.delimiter import Delimiter

        return [t for t in Delimiter.split_camel(name).split() if t.isalpha()]
    except ImportError:
        text = re.sub(r"[^A-Za-z]+", " ", name)
        text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", text)
        return [t.lower() for t in text.split() if t]


def load_symbols(index_dir: Path) -> list[dict[str, Any]]:
    """跨领域抽样：锚点文件 + 按符号数排序后等距取样。

    等距取样而不是取前 N——取前 N 会集中在同一类大文件（都是 Service），
    等距能横跨 controller / service / entity / config 各层。
    """
    records = json.loads((index_dir / "symbols_index.json").read_text(encoding="utf-8"))
    by_file: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        by_file[Path(record["file"]).name].append(record)

    ordered = sorted(by_file, key=lambda f: (-len(by_file[f]), f))
    picked = [f for f in ANCHOR_FILES if f in by_file]
    rest = [f for f in ordered if f not in picked]
    step = max(1, len(rest) // max(1, FILE_SAMPLE - len(picked)))
    picked.extend(rest[::step][: FILE_SAMPLE - len(picked)])
    return [r for f in picked for r in by_file[f]]


def build_edges(index_dir: Path, kept: set[int], by_name: dict[str, int]) -> list[dict[str, Any]]:
    """真实 `calls` 边 + 物化出来的 `contains` 边。

    `contains` 是 10 章说的「性价比最高的第一步」：数据本来就在
    ``code_symbols.container`` 里，物化成边之后孤点直接归零。
    """
    edges: list[dict[str, Any]] = []
    with sqlite3.connect(index_dir / "codegraph.sqlite") as conn:
        rows = conn.execute(
            "SELECT source_symbol_id, target_symbol_id, kind, confidence FROM code_edges"
        ).fetchall()
    for src, dst, kind, confidence in rows:
        if src in kept and dst in kept:
            edges.append(
                {
                    "source_id": src,
                    "target_id": dst,
                    "kind": kind or "calls",
                    "confidence": confidence if confidence is not None else 0.8,
                    "provenance": "lsp_call_hierarchy",
                }
            )
    return edges + _contains_edges(kept, by_name)


def _contains_edges(kept: set[int], by_name: dict[str, int]) -> list[dict[str, Any]]:
    return [
        {
            "source_id": by_name[container],
            "target_id": symbol_id,
            "kind": "contains",
            "confidence": 1.0,
            "provenance": "derived_container",
        }
        for symbol_id, container in _containers(kept)
        if container in by_name and by_name[container] != symbol_id
    ]


_CONTAINERS: dict[int, str] = {}


def _containers(kept: set[int]) -> Iterable[tuple[int, str]]:
    return ((sid, c) for sid, c in _CONTAINERS.items() if sid in kept and c)


def build_postings(symbols: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """名字与容器两个域的倒排表。

    只有这两个域：注解没有被解析器抽取（10 章记录的欠账），
    doc 在样例项目里基本为空。**fixture 如实反映现状，不补齐。**
    """
    postings: dict[str, dict[tuple[int, str], int]] = defaultdict(lambda: defaultdict(int))
    for record in symbols:
        for term in split_identifier(record["name"]):
            postings[term][(record["symbol_id"], "name")] += 1
        for term in split_identifier(record.get("container") or ""):
            postings[term][(record["symbol_id"], "container")] += 1
    return {
        term: [
            {"symbol_id": sid, "field": field, "tf": tf}
            for (sid, field), tf in sorted(entries.items())
        ]
        for term, entries in sorted(postings.items())
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, required=True, help="索引产物目录")
    parser.add_argument("--out", type=Path, required=True, help="fixture 输出路径")
    args = parser.parse_args(argv)

    symbols = load_symbols(args.index)
    if not symbols:
        parser.error(f"{args.index} 里没有找到目标文件的符号")
    kept = {r["symbol_id"] for r in symbols}
    _CONTAINERS.update({r["symbol_id"]: r.get("container") or "" for r in symbols})
    by_name: dict[str, int] = {}
    for record in symbols:
        by_name.setdefault(record["name"], record["symbol_id"])

    payload = {
        "symbols": [
            {
                "symbol_id": r["symbol_id"],
                "name": r["name"],
                "kind": r["type"],
                "file": Path(r["file"]).name,
                "span": [r["range"]["start_line"], r["range"]["end_line"]],
                "signature": r.get("signature", ""),
                "container": r.get("container", ""),
                "language": r.get("language", "java"),
            }
            for r in symbols
        ],
        "edges": build_edges(args.index, kept, by_name),
        "postings": build_postings(symbols),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8"
    )

    edges = payload["edges"]
    print(f"符号 {len(symbols)}  边 {len(edges)}  term {len(payload['postings'])}")
    print(f"  其中 contains 边 {sum(1 for e in edges if e['kind'] == 'contains')}")
    connected = {e["source_id"] for e in edges} | {e["target_id"] for e in edges}
    print(
        f"  连通符号 {len(connected)}/{len(symbols)} ({100 * len(connected) / len(symbols):.0f}%)"
    )
    print(f"→ {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
