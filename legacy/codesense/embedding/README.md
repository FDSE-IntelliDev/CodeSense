# Term Embedding Model - 召回通道与判别通道

## 1. 目标

Embedding 模块当前按两个核心通道来理解：

1. **通道一：相关性召回通道**
   - 由 `ICF/FastText` + `Semantic Embedding` 组成
   - 负责从项目词表或候选代码元素中找出“可能相关”的结果
   - 同时覆盖两类相关性：
     - 项目内调用链共现相关性
     - 通用语义相关性

2. **通道二：Pairwise 判别通道**
   - 建立在通道一结果之上
   - 对 `(query, candidate)` 做逐对判断
   - 目标不是召回更多候选，而是过滤通道一结果中的 false positive

也就是说，当前推荐的整体定位是：

```text
通道一负责“找出来”
通道二负责“判别是否真的相关”
```

---

## 2. 文件划分

```text
codesense/embedding/
├── README.md
├── build_call_chains.py
├── parallel_build_call_chains.py
├── build_enhanced_corpus.py       # 构建增强调用链 corpus
├── project_term_vocab.py          # 项目统一术语表
├── icf_term_embedding.py          # 通道一组成部分：共现相关性
├── semantic_term_embedding.py     # 通道一组成部分：语义相关性
├── hybrid_term_embedding.py       # 通道一：共现 + 语义融合
├── pairwise_term_reranker.py      # 通道二：pairwise 判别/重排
└── embedding_main.py              # embedding 模块统一入口
```

---

## 3. 通道一：相关性召回通道

通道一由两个子通道组成：

### 3.1 共现子通道：ICF + FastText

文件：

```text
codesense/embedding/icf_term_embedding.py
```

职责：
- 从 `word2vec_call_chains.json` 中抽取项目术语
- 计算 ICF（Inverse Chain Frequency）
- 用 `enhanced_call_chain_corpus.json` 训练 FastText
- 学习项目内调用链中的共现相关性

它回答的问题是：

```text
在当前项目调用链上下文中，哪些 term 经常处在相近调用关系里？
```

例如：
- `auth` 和 `token` 可能共现相关
- `save` 和 `delete` 可能因为 CRUD 流程而共现相关

注意：这不是严格的语义相似度，而是项目内结构/流程相关性。

#### TODO：短语/短句 query 的共现召回优化

当前 ICF/FastText 共现子通道主要是 token-level recall：

```text
query = "save dept"
  ↓ tokenizer
["save", "dept"]
  ↓
分别用 save、dept 去查 FastText most_similar
  ↓
合并候选
```

这种方式召回强，但容易把单个 token 的噪声带进来。后续可以增加一条 **phrase-vector recall**：

```text
query = "save dept"
  ↓ tokenizer
["save", "dept"]
  ↓
取 save、dept 的 FastText embedding 平均向量
  ↓
用平均向量查 most_similar
```

这样可以让共现通道同时具备两种召回能力：

- **token-level recall**：保证每个关键词都有机会召回相关 term
- **phrase-vector recall**：让短语/短句作为整体在项目共现空间中查找相关 term

建议后续只在 query 分词后包含多个有效 token 时启用 phrase-vector recall。单词 query 下平均向量等价于自身，没有额外收益。

---

文件：

```text
codesense/embedding/semantic_term_embedding.py
```

职责：
- 使用 `sentence-transformers/all-MiniLM-L6-v2`
- 对项目术语表构建 semantic embedding index
- 学习术语字符串本身的语义相关性

它回答的问题是：

```text
从通用语义角度看，query 和 candidate 是否语义接近？
```

例如：
- `auth` 和 `authenticate` 语义接近
- `user` 和 `users` 语义接近

### 3.3 通道一融合：Hybrid

文件：

```text
codesense/embedding/hybrid_term_embedding.py
```

职责：
- 组合共现子通道和语义子通道
- 输出候选 term 及其：
  - `co_score`
  - `sem_score`
  - `final_score`

默认可配置权重：

```python
co_weight = 0.2
sem_weight = 0.8
```

因此通道一可以理解为：

```text
co-occurrence relevance + semantic relevance
```

它主要用于**召回可能相关的候选**，而不是最终判定候选一定正确。

---

## 4. 通道二：Pairwise 判别通道

文件：

```text
codesense/embedding/pairwise_term_reranker.py
```

Pairwise 通道建立在通道一结果之上：

```text
query A
  ↓
通道一找到候选 B / C / D / ...
  ↓
构造 (A,B)、(A,C)、(A,D)
  ↓
Pairwise 模型逐对判断
  ↓
过滤或压低 false positive
```

默认模型：

```text
cross-encoder/stsb-distilroberta-base
```

它回答的问题是：

```text
query 和 candidate 这一对，是否真的应该被认为相关？
```

这和通道一不同：

| 阶段 | 作用 |
|---|---|
| 通道一 Hybrid | 召回候选，宁可多召一些 |
| 通道二 Pairwise | 对候选做判别，去除 false positive |

### 例子

通道一可能因为主题相近召回：

```text
save -> delete
```

但 pairwise 阶段可以进一步判断：

```text
(save, delete) 虽然同属 CRUD，但方向相反，不应排太高
```

因此 Pairwise 的定位不是替代通道一，而是对通道一的候选做二阶段过滤和重排。

---

## 5. 当前整体流程

```text
query string
  ↓
[通道一 - Semantic]
  粗召回语义相关候选
  ↓
[通道一 - ICF/FastText]
  补充项目内共现相关性
  ↓
[通道一 - Hybrid]
  生成候选池，并保留 co_score / sem_score / hybrid_score
  ↓
[通道二 - Pairwise]
  对 (query, candidate) 做逐对判别
  ↓
最终 top-N term results
```

一句话总结：

```text
Hybrid 用来召回，Pairwise 用来判别。
```

---

## 6. 离线产物

```text
output/youlai-boot-master/
├── word2vec_call_chains.json
├── enhanced_call_chain_corpus.json
├── term_project_vocab.json
├── term_icf_fasttext.model
├── term_icf.npz
└── term_semantic_embeddings.npz
```

说明：
- `word2vec_call_chains.json`：原始调用链数据，用于构建项目词表和 ICF
- `enhanced_call_chain_corpus.json`：增强训练语料，用于训练 FastText 共现模型
- `term_project_vocab.json`：项目统一术语表，供 semantic / co-occurrence 共享
- `term_icf_fasttext.model`：共现子通道模型
- `term_icf.npz`：ICF 数据和高频词集合
- `term_semantic_embeddings.npz`：语义向量矩阵

---

## 7. 词表复用策略

项目术语表是独立于具体通道的底层产物：

- 如果 `term_project_vocab.json` 已存在，semantic / co-occurrence 都优先读取它
- 如果不存在，则从 `word2vec_call_chains.json` 中抽取术语并保存
- semantic embedding matrix 的行顺序直接与 `term_project_vocab.json` 对齐

这样可以避免 semantic 通道为了拿词表而依赖 ICF 逻辑，也避免多个通道各自生成不一致的词表。

---

## 8. 使用方法

### 8.1 构建 corpus

```bash
python codesense/embedding/embedding_main.py --build-corpus
```

该步骤会：
1. 构建 `word2vec_call_chains.json`
2. 构建 `enhanced_call_chain_corpus.json`

### 8.2 构建并训练通道一所需索引

```bash
python codesense/embedding/embedding_main.py --build-and-train
```

该步骤会：
- 构建/加载项目词表
- 构建/加载 ICF
- 训练/加载 FastText
- 构建/加载 semantic embeddings

### 8.3 查询相关 term

```bash
python codesense/embedding/embedding_main.py --query "security"
```

默认会走：

```text
Hybrid 召回 + Pairwise 判别
```

---

## 9. 接口说明

### 通道一：Hybrid

```python
from embedding.hybrid_term_embedding import HybridTermEmbedding

embedder = HybridTermEmbedding(co_weight=0.2, sem_weight=0.8)
results = embedder.find_related_terms("security")
```

返回字段包括：
- `term`
- `co_score`
- `sem_score`
- `final_score`
- `rank_source`
- `is_high_freq`
- `icf`

### 通道二：Pairwise

```python
from embedding.pairwise_term_reranker import PairwiseTermReranker

reranker = PairwiseTermReranker(
    co_weight=0.2,
    sem_weight=0.8,
    hybrid_weight=0.0,
    pairwise_weight=1.0,
)
results = reranker.find_related_terms("security")
```

这里推荐将：

```python
hybrid_weight = 0.0
pairwise_weight = 1.0
```

理解为：
- Hybrid 只负责召回候选
- Pairwise 负责最终判别和排序

---

## 10. `resolved_term` 说明

部分接口会返回：

- `resolved_term_a`
- `resolved_term_b`
- `a_in_project_vocab`
- `b_in_project_vocab`

这里的 `resolve` 不是语义推理，而是：

```text
把原始输入字符串尽量映射到项目词表里的标准术语
```

它主要用于：

1. 判断共现通道能不能算分
2. 提供可解释性信息

如果输入没有在项目里出现：
- 共现通道可能无法计算 `co_score`
- semantic / pairwise 仍然可以直接对字符串计算分数

---

## 11. TODO：Semantic 表示增强

当前 semantic / pairwise 主要依赖“术语字符串本身”。

后续可以考虑为每个代码元素或 term 提供更多相关语料一起做 embedding，例如：

- 语义标签
- schema 中的字段说明
- 与代码元素绑定的描述性文本
- 其它结构化属性

目标是让 semantic 相似度不只看裸词，而是看：

```text
词 + 语义标签 + schema 内容 + 额外描述语料
```

这样可以进一步提升：
- 裸词语义不足时的表达能力
- domain-specific 语义一致性
- pairwise 阶段的可判别性

---

## 12. 当前设计总结

| 通道 | 文件 | 作用 |
|---|---|---|
| 通道一-共现 | `icf_term_embedding.py` | 项目内调用链共现相关性 |
| 通道一-语义 | `semantic_term_embedding.py` | 通用语义相关性 |
| 通道一-Hybrid | `hybrid_term_embedding.py` | 召回候选，融合 co/sem score |
| 通道二-Pairwise | `pairwise_term_reranker.py` | 判别候选，过滤 false positive |

最终理解：

```text
ICF + Semantic = 相关性召回通道
Pairwise = 判别通道 / false positive filter
```