"""跑语义检索 benchmark。

    python scripts/run_benchmark.py --index-dir DIR --queries evaluation/benchmark/queries.json \\
        --base-url https://api.openai.com/v1 --model gpt-4o-mini

对每条查询跑三条路径：

    literal    只用查询里的字面词（下限参照）
    generic    LLM 凭通用知识派生词，**不给它看项目词表**
    grounded   LLM **从项目词表里挑**，外加注解信号

`generic` vs `grounded` 才是关键对比——它直接检验
``docs/design/09-grounding.md`` 第六节的主张：把项目词表放进 prompt，
输出的词就天然落在项目实际用法上，不会出现「LLM 说 buffer 而项目写 buf」。

指标用**召回率**而非准确率：gold 集只求确凿不求完备，
列出的都确实是正确答案，但没列的未必是错的。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from codesense.indexing import build_expansion_table
from codesense.ql import Element, Frag, IndexField
from codesense.ql.context import EvalContext
from codesense.ql.operators import eval_unit, score_of
from codesense.ql.satisfiers import AnnotationSatisfier, LexicalSatisfier
from codesense.ql.store import (
    InMemoryEdgeStore,
    InMemoryPostingIndex,
    InMemorySymbolStore,
    Posting,
)
from codesense.ql.unit import QueryUnit, Term

#: 放进 prompt 的项目词表大小。大项目的全量词表塞不下，
#: 按 ICF 取中段——太常见的没区分度，只出现一两次的多半是噪音。
VOCAB_SAMPLE = 600

#: 报告用的截断位置。
CUTOFFS = (10, 30, 100)

GENERIC_PROMPT = """\
你在为一个代码检索系统扩展查询词。

目标代码库：{project}
用户查询：{query}

请给出你认为会出现在相关代码的标识符里的英文单词，输出 JSON：
{{"terms": ["词1", "词2", ...], "annotations": ["@注解名", ...]}}

要求：terms 最多 25 个，全部小写单词（标识符切分后的形态），
不要 get/set/value 这类通用词。
"""

PROMPT = """\
你在为一个代码检索系统扩展查询词。

目标代码库：{project}
用户查询：{query}

这个代码库里出现过的词（已按信息量排序，只能从中挑）：
{vocab}

请从上面的词表里挑出与查询相关的词，输出 JSON：
{{"terms": ["词1", "词2", ...], "annotations": ["@注解名", ...], "reason": "一句话"}}

要求：
- terms 最多 25 个，**必须**全部来自上面的词表，不要自己造词
- 挑那些真正指向查询意图的领域词，不要挑 get/set/value 这类通用词
- annotations 里放你认为相关的框架注解（如 @PostMapping），词表里没有也可以写
"""


@dataclass
class Result:
    query_id: str
    project: str
    gold: list[str]
    ranks: dict[str, dict[str, int]] = field(default_factory=dict)
    terms: dict[str, list[str]] = field(default_factory=dict)
    annotations: list[str] = field(default_factory=list)

    def recall(self, arm: str, cutoff: int) -> float:
        ranks = self.ranks.get(arm, {})
        found = sum(1 for name in self.gold if 0 < ranks.get(name, 0) <= cutoff)
        return found / len(self.gold) if self.gold else 0.0


def load_index(path: Path) -> tuple[EvalContext, dict[str, list[int]]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    elements = [
        Element(
            symbol_id=s["symbol_id"],
            name=s["name"],
            kind=s["kind"],
            file=s["file"],
            span=tuple(s["span"]),
            signature=s.get("signature", ""),
            container=s.get("container", ""),
            doc=s.get("doc", ""),
            language="java",
            modifiers=frozenset(s.get("modifiers", ())),
        )
        for s in payload["symbols"]
    ]
    postings = {
        term: [Posting(p["symbol_id"], IndexField(p["field"]), p["tf"]) for p in entries]
        for term, entries in payload["postings"].items()
    }
    ctx = EvalContext(
        symbols=InMemorySymbolStore(elements),
        postings=InMemoryPostingIndex(postings, total_symbols=len(elements)),
        expansion=build_expansion_table(),
        edges=InMemoryEdgeStore([]),
    )
    by_name: dict[str, list[int]] = {}
    for element in elements:
        by_name.setdefault(element.name, []).append(element.symbol_id)
    return ctx, by_name


def vocabulary(ctx: EvalContext, limit: int = VOCAB_SAMPLE) -> list[str]:
    """放进 prompt 的项目词表。``limit <= 0`` 表示全给。

    **收窄方式很要紧。** 早期版本按 ``abs(icf_ratio - 0.55)`` 取固定
    ICF 带，结果在 netty 上把 `buf`(0.176) / `allocator`(0.311) /
    `pooled` / `chunk` 全排除在外——恰恰因为它们在 netty 里常见。
    而查询问的就是缓冲区分配。**与查询无关的静态收窄会系统性地丢掉
    领域核心词**，这是 benchmark 量出来的。
    """
    usable = [
        term
        for term in ctx.postings.terms()
        if term.isalpha()
        and len(term) >= 3
        and (info := ctx.postings.term_info(term))
        and info.df >= 2
    ]
    if limit <= 0:
        return sorted(usable)
    scored = sorted(
        (abs((ctx.postings.term_info(t).icf_ratio if ctx.postings.term_info(t) else 0) - 0.55), t)
        for t in usable
    )
    return sorted(term for _, term in scored[:limit])


def literal_terms(query: str, ctx: EvalContext) -> list[str]:
    """查询里能直接对上索引的词。baseline 用它。"""
    words = {w.lower() for w in re.findall(r"[A-Za-z]+", query) if len(w) > 2}
    return sorted(w for w in words if ctx.postings.term_info(w) is not None)


def derive(
    query: str, project: str, vocab: Sequence[str] | None, judge_config: Any
) -> dict[str, Any]:
    """让 LLM 派生查询词。``vocab`` 为 None 时它只能凭通用知识。"""
    import requests

    prompt = (
        PROMPT.format(project=project, query=query, vocab=", ".join(vocab))
        if vocab is not None
        else GENERIC_PROMPT.format(project=project, query=query)
    )
    response = requests.post(
        f"{judge_config.base_url.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {judge_config.api_key}"},
        json={
            "model": judge_config.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
        },
        timeout=judge_config.timeout,
    )
    response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"]
    start, end = content.find("{"), content.rfind("}")
    if start < 0 or end <= start:
        return {"terms": [], "annotations": []}
    return json.loads(content[start : end + 1])


def rank(frag: Frag, unit: str) -> dict[str, int]:
    """符号名 → 名次。同名取最好的名次。"""
    ordered = sorted(frag.nodes, key=lambda sid: (-score_of(frag, sid, unit), sid))
    ranks: dict[str, int] = {}
    for position, symbol_id in enumerate(ordered, 1):
        ranks.setdefault(frag.nodes[symbol_id].name, position)
    return ranks


def search(ctx: EvalContext, terms: Sequence[str], annotations: Sequence[str]) -> Frag:
    satisfiers: list[Any] = []
    if terms:
        satisfiers.append(LexicalSatisfier(terms=tuple(Term(t) for t in terms), weight=0.5))
    if annotations:
        satisfiers.append(AnnotationSatisfier(names=tuple(annotations), weight=0.9))
    if not satisfiers:
        return Frag()
    return eval_unit(QueryUnit("q", satisfiers=tuple(satisfiers)), ctx)


def run(args: argparse.Namespace) -> list[Result]:
    from codesense.llm import LlmConfig

    config = LlmConfig.load(base_url=args.base_url, model=args.model)
    spec = json.loads(args.queries.read_text(encoding="utf-8"))
    contexts: dict[str, tuple[EvalContext, dict[str, list[int]]]] = {}
    results: list[Result] = []

    for case in spec["queries"]:
        project = case["project"]
        if project not in contexts:
            contexts[project] = load_index(args.index_dir / f"{project}.json")
        ctx, _ = contexts[project]

        result = Result(query_id=case["id"], project=project, gold=case["gold"])

        literal = literal_terms(case["query"], ctx)
        result.terms["literal"] = literal
        result.ranks["literal"] = rank(search(ctx, literal, ()), "q")

        for arm, vocab in (("generic", None), ("grounded", vocabulary(ctx, args.vocab_size))):
            answer = derive(case["query"], project, vocab, config)
            terms = [t.lower() for t in answer.get("terms", []) if isinstance(t, str)][:25]
            annotations = [a for a in answer.get("annotations", []) if isinstance(a, str)]
            result.terms[arm] = terms
            if arm == "grounded":
                result.annotations = annotations
            result.ranks[arm] = rank(search(ctx, sorted({*literal, *terms}), annotations), "q")

        in_vocab = sum(1 for t in result.terms["generic"] if ctx.postings.term_info(t) is not None)
        total = len(result.terms["generic"]) or 1
        results.append(result)
        print(
            f"  {case['id']:<22}generic {total} 词（{in_vocab} 个在项目词表里, "
            f"{100 * in_vocab / total:.0f}%）  grounded {len(result.terms['grounded'])} 词"
        )
    return results


ARMS = ("literal", "generic", "grounded")


def report(results: Sequence[Result]) -> None:
    for cutoff in CUTOFFS:
        print(f"\n{'=' * 62}\n召回率 @{cutoff}")
        print(f"  {'查询':<24}{'gold':>5}" + "".join(f"{a:>11}" for a in ARMS))
        print("  " + "-" * 58)
        totals: Counter[str] = Counter()
        for result in results:
            row = f"  {result.query_id:<24}{len(result.gold):>5}"
            for arm in ARMS:
                value = result.recall(arm, cutoff)
                totals[arm] += value
                row += f"{value:>10.0%} "
            print(row)
        n = len(results) or 1
        print("  " + "-" * 58)
        print(f"  {'平均':<24}{'':>5}" + "".join(f"{totals[a] / n:>10.0%} " for a in ARMS))
    print()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index-dir", type=Path, required=True)
    parser.add_argument("--queries", type=Path, required=True)
    parser.add_argument("--base-url", default="https://api.openai.com/v1")
    parser.add_argument("--model", default="gpt-4o-mini")
    parser.add_argument(
        "--vocab-size", type=int, default=VOCAB_SAMPLE, help="放进 prompt 的词表大小，<=0 表示全给"
    )
    parser.add_argument("--dump", type=Path, help="把明细写到这里")
    args = parser.parse_args(argv)

    results = run(args)
    report(results)
    if args.dump:
        args.dump.write_text(
            json.dumps(
                [
                    {
                        "id": r.query_id,
                        "terms": r.terms,
                        "annotations": r.annotations,
                        "gold_ranks": {
                            g: {arm: r.ranks.get(arm, {}).get(g) for arm in ARMS} for g in r.gold
                        },
                    }
                    for r in results
                ],
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )
        print(f"明细 → {args.dump}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
