# Term Embedding Model - 单通道、双通道与 Pairwise 重排

## 1. 目标

当前在 `embedding/` 目录下提供四层可独立测试的 term-level 模型/阶段：

1. **共现单通道**：`FastText + ICF`
2. **语义单通道**：`SentenceTransformer`
3. **双通道融合**：语义分数 + 共现分数
4. **Pairwise 重排**：在双通道候选之上，对 `(query, candidate)` 词对做二阶段判断

这样可以分别测试：
- 纯共现能力
- 纯语义能力
- 融合后的最终效果
- 在融合候选上再进行 pairwise 重排后的效果

并且每一层都尽量提供两类接口：

- 输入一个 `string`，返回项目内最相关的 top-N 个词
- 输入 `string A` 和 `string B`，返回二者的相似度得分

---

## 2. 文件划分

```text
embedding/
├── README.md
├── build_call_chains.py
├── parallel_build_call_chains.py
├── icf_term_embedding.py         # 单通道：FastText + ICF
├── semantic_term_embedding.py    # 单通道：Semantic only
├── hybrid_term_embedding.py      # 双通道：语义 + 共现融合
└── pairwise_term_reranker.py     # 第三阶段：pairwise 重排
```

---

## 3. 单通道一：FastText + ICF

文件：

```text
embedding/icf_term_embedding.py
```

职责：
- 从 `word2vec_call_chains.json` 中抽取项目术语
- 训练 FastText 共现模型
- 计算 ICF（Inverse Chain Frequency）
- 对高频词之间的伪相关做惩罚

### 高频词定义
当前不使用固定阈值，而是：

- 统计每个术语覆盖多少条调用链
- 按调用链覆盖率排序
- 取前 10% 作为高频词集合

### 提供接口

```python
find_related_terms(query: str, top_k: int = 10)
score_pair(text_a: str, text_b: str)
```

### 适合测试什么
- 项目内调用链共现相关性
- 高频词惩罚是否有效
- 纯共现模型的局限在哪里

---

## 4. 单通道二：Semantic only

文件：

```text
embedding/semantic_term_embedding.py
```

职责：
- 从项目调用链中抽取项目术语表
- 为项目术语预计算 sentence-transformer 向量
- 仅基于语义相似度返回 top-N 相关词或 A/B 相似度

默认模型：

```text
all-MiniLM-L6-v2
```

### 提供接口

```python
find_related_terms(query: str, top_k: int = 10)
score_pair(text_a: str, text_b: str)
```

### 适合测试什么
- 语义模型本身是否能把语义接近的词拉近
- 对 OOV / 弱共现场景的处理能力
- 与共现模型结果的差异

---

## 5. 双通道：Hybrid

文件：

```text
embedding/hybrid_term_embedding.py
```

职责：
- 组合共现单通道与语义单通道
- 为每个候选词同时计算：
  - `co_score`
  - `sem_score`
  - `final_score`

### 当前融合方式

```text
final_score = 0.6 * sem_score + 0.4 * co_score
```

实现中：
- 若候选只来自一个通道，则按已有通道权重归一化
- 若两个通道都命中，则按上述公式融合

### 提供接口

```python
find_related_terms(query: str, top_k: int = 10)
score_pair(text_a: str, text_b: str)
```

### 适合测试什么
- 语义与共现融合后是否更符合直觉
- 高频共现噪声是否下降
- 是否能同时保留项目内命名习惯和语义相近性

---

## 6. 第三阶段：Pairwise 重排

文件：

```text
embedding/pairwise_term_reranker.py
```

职责：
- 先使用双通道模型召回候选词
- 再对 `(query, candidate)` 词对做 pairwise 打分
- 用 pairwise 分数对双通道结果进行重排

默认 pairwise 模型：

```text
cross-encoder/stsb-distilroberta-base
```

### 当前融合方式

```text
final_score = 0.6 * pair_score + 0.4 * hybrid_score
```

### 提供接口

```python
find_related_terms(query: str, top_k: int = 10)
score_pair(text_a: str, text_b: str)
```

### 它解决什么问题
当前 semantic 单向量模型容易把“主题相近但方向相反”的词拉近，例如：
- `save` / `delete`

Pairwise 重排阶段的作用是：
- 在双通道已经召回的候选上，进一步判断 `(A, B)` 是否真的更接近
- 区分：
  - 真正语义相近 / 可替代
  - 主题相关但方向相反
  - 仅仅共现接近

---

## 7. 当前整体流程

当前推荐流程如下：

```text
query string
  ↓
[第一阶段] semantic 单通道召回
  - 提供语义相近候选
  - 对 OOV / 弱共现场景更友好
  ↓
[第二阶段] co-occurrence 单通道打分
  - 用 FastText + ICF 引入项目内调用链共现信息
  - 修正 purely semantic 结果中缺失的项目特定相关性
  ↓
[第二阶段输出] hybrid 双通道融合
  - semantic score + co score -> hybrid score
  ↓
[第三阶段] pairwise rerank
  - 对 (query, candidate) 逐对重排
  - 进一步区分“语义相近”和“主题接近但方向相反”
  ↓
最终 top-N term results
```

换句话说，当前设计是：

- **semantic**：负责粗召回语义候选
- **co-occurrence**：负责补项目内共现结构信号
- **pairwise**：负责最终判别和重排

---

## 8. TODO：Semantic 通道后续优化方向

当前 semantic / pairwise 模块虽然已经引入，但 semantic 表示本身仍然主要依赖“术语字符串本身”。

后续仍保留一个重要优化方向：

### 提供更多相关语料一起做 semantic 表示

如果未来每个代码元素增加了更多结构化或语义化信息，可以考虑把这些额外内容一起作为 term 的上下文语料：

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

## 9. 离线产物

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
- `term_project_vocab.json`：项目术语表，作为 semantic / co-occurrence 的共享底层词表，也作为语义向量矩阵的行顺序表
- `term_icf_fasttext.model`：共现单通道模型
- `term_icf.npz`：ICF 数据和高频词集合
- `term_semantic_embeddings.npz`：语义向量矩阵

### 词表复用策略

项目术语表是独立于具体通道的底层产物：

- 如果 `term_project_vocab.json` 已存在，semantic / co-occurrence 都优先读取它
- 如果不存在，则从 `word2vec_call_chains.json` 中抽取术语并保存
- semantic embedding matrix 的行顺序直接与 `term_project_vocab.json` 对齐


---

## 10. 使用方法

### 10.1 构建增强调用链 corpus

```bash
python embedding/build_enhanced_corpus.py
```

该步骤从 `word2vec_call_chains.json` 生成 `enhanced_call_chain_corpus.json`，供 ICF/FastText 共现通道训练使用。

### 10.2 训练共现单通道

```bash
python embedding/icf_term_embedding.py --train
```

### 10.3 构建语义单通道索引

```bash
python embedding/semantic_term_embedding.py --build
```

### 10.4 构建双通道索引

```bash
python embedding/hybrid_term_embedding.py --train
```

注意：双通道构建会调用共现单通道训练，以及语义单通道的索引构建过程。

### 10.5 构建 pairwise 所需基础索引

```bash
python embedding/pairwise_term_reranker.py --build
```

注意：pairwise 本身主要是在线重排，这里的 `--build` 只是触发其依赖的 hybrid 基础索引构建。

---

## 11. 查询接口示例

### 共现单通道：查 top-N

```bash
python embedding/icf_term_embedding.py --related "auth" --top-k 10
```

### 共现单通道：查 A/B 相似度

```bash
python embedding/icf_term_embedding.py --pair-a "auth" --pair-b "authenticate"
```

### 语义单通道：查 top-N

```bash
python embedding/semantic_term_embedding.py --related "auth" --top-k 10
```

### 语义单通道：查 A/B 相似度

```bash
python embedding/semantic_term_embedding.py --pair-a "auth" --pair-b "authenticate"
```

### 双通道：查 top-N

```bash
python embedding/hybrid_term_embedding.py --related "auth" --top-k 10
```

### 双通道：查 A/B 相似度

```bash
python embedding/hybrid_term_embedding.py --pair-a "auth" --pair-b "authenticate"
```

### Pairwise：查 top-N

```bash
python embedding/pairwise_term_reranker.py --related "auth" --top-k 10
```

### Pairwise：查 A/B 相似度

```bash
python embedding/pairwise_term_reranker.py --pair-a "save" --pair-b "update"
```

---

## 12. 返回结果说明

### `find_related_terms(...)`
各文件会返回 term 列表，字段可能包括：

- `term`
- `co_score`（仅共现 / 双通道 / pairwise）
- `sem_score`（仅语义 / 双通道 / pairwise）
- `hybrid_score`（仅 pairwise）
- `pair_score`（仅 pairwise）
- `final_score`
- `rank_source`
- `is_high_freq`
- `icf`

### `score_pair(...)`
各文件会返回：

- `text_a`
- `text_b`
- `resolved_term_a`
- `resolved_term_b`
- `a_in_project_vocab`
- `b_in_project_vocab`
- 通道对应分数
- `final_score`
- `rank_source`

#### `resolved_term_a` / `resolved_term_b` 是什么

这里的 `resolve` 不是在做语义推理，而是在做：

- **把原始输入字符串尽量映射到“项目词表里的标准术语”**

例如：
- 输入 `"Auth"`，可能 resolve 成 `"auth"`
- 输入 `"authenticate"`，如果它本来就在项目词表里，就 resolve 成自身
- 如果输入无法稳定映射到项目中的某个 term，则返回 `None`

它的主要作用是：

1. **判断共现通道能不能算分**
   - 共现通道依赖项目内的 FastText 词表
   - 只有当输入能 resolve 到项目 term 时，才能稳定计算 `co_score`

2. **提供可解释性信息**
   - 告诉你输入字符串是否真的命中了项目内部术语
   - 帮助区分“项目内 term”与“仅通过语义模型比较的输入字符串”

#### 如果输入没有在项目里出现，会影响 semantic 相似度吗？

**不会。**

语义单通道 / 双通道 / pairwise 阶段里的 semantic 分数，本质上都可以直接对输入字符串编码后计算：

- 只要 sentence-transformer / cross-encoder 能处理这个字符串
- 即使它没有在项目词表中出现，也仍然可以计算 semantic / pairwise similarity

也就是说：

- **共现通道是否可用**：取决于能否 resolve 到项目 term
- **语义通道是否可用**：不依赖是否在项目词表里出现
- **pairwise 阶段是否可用**：也不依赖是否在项目词表里出现，但它通常建立在已有候选集之上做重排

所以常见情况会是：

- `signin` 不在项目词表里 → `co_score = 0`
- semantic model 仍可计算 `signin` 和 `login` 的相似度 → `sem_score > 0`
- pairwise model 也仍可比较 `(signin, login)` 这一对 → `pair_score > 0`

---

## 13. 推荐测试方式

建议分别对四套阶段/模型跑这些例子：

### 正样本
- `auth` / `authenticate`
- `user` / `users`
- `token` / `auth`
- `save` / `update`

### 负样本
- `get` / `role`
- `save` / `delete`

比较：
- 单通道共现输出
- 单通道语义输出
- 双通道融合输出
- pairwise 重排输出

这样能清楚看到：
- 哪些词是共现拉起来的
- 哪些词是语义拉起来的
- 哪些词在 pairwise 阶段被重新拉开
- 最终结果是否更符合开发语义直觉
