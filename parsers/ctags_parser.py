# parsers/ctags_parser.py
import json
import re
import subprocess
from typing import List, Tuple

from parsers.base import SymbolItem, RangeInfo, DependencyItem


def parse_c_cpp_ctags(file_rel: str, source: str, lang: str = "c") -> Tuple[
    List[SymbolItem], list, List[DependencyItem]]:
    """
    使用 Universal Ctags 解析 C/Cpp 文件，提取语法符号及引用依赖。
    适合用来替代 Tree-sitter 以规避深度 RecursionError 并提升超大型项目的主干提取速度。
    """
    symbols: List[SymbolItem] = []
    calls: list = []
    deps: List[DependencyItem] = []

    lines = source.splitlines()

    # 1. 提取依赖 (#include)
    include_pattern = re.compile(r'^\s*#\s*include\s*[<"]([^>"]+)[>"]')
    for line_num, line in enumerate(lines):
        m = include_pattern.match(line)
        if m:
            deps.append(DependencyItem(
                source_file=file_rel,
                target_file=m.group(1),
                type="include"
            ))

    # 2. 调用 Ctags 提取代码元素并解析 JSON 输出
    # 参数说明：--fields=+nK 增加行号(n)和全量类型标识(K)，--language-force 强制语言类型识别
    ctags_lang = "C++" if lang == "cpp" else "C"
    cmd = [
        "ctags",
        "--output-format=json",
        "--fields=+nK",
        "--sort=no",
        f"--language-force={ctags_lang}",
        "-f", "-",
        "-"
    ]

    try:
        proc = subprocess.run(
            cmd,
            input=source,
            capture_output=True,
            text=True,
            check=False
        )

        for line in proc.stdout.splitlines():
            if not line.strip():
                continue
            try:
                record = json.loads(line)
                if record.get("_type") == "tag" or "name" in record:
                    name = record.get("name", "unknown")
                    kind = record.get("kind", "unknown")

                    # ctags 行号是 1-based
                    line_idx = record.get("line", 1)

                    symbols.append(SymbolItem(
                        name=name,
                        type=kind,
                        file=file_rel,
                        range=RangeInfo(line_idx, line_idx),
                        signature=record.get("pattern", name).strip("/^$ \n\r\t.;"),
                        language=lang,
                        doc="",
                        container=record.get("scope", "")
                    ))
            except json.JSONDecodeError:
                continue
    except Exception as e:
        print(f"Ctags parsing failed for {file_rel}: {e}")

    return symbols, calls, deps
