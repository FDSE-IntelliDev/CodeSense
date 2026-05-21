#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import json
import os
import pathlib
import sys
from dataclasses import asdict
from typing import Dict, List, Optional, Tuple

from parsers import (
    BaseCallResolver,
    CallItem,
    DependencyItem,
    LSPCallResolver,
    SymbolItem,
    parse_file_with_registry,
)
from parsers.registry import LANG_EXT

from definition import PROJECT_PATH

sys.setrecursionlimit(10000)

TEXT_FILE_SIZE_LIMIT = 2 * 1024 * 1024
BATCH_SIZE = 50


def relpath(path: str, root: str) -> str:
    return os.path.relpath(path, root).replace("\\", "/")


def read_text(path: str) -> Optional[str]:
    try:
        if os.path.getsize(path) > TEXT_FILE_SIZE_LIMIT:
            return None
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    except Exception:
        return None


DEFAULT_IGNORE_DIRS = {
    ".git",
    ".idea",
    ".vscode",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
    "out",
    "target",
    "test",
    ".mvn",
    ".gradle",
    ".next",
    ".nuxt",
    ".cache",
    ".pytest_cache",
    ".mypy_cache",
    ".tox",
    "coverage",
    "bin",
    "obj",
}

def _load_extra_ignore_dirs() -> set:
    """
    支持通过环境变量追加忽略目录：
    CODE_INDEXER_IGNORE_DIRS="tmp,logs,generated"
    """
    raw = os.getenv("CODE_INDEXER_IGNORE_DIRS", "")
    if not raw.strip():
        return set()
    return {x.strip() for x in raw.split(",") if x.strip()}

def list_source_files(project_path: str) -> List[str]:
    files = []
    ignore_dirs = set(DEFAULT_IGNORE_DIRS) | _load_extra_ignore_dirs()

    for root, dirs, filenames in os.walk(project_path):
        # 目录级过滤：忽略常见产物目录 + 以 "." 开头的隐藏目录
        dirs[:] = [d for d in dirs if d not in ignore_dirs and not d.startswith(".")]

        for fn in filenames:
            ext = pathlib.Path(fn).suffix.lower()
            if ext in LANG_EXT:
                files.append(os.path.join(root, fn))
    return files


def analyze_file_ast(project_path: str, file_path: str):
    file_abs = os.path.abspath(file_path)
    source = read_text(file_path)
    if source is None:
        return [], [], []
    return parse_file_with_registry(file_abs, file_path, source)


def build_symbol_lookup(symbols: List[SymbolItem]) -> Dict[str, List[SymbolItem]]:
    d: Dict[str, List[SymbolItem]] = {}
    for s in symbols:
        d.setdefault(s.name, []).append(s)
    return d


def run(project_path: str, output_dir: str):
    files = list_source_files(project_path)

    all_symbols: List[SymbolItem] = []
    all_calls_raw: List[Tuple[str, str, int, str, str]] = []
    all_deps: List[DependencyItem] = []

    for i in range(0, len(files), BATCH_SIZE):
        batch = files[i:i + BATCH_SIZE]
        for fp in batch:
            # if fp=="/Users/huangzhuochen/IdeaProjects/youlai-boot-master/src/main/java/com/youlai/boot/core/validator/FieldValidator.java":
            #     a=1
            symbols, calls, deps = analyze_file_ast(project_path, fp)
            fr = relpath(fp, project_path)
            all_symbols.extend(symbols)
            all_calls_raw.extend([(c[0], c[1], c[2], c[3], fr) for c in calls])
            all_deps.extend(deps)

    symbols_lookup = build_symbol_lookup(all_symbols)
    call_resolver: BaseCallResolver = LSPCallResolver(project_path)

    all_calls: List[CallItem] = []
    for caller, callee, line, code, fr in all_calls_raw:
        all_calls.extend(
            call_resolver.resolve(caller, callee, line, code, fr, symbols_lookup)
        )

    dep_seen = set()
    deps_uniq = []
    for d in all_deps:
        k = (d.source_file, d.target_file, d.type)
        if k not in dep_seen:
            dep_seen.add(k)
            deps_uniq.append(d)

    os.makedirs(output_dir, exist_ok=True)

    with open(os.path.join(output_dir, "symbols_index.json"), "w", encoding="utf-8") as f:
        json.dump(
            [
                {"symbol_id": idx, **asdict(s), "range": asdict(s.range)}
                for idx, s in enumerate(all_symbols, start=1)
            ],
            f,
            ensure_ascii=False,
            indent=2,
        )

    with open(os.path.join(output_dir, "call_graph.json"), "w", encoding="utf-8") as f:
        json.dump([asdict(c) for c in all_calls], f, ensure_ascii=False, indent=2)

    with open(os.path.join(output_dir, "dependency_graph.json"), "w", encoding="utf-8") as f:
        json.dump([asdict(d) for d in deps_uniq], f, ensure_ascii=False, indent=2)

    print(f"Done. symbols={len(all_symbols)}, calls={len(all_calls)}, deps={len(deps_uniq)}")
    print(f"Output dir: {output_dir}")


def main():
    parser = argparse.ArgumentParser(description="LSP-first code indexer with AST fallback")
    parser.add_argument("--project_path", help="项目根目录路径")
    parser.add_argument("--output", default=".", help="输出目录，默认当前目录")
    # args = parser.parse_args([
    #     "--project_path","/Users/huangzhuochen/IdeaProjects/youlai-boot-master-gt",
    #     "--output","./output/youlai-boot-master-gt"
    # ])
    args = parser.parse_args([
        "--project_path", PROJECT_PATH,
        "--output", "./output/youlai-boot-master",
    ])


    project_path = os.path.abspath(args.project_path)
    output_dir = os.path.abspath(args.output)
    run(project_path, output_dir)


if __name__ == "__main__":
    main()
