"""词义错配打分：通用向量位置 vs 项目里的实际用法。

对每个词 t，取它在项目语料里的共现词 C(t)，算 t 与 C(t) 在通用空间里的平均余弦。
分数低 = 通用向量放的位置和项目怎么用它对不上 = 最该微调的词。
"""
import json, collections, sys
import numpy as np
from gensim.models.fasttext import load_facebook_vectors
kv = load_facebook_vectors(sys.argv[1])
REPO = "/home1/wangchong/Workspace/CodeSense/output/youlai-boot-master"
corpus = [e if isinstance(e, list) else [e]
          for e in json.load(open(f"{REPO}/enhanced_call_chain_corpus.json"))]
freq = collections.Counter(t for e in corpus for t in e)
co = collections.defaultdict(collections.Counter)
for e in corpus:
    for a in set(e):
        co[a].update(x for x in set(e) if x != a)

nrm = lambda w: kv[w] / (np.linalg.norm(kv[w]) + 1e-9)
import math
N = len(corpus)
df = collections.Counter()
for e in corpus: df.update(set(e))
icf = {t: math.log(N / df[t]) for t in df}          # 与 icf_term_embedding.py 同式
rows = []
for t, ctx in co.items():
    partners = [(u, n) for u, n in ctx.most_common(25)]
    if len(partners) < 3 or freq[t] < 50:
        continue
    tv = nrm(t)
    w = [n * icf[u] for u, n in partners]           # ICF 加权，压掉 get/save/id
    if sum(w) <= 0: continue
    s = np.average([float(tv @ nrm(u)) for u, _ in partners], weights=w)
    rows.append((s, t, freq[t], kv.key_to_index.get(t, -1),
                 [u for u, _ in partners[:5]]))
rows.sort()

print("=== 最该微调的 25 个词（ICF 加权后）===")
print(f"  {'词':<13}{'项目频次':>9}{'cc排名':>10}{'错配分':>8}   项目里的共现词")
for s, t, f, r, ctx in rows[:25]:
    print(f"  {t:<13}{f:>9}{(f'{r:,}' if r>0 else 'OOV'):>10}{s:>8.3f}   {', '.join(ctx)}")

print("\n=== 对照：匹配最好的 8 个（微调应尽量别动它们）===")
for s, t, f, r, ctx in rows[-8:]:
    print(f"  {t:<13}{f:>9}{(f'{r:,}' if r>0 else 'OOV'):>10}{s:>8.3f}   {', '.join(ctx)}")

bad = [r for r in rows if r[0] < 0.15]
mass = sum(r[2] for r in bad) / sum(freq.values())
print(f"\n错配分 <0.15 的词: {len(bad)}/{len(rows)} 个，"
      f"占项目 token 总量的 {100*mass:.1f}%")

