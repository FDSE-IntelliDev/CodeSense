"""实测 cc.en.300 对本项目词表的适用性。

四个问题：
  1. 覆盖率 —— 302 个语料词 / 462 个符号单元有多少在 2M 词表里
  2. 系统词汇的词义 —— pool/thread/flush/swap 的近邻是不是自然语言义
  3. 缩写对应 —— cos(dept, department) 高不高，**且必须扣掉正字法混淆**
  4. 单元扩展 —— performance 的近邻里有没有 cache/buffer/async
"""
import json, re, sys, collections
import numpy as np
from gensim.models.fasttext import load_facebook_vectors

MODEL = sys.argv[1]
REPO = "/home1/wangchong/Workspace/CodeSense/output/youlai-boot-master"

print(f"加载 {MODEL} ...", flush=True)
kv = load_facebook_vectors(MODEL)
print(f"词表 {len(kv.key_to_index):,}  维度 {kv.vector_size}\n", flush=True)

cos = lambda a, b: float(np.dot(kv[a], kv[b]) / (np.linalg.norm(kv[a]) * np.linalg.norm(kv[b])))
inv = lambda w: w in kv.key_to_index


def split_ident(s):
    s = re.sub(r"[^A-Za-z]+", " ", s)
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", s)
    return [t.lower() for t in s.split() if t]


# ---------- 1. 覆盖率 ----------
print("=" * 74)
print("1. 覆盖率")
print("=" * 74)
corpus = json.load(open(f"{REPO}/enhanced_call_chain_corpus.json"))
cvocab = collections.Counter(t for e in corpus for t in (e if isinstance(e, list) else [e]))
syms = json.load(open(f"{REPO}/symbols_index.json"))
syms = syms if isinstance(syms, list) else list(syms.values())
units = collections.Counter()
for s in syms:
    units.update(split_ident(s.get("name") or s.get("symbol_name") or ""))

for name, vocab in [("调用链语料词表", cvocab), ("符号切分单元", units)]:
    miss = sorted({t for t in vocab if not inv(t)}, key=lambda t: -vocab[t])
    print(f"{name}: {len(vocab)} 个，词表内 {len(vocab)-len(miss)} "
          f"({100*(len(vocab)-len(miss))/len(vocab):.0f}%)")
    print(f"  OOV {len(miss)} 个（靠子词合成）: {miss[:14]}")

# ---------- 2. 系统词汇的词义 ----------
print("\n" + "=" * 74)
print("2. 系统词汇的近邻 —— 是自然语言义还是软件义？")
print("=" * 74)
for w in ["pool", "thread", "stream", "flush", "swap", "sector", "block",
          "buffer", "cache", "async", "disk", "queue"]:
    if inv(w):
        nb = [x for x, _ in kv.most_similar(w, topn=8)]
        print(f"  {w:10s} {', '.join(nb)}")

# ---------- 3. 缩写对应（带正字法对照） ----------
print("\n" + "=" * 74)
print("3. 缩写 ↔ 全称：真是语义对应，还是只是拼写像？")
print("=" * 74)
# (缩写, 正确全称, [同前缀但语义无关的干扰词])
PAIRS = [
    ("dept",   "department",     ["depth", "deposit", "depict", "depot"]),
    ("cfg",    "configuration",  ["cog", "cliff", "coffee"]),
    ("config", "configuration",  ["confidence", "confetti", "conflict"]),
    ("mgr",    "manager",        ["merge", "mugger", "meager"]),
    ("buf",    "buffer",         ["buffalo", "buffet", "bufo"]),
    ("impl",   "implementation", ["imply", "impala", "impale"]),
    ("msg",    "message",        ["mosaic", "musing", "massage"]),
    ("auth",   "authentication", ["author", "authority", "autism"]),
    ("req",    "request",        ["reque", "requiem", "reggae"]),
    ("pwd",    "password",       ["powder", "pawed", "pwn"]),
    ("dict",   "dictionary",     ["dictate", "diction", "dictator"]),
    ("addr",   "address",        ["adder", "adorn", "addax"]),
    ("dto",    "object",         ["ditto", "dote", "auto"]),
]
print(f"  {'缩写':<8}{'全称':<16}{'cos':>7}   {'干扰词最高':>12}{'cos':>7}   判定")
print("  " + "-" * 68)
wins = 0
tested = 0
for ab, full, distractors in PAIRS:
    if not (inv(ab) and inv(full)):
        print(f"  {ab:<8}{full:<16}  —— 不在词表")
        continue
    s_true = cos(ab, full)
    ds = [(d, cos(ab, d)) for d in distractors if inv(d)]
    if not ds:
        continue
    d_best, s_dist = max(ds, key=lambda x: x[1])
    tested += 1
    ok = s_true > s_dist
    wins += ok
    print(f"  {ab:<8}{full:<16}{s_true:>7.3f}   {d_best:>12}{s_dist:>7.3f}   "
          f"{'✓ 语义赢' if ok else '✗ 拼写赢'}")
print(f"\n  {wins}/{tested} 对里全称击败了同前缀干扰词")

# 随机对照：cos 的基线是多少
rng = np.random.default_rng(0)
common = [w for w in list(kv.key_to_index)[:20000] if w.isalpha() and len(w) > 3]
samp = rng.choice(len(common), 4000).reshape(2000, 2)
base = [cos(common[i], common[j]) for i, j in samp if common[i] != common[j]]
print(f"  随机词对基线: 均值 {np.mean(base):.3f}  95分位 {np.percentile(base,95):.3f}")

# ---------- 4. 单元扩展 ----------
print("\n" + "=" * 74)
print("4. 单元扩展：performance 的近邻里有没有 cache/buffer/async？")
print("=" * 74)
TARGETS = {
    "performance": ["cache", "buffer", "async", "batch", "pool", "latency", "throughput"],
    "disk":        ["swap", "block", "sector", "flush", "sync", "storage", "volume"],
    "io":          ["read", "write", "stream", "file", "input", "output"],
}
for unit, want in TARGETS.items():
    if not inv(unit):
        continue
    nb = [x for x, _ in kv.most_similar(unit, topn=30)]
    hit = [w for w in want if w in nb]
    print(f"  {unit}:")
    print(f"    top-15 近邻: {', '.join(nb[:15])}")
    print(f"    命中期望词 {len(hit)}/{len(want)}: {hit or '无'}")
    print(f"    期望词各自的 cos: " +
          ", ".join(f"{w}={cos(unit,w):.2f}" for w in want if inv(w)))
