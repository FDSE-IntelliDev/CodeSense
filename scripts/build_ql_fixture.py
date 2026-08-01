"""Cutting a small fixture out of a real index, for validating hand-written
QL.

Phase A's acceptance test is "hand-write some QL and see whether the operator
set suffices". For that exercise to mean anything the data has to come from a
real project rather than an invented topology -- the sparsity, naming habits
and missing fields of real data are what the operators will actually meet.

Usage::

    python scripts/build_ql_fixture.py \\
        --index output/youlai-boot-master \\
        --out tests/fixtures/ql/mini_index.json

This is a **glue script**: read, split, write. The real logic lives in
``codesense.ql``; this only reshapes external data into what it reads.
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

#: How many files to take. **Sampling must cross domains**, never a single
#: module: ICF is computed against the indexed corpus, so a fixture of only
#: user/auth files puts `user` on 75% of symbols, ICF declares it useless,
#: and lexical queries come back empty. In the real project `user` is
#: 125/1718 (7%), and the fixture's word distribution should be close.
FILE_SAMPLE = 40

#: Files that must be included -- best connected in the call graph, and so
#: able to support graph queries.
ANCHOR_FILES = (
    "RedisTokenManager.java",
    "SecurityUtils.java",
    "UserController.java",
    "UserServiceImpl.java",
    "Result.java",
)


def split_identifier(name: str) -> list[str]:
    """Split an identifier, preferring the project's srctoolkit dependency
    (Ronin underneath).

    Ronin's frequency table was mined from GitHub Java projects and does well
    on Java. The fallback path is used only when the dependency is missing,
    is for fixture construction alone, and never reaches production.
    """
    try:
        from srctoolkit.delimiter import Delimiter

        return [t for t in Delimiter.split_camel(name).split() if t.isalpha()]
    except ImportError:
        text = re.sub(r"[^A-Za-z]+", " ", name)
        text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", text)
        return [t.lower() for t in text.split() if t]


def load_symbols(index_dir: Path) -> list[dict[str, Any]]:
    """Cross-domain sampling: the anchor files plus an even stride through
    files ordered by symbol count.

    An even stride rather than the top N: the top N clusters on one kind of
    large file (all Services), while a stride spans the controller / service
    / entity / config layers.
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
    """Real `calls` edges plus materialised `contains` edges.

    `contains` is chapter 10's "cheapest first step by far": the data already
    sits in ``code_symbols.container``, and materialising it as edges drops
    the orphan count straight to zero.
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
    """An inverted index over the name and container fields.

    Only those two: annotations were not extracted by the parser (a debt
    recorded in chapter 10) and doc is essentially empty in the sample
    project. **The fixture reflects reality and does not paper over it.**
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
    parser.add_argument(
        "--index", type=Path, required=True, help="directory holding the index artifacts"
    )
    parser.add_argument("--out", type=Path, required=True, help="where to write the fixture")
    args = parser.parse_args(argv)

    symbols = load_symbols(args.index)
    if not symbols:
        parser.error(f"no symbols for the target files found in {args.index}")
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
    print(f"symbols {len(symbols)}  edges {len(edges)}  terms {len(payload['postings'])}")
    print(f"  of which contains edges {sum(1 for e in edges if e['kind'] == 'contains')}")
    connected = {e["source_id"] for e in edges} | {e["target_id"] for e in edges}
    print(
        f"  connected symbols {len(connected)}/{len(symbols)} "
        f"({100 * len(connected) / len(symbols):.0f}%)"
    )
    print(f"→ {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
