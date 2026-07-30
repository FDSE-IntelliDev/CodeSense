"""门禁1的词和门禁2的词，是不是同一批？"""
import json, collections, math, sys
import numpy as np
from gensim.models.fasttext import load_facebook_vectors
kv = load_facebook_vectors(sys.argv[1])
REPO = "/home1/wangchong/Workspace/CodeSense/output/youlai-boot-master"
corpus = [e if isinstance(e,list) else [e] for e in
          json.load(open(f"{REPO}/enhanced_call_chain_corpus.json"))]
freq = collections.Counter(t for e in corpus for t in e)
co = collections.defaultdict(collections.Counter)
for e in corpus:
    for a in set(e): co[a].update(x for x in set(e) if x != a)
N=len(corpus); df=collections.Counter()
for e in corpus: df.update(set(e))
icf={t: math.log(N/df[t]) for t in df}
nrm=lambda w: kv[w]/(np.linalg.norm(kv[w])+1e-9)

def mismatch(t):
    ps=[(u,n) for u,n in co[t].most_common(25)]
    if len(ps)<3: return None
    w=[n*icf[u] for u,n in ps]
    return float(np.average([nrm(t)@nrm(u) for u,_ in ps], weights=w))

cos=lambda a,b: float(nrm(a)@nrm(b))
PAIRS=[("dept","department"),("auth","authentication"),("config","configuration"),
       ("mgr","manager"),("addr","address"),("dict","dictionary"),("pwd","password"),
       ("buf","buffer"),("msg","message"),("impl","implementation"),("cfg","configuration"),
       ("req","request"),("perms","permission"),("scopes","scope"),("vo","object"),
       ("recur","recursive"),("dep","dependency")]
print("门禁1 的 13 对缩写 —— 项目侧那个词的错配分是多少？")
print(f"  {'缩写':<9}{'全称':<16}{'cos':>7}{'项目频次':>9}{'错配分':>9}   微调该怎么处理")
print("  " + "-"*76)
for ab, full in PAIRS:
    if ab not in kv.key_to_index or full not in kv.key_to_index: continue
    m = mismatch(ab); f = freq.get(ab, 0)
    c = cos(ab, full)
    if m is None:
        note = "项目里没有/太少 → 不会被更新，天然安全"
    elif m >= 0.15:
        note = "✅ 已匹配 → 冻结（lockf≈0），门禁1 自动满足"
    else:
        note = "⚠️ 错配 → 放开更新，且微调应**改善** cos"
    print(f"  {ab:<9}{full:<16}{c:>7.3f}{f:>9}"
          f"{(f'{m:.3f}' if m is not None else '—'):>9}   {note}")
