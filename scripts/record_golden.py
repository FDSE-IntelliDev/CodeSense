"""重录 golden 期望值。

    python -m scripts.record_golden               # 全部（缺资源的跳过）
    python -m scripts.record_golden --only tokenizer

**重录前先确认当前行为是对的。** golden 只保证「和上次一样」，不保证「对」——
在有真实 bug 的状态下重录，等于把 bug 固化成期望值。

正常的重构流程是：改代码 -> 跑 pytest -m slow -> 绿了就说明行为没变。
只有在**有意**改变行为（修 bug、换算法）之后，才该重录。
"""

from __future__ import annotations

import argparse
import json
import sys

from tests import golden_cases as gc


def _dump(name: str, cases: dict) -> None:
    path = gc.GOLDEN_DIR / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(cases, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    size = path.stat().st_size
    print(f"  {name}: {len(cases)} 组用例 -> {path.name}（{size // 1024}K）")


def record_tokenizer() -> None:
    _dump("tokenizer", gc.tokenizer_cases())


def record_relation_filter() -> None:
    from codesense.filters.relation_graph_store import RelationGraphStore

    db = gc.PROJECT_OUTPUT / "codegraph.sqlite"
    store = RelationGraphStore.open_if_ready(str(db))
    if store is None:
        print(f"  relation_filter: 跳过（{db} 不可用）")
        return
    try:
        _dump("relation_filter", gc.relation_filter_cases(store))
    finally:
        store.close()


def record_intention() -> None:
    _dump("intention_stages", gc.intention_cases())


RECORDERS = {
    "tokenizer": record_tokenizer,
    "relation_filter": record_relation_filter,
    "intention": record_intention,
}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--only", choices=sorted(RECORDERS), help="只重录其中一组")
    args = p.parse_args(argv)

    names = [args.only] if args.only else list(RECORDERS)
    for name in names:
        try:
            RECORDERS[name]()
        except (ImportError, ModuleNotFoundError, OSError) as e:
            print(f"  {name}: 跳过（{type(e).__name__}: {str(e)[:70]}）", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
