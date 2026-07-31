"""在项目词表上探测一个预训练词向量模型好不好用。

设计文档第 09 章的几个判断都是这个脚本量出来的。换模型、或者微调完之后，
重跑它就能知道结论还成不成立。

    python scripts/probe_embeddings.py coverage   --model M --index D
    python scripts/probe_embeddings.py senses     --model M
    python scripts/probe_embeddings.py abbrev     --model M
    python scripts/probe_embeddings.py mismatch   --model M --index D
    python scripts/probe_embeddings.py gates      --model M --index D

四个子命令对应四个问题：

    coverage   项目词表有多少在模型词表里，排名靠不靠前
    senses     系统词汇的近邻是软件义还是自然语言义
    abbrev     缩写↔全称的对应是真语义还是只是拼写像（**带正字法对照**）
    mismatch   哪些词的通用向量和项目里的实际用法对不上（微调优先级）
    gates      微调的两个门禁分别覆盖哪些词，是否真的冲突

依赖 ``gensim``，以及一份 fastText ``.bin``（如 ``cc.en.300.bin``）。
这是**研究脚本**：读盘、算数、打印，不进查询路径。
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import numpy as np

#: 缩写 → (正确全称, 同前缀但语义无关的干扰词)。
#: 干扰词是关键：fastText 有子词，`dept` 和 `department` 共享 n-gram，
#: 余弦会被正字法重叠本身抬高，不设对照就分不清语义还是拼写。
ABBREVIATIONS: dict[str, tuple[str, tuple[str, ...]]] = {
    "dept": ("department", ("depth", "deposit", "depict", "depot")),
    "cfg": ("configuration", ("cog", "cliff", "coffee")),
    "config": ("configuration", ("confidence", "confetti", "conflict")),
    "mgr": ("manager", ("merge", "mugger", "meager")),
    "buf": ("buffer", ("buffalo", "buffet", "bufo")),
    "impl": ("implementation", ("imply", "impala", "impale")),
    "msg": ("message", ("mosaic", "musing", "massage")),
    "auth": ("authentication", ("author", "authority", "autism")),
    "pwd": ("password", ("powder", "pawed", "pwn")),
    "dict": ("dictionary", ("dictate", "diction", "dictator")),
    "addr": ("address", ("adder", "adorn", "addax")),
    "perms": ("permission", ("perm", "perky", "persimmon")),
}

#: 系统层词汇。它们在自然语言里的主义项和在代码里的完全不同，
#: 是通用语料训出来的向量最可能出错的地方。
SYSTEMS_TERMS = (
    "pool",
    "thread",
    "stream",
    "flush",
    "swap",
    "sector",
    "block",
    "buffer",
    "cache",
    "async",
    "disk",
    "queue",
)

#: 判定「已匹配 / 错配」的分界。低于它的词在微调时应放开，高于的应冻结。
MISMATCH_FLOOR = 0.15


def load_vectors(model_path: Path) -> Any:
    from gensim.models.fasttext import load_facebook_vectors

    return load_facebook_vectors(str(model_path))


def load_corpus(index_dir: Path) -> list[list[str]]:
    raw = json.loads((index_dir / "enhanced_call_chain_corpus.json").read_text(encoding="utf-8"))
    return [entry if isinstance(entry, list) else [entry] for entry in raw]


def split_identifier(name: str) -> list[str]:
    text = re.sub(r"[^A-Za-z]+", " ", name)
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", text)
    return [token.lower() for token in text.split() if token]


class Space:
    """一个词向量空间上的几个常用量。"""

    def __init__(self, vectors: Any) -> None:
        self._kv = vectors

    def __contains__(self, word: object) -> bool:
        return word in self._kv.key_to_index

    def rank(self, word: str) -> int:
        return self._kv.key_to_index.get(word, -1)

    def unit(self, word: str) -> np.ndarray:
        vector = self._kv[word]
        return vector / (float(np.linalg.norm(vector)) + 1e-9)

    def cosine(self, left: str, right: str) -> float:
        return float(self.unit(left) @ self.unit(right))

    def neighbours(self, word: str, count: int = 8) -> list[str]:
        return [name for name, _ in self._kv.most_similar(word, topn=count)]

    def size(self) -> int:
        return len(self._kv.key_to_index)


def context_profile(corpus: list[list[str]]) -> tuple[Counter[str], dict[str, Counter[str]]]:
    """词频与共现。"""
    freq: Counter[str] = Counter(term for entry in corpus for term in entry)
    co: dict[str, Counter[str]] = defaultdict(Counter)
    for entry in corpus:
        unique = set(entry)
        for term in unique:
            co[term].update(unique - {term})
    return freq, co


def inverse_chain_frequency(corpus: list[list[str]]) -> dict[str, float]:
    total = len(corpus)
    document_frequency: Counter[str] = Counter()
    for entry in corpus:
        document_frequency.update(set(entry))
    return {term: math.log(total / df) for term, df in document_frequency.items()}


def mismatch_score(
    space: Space, term: str, partners: list[tuple[str, int]], icf: dict[str, float]
) -> float | None:
    """通用向量的位置与项目里实际用法的吻合度。

    用 ICF 加权，否则 `get`（出现 12 万次）会主导所有上下文向量。
    """
    usable = [(word, count) for word, count in partners if word in space]
    if len(usable) < 3:
        return None
    weights = [count * icf.get(word, 0.0) for word, count in usable]
    if sum(weights) <= 0:
        return None
    similarities = [space.cosine(term, word) for word, _ in usable]
    return float(np.average(similarities, weights=weights))


def cmd_coverage(space: Space, corpus: list[list[str]], index_dir: Path) -> None:
    freq, _ = context_profile(corpus)
    symbols = json.loads((index_dir / "symbols_index.json").read_text(encoding="utf-8"))
    units: Counter[str] = Counter()
    for record in symbols:
        units.update(split_identifier(record.get("name") or ""))

    print(f"模型词表 {space.size():,}\n")
    for label, vocabulary in (("调用链语料", freq), ("符号切分单元", units)):
        missing = sorted((t for t in vocabulary if t not in space), key=lambda t: -vocabulary[t])
        inside = len(vocabulary) - len(missing)
        pct = 100 * inside / len(vocabulary)
        print(f"{label}: {len(vocabulary)} 个，在模型里 {inside} ({pct:.0f}%)")
        print(f"  OOV: {missing[:8]}")

    print("\n「在词表里」不等于「向量能用」——按排名分档：")
    buckets = (
        (0, 50_000, "top 50k  训得充分"),
        (50_000, 200_000, "50k-200k 尚可"),
        (200_000, 800_000, "200k-800k 偏弱"),
        (800_000, 10**9, ">800k    基本是噪音"),
    )
    for low, high, label in buckets:
        hit = [t for t in freq if low <= space.rank(t) < high]
        print(f"  {label:<22}{len(hit):>4} 个")
        if low >= 200_000 and hit:
            print(f"      {sorted(hit, key=lambda t: -freq[t])[:10]}")


def cmd_senses(space: Space) -> None:
    print("系统词汇的近邻 —— 软件义还是自然语言义？\n")
    for term in SYSTEMS_TERMS:
        if term in space:
            print(f"  {term:<10}{', '.join(space.neighbours(term))}")


def cmd_abbrev(space: Space) -> None:
    print("缩写 ↔ 全称：真语义对应，还是只是拼写像？")
    print(f"  {'缩写':<8}{'全称':<16}{'cos':>7}   {'干扰词':>12}{'cos':>7}   判定")
    print("  " + "-" * 66)
    wins = tested = 0
    for short, (full, distractors) in ABBREVIATIONS.items():
        if short not in space or full not in space:
            print(f"  {short:<8}{full:<16}  —— 不在词表")
            continue
        available = [(d, space.cosine(short, d)) for d in distractors if d in space]
        if not available:
            continue
        worst, worst_score = max(available, key=lambda pair: pair[1])
        true_score = space.cosine(short, full)
        tested += 1
        won = true_score > worst_score
        wins += won
        print(
            f"  {short:<8}{full:<16}{true_score:>7.3f}   {worst:>12}{worst_score:>7.3f}   "
            f"{'✓ 语义赢' if won else '✗ 拼写赢'}"
        )
    print(f"\n  {wins}/{tested} 对里全称击败了同前缀干扰词")
    print(f"  {_baseline(space)}")


def _baseline(space: Space) -> str:
    generator = np.random.default_rng(0)
    words = [w for w in list(space._kv.key_to_index)[:20_000] if w.isalpha() and len(w) > 3]
    pairs = generator.choice(len(words), 4_000).reshape(2_000, 2)
    scores = [space.cosine(words[i], words[j]) for i, j in pairs if words[i] != words[j]]
    return f"随机词对基线: 均值 {np.mean(scores):.3f}  95 分位 {np.percentile(scores, 95):.3f}"


def cmd_mismatch(space: Space, corpus: list[list[str]]) -> None:
    freq, co = context_profile(corpus)
    icf = inverse_chain_frequency(corpus)
    rows = []
    for term, partners in co.items():
        if freq[term] < 50 or term not in space:
            continue
        score = mismatch_score(space, term, partners.most_common(25), icf)
        if score is not None:
            rows.append((score, term, freq[term], [w for w, _ in partners.most_common(5)]))
    rows.sort()

    print("最该微调的 20 个词（通用向量与项目用法最不匹配）")
    print(f"  {'词':<14}{'项目频次':>9}{'错配分':>9}   项目里的共现词")
    for score, term, count, context in rows[:20]:
        print(f"  {term:<14}{count:>9}{score:>9.3f}   {', '.join(context)}")

    bad = [row for row in rows if row[0] < MISMATCH_FLOOR]
    mass = sum(row[2] for row in bad) / sum(freq.values())
    share = f"{100 * mass:.1f}%"
    print(f"\n  错配分 <{MISMATCH_FLOOR} 的词 {len(bad)}/{len(rows)} 个，占 token 总量 {share}")
    print("  注意：该指标把「真错配」和「共现词全是无信息动词」混在一起，")
    print("  榜单前部可信，占比应作上界看。")


def cmd_gates(space: Space, corpus: list[list[str]]) -> None:
    """微调的两个门禁分别覆盖哪些词——它们是否真的冲突。"""
    freq, co = context_profile(corpus)
    icf = inverse_chain_frequency(corpus)
    print("门禁1（缩写映射不许退化）的词，各自的错配分：")
    print(f"  {'缩写':<8}{'全称':<16}{'cos':>7}{'项目频次':>9}{'错配分':>9}   微调怎么处理")
    print("  " + "-" * 74)
    for short, (full, _) in ABBREVIATIONS.items():
        if short not in space or full not in space:
            continue
        score = (
            mismatch_score(space, short, co[short].most_common(25), icf) if short in co else None
        )
        if score is None:
            note = "项目里没有/太少 → 拿不到梯度，天然安全"
        elif score >= MISMATCH_FLOOR:
            note = "✅ 已匹配 → 冻结，门禁1 自动满足"
        else:
            note = "⚠️ 错配 → 放开，微调应**改善** cos"
        shown = f"{score:.3f}" if score is not None else "—"
        head = f"  {short:<8}{full:<16}{space.cosine(short, full):>7.3f}"
        print(f"{head}{freq.get(short, 0):>9}{shown:>9}   {note}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("coverage", "senses", "abbrev", "mismatch", "gates"))
    parser.add_argument("--model", type=Path, required=True, help="fastText .bin")
    parser.add_argument("--index", type=Path, help="索引产物目录，coverage/mismatch/gates 需要")
    args = parser.parse_args(argv)

    if args.command in ("coverage", "mismatch", "gates") and args.index is None:
        parser.error(f"{args.command} 需要 --index")

    print(f"加载 {args.model} ...", flush=True)
    space = Space(load_vectors(args.model))

    if args.command == "senses":
        cmd_senses(space)
        return 0

    corpus = load_corpus(args.index)
    if args.command == "coverage":
        cmd_coverage(space, corpus, args.index)
    elif args.command == "abbrev":
        cmd_abbrev(space)
    elif args.command == "mismatch":
        cmd_mismatch(space, corpus)
    else:
        cmd_gates(space, corpus)
    return 0


if __name__ == "__main__":
    sys.exit(main())
