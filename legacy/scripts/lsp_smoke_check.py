"""手动验一次 JDT.LS 通不通：起服务、抽一条调用链、打印。

    python -m scripts.lsp_smoke_check --file <绝对路径> --func updateUser

需要本地装好 jdtls，且 configs/default.yaml 里的 target.project_path
指向一个真实的 Java 项目。跑得慢（要等索引），只在排查 LSP 问题时用。
"""

from __future__ import annotations

import argparse
import json
import time

from codesense.config import load_config
from codesense.parsers.java_lsp_client import JavaCallChainExtractor, JavaLSPClient


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--file", required=True, help="目标 .java 文件的绝对路径")
    p.add_argument("--func", required=True, help="目标函数名")
    p.add_argument("--layer", type=int, default=2)
    p.add_argument("--warmup", type=float, default=10.0, help="等待 JDT.LS 建索引的秒数")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    cfg = load_config()

    client = JavaLSPClient(project_root=str(cfg.project_path), jdtls_path=cfg.tools.jdtls_path)
    print("启动 LSP 并建索引，可能要等十几秒 ...")
    client.start()
    time.sleep(args.warmup)
    try:
        result = JavaCallChainExtractor(client).get_call_chain(
            filepath=args.file, func_name=args.func, layer=args.layer
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))
    finally:
        client.stop()
        print("LSP 已停止。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
