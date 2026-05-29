# Term Embedding Model - 单通道与双通道术语相似度模型

## 1. 目标

当前在 `embedding/` 目录下提供三套可独立测试的 term-level 模型：

1. **共现单通道**：`FastText + ICF`
2. **语义单通道**：`SentenceTransformer`
3. **双通道融合**：共现分数 + 语义分数

这样可以分别测试：
- 纯共现能力
- 纯语义能力
- 融合后的最终效果

并且每个通道都提供两类接口：

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
└── hybrid_term_embedding.py      # 双通道：融合模型
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

### TODO：Semantic 通道后续优化方向

当前 semantic channel 仍然主要是一个“单向量语义相关度”模型，因此会出现一些典型问题：

- 反义词或对立动作词（如 `save` / `delete`）可能因为主题接近而相似度偏高
- 某些开发里更相近的动作词（如 `save` / `update`）不一定比反义词更近

后续可以考虑两条优化路线：

1. **增加 pairwise 阶段**
   - 保留当前 semantic embedding 作为第一阶段粗召回
   - 再增加一个 pairwise scorer，对 `(A, B)` 这种词对做二阶段判断/重排
   - 目标是区分：
     - 语义相近 / 可替代
     - 主题相关但方向相反
     - 仅仅共现接近

2. **提供更多相关语料一起做 semantic 表示**
   - 当前 semantic channel 主要对“术语字符串本身”编码
   - 后续如果每个代码元素增加了语义标签、schema 字段或其它结构化属性
   - 可以考虑把这些额外信息作为 term 的上下文语料一起编码
   - 例如：
     - 语义标签
     - schema 中的字段说明
     - 与代码元素绑定的描述性文本
   - 目标是让 semantic 相似度不只看裸词，而是看“词 + 语义上下文”

这两条路线可以独立推进，也可以组合使用：
- 先通过“更多语料”改善 term embedding 本身
- 再通过 pairwise 阶段做更细粒度的关系判断

---

## 6. 离线产物

```text
output/youlai-boot-master/
├── word2vec_call_chains.json
├── term_icf_fasttext.model
├── term_icf.npz
├── term_semantic_vocab.json
└── term_semantic_embeddings.npz
```

说明：
- `term_icf_fasttext.model`：共现单通道模型
- `term_icf.npz`：ICF 数据和高频词集合
- `term_semantic_vocab.json`：语义单通道使用的项目术语表
- `term_semantic_embeddings.npz`：语义向量矩阵

---

## 7. 使用方法

### 7.1 训练共现单通道

```bash
python embedding/icf_term_embedding.py --train
```

### 7.2 构建语义单通道索引

```bash
python embedding/semantic_term_embedding.py --train
```

### 7.3 构建双通道索引

```bash
python embedding/hybrid_term_embedding.py --train
```

注意：双通道构建会调用共现单通道训练，以及语义单通道的索引构建过程。

---

## 8. 查询接口示例

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

---

## 9. 返回结果说明

### `find_related_terms(...)`
各文件会返回 term 列表，字段可能包括：

- `term`
- `co_score`（仅共现 / 双通道）
- `sem_score`（仅语义 / 双通道）
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

语义单通道 / 双通道里的 `sem_score` 是直接对输入字符串编码后计算的：

- 只要 sentence-transformer 能编码这个字符串
- 即使它没有在项目词表中出现，也仍然可以计算 semantic similarity

也就是说：

- **共现通道是否可用**：取决于能否 resolve 到项目 term
- **语义通道是否可用**：不依赖是否在项目词表里出现

所以常见情况会是：

- `signin` 不在项目词表里 → `co_score = 0`
- 但 semantic model 仍可计算 `signin` 和 `login` 的相似度 → `sem_score > 0`

---

## 10. 推荐测试方式

建议分别对三套模型跑这些例子：

### 正样本
- `auth` / `authenticate`
- `user` / `users`
- `token` / `auth`

### 负样本
- `get` / `role`
- `save` / `delete`

比较：
- 单通道共现输出
- 单通道语义输出
- 双通道融合输出

这样能清楚看到：
- 哪些词是共现拉起来的
- 哪些词是语义拉起来的
- 融合后最终效果是否更稳
