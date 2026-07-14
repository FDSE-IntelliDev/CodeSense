"""
Embedding 模块统一入口。

提供三个主要函数：
1. build_corpus()
   - 调用 parallel_build_call_chains.py 构建 word2vec_call_chains.json
   - 调用 build_enhanced_corpus.py 构建 enhanced_call_chain_corpus.json

2. build_and_train()
   - 构建/加载项目词表
   - 构建/加载 ICF
   - 训练/加载 FastText 共现模型
   - 构建/加载 semantic embedding index

3. find_relative_terms()
   - 加载训练好的双通道 + pairwise reranker
   - 输入 query string，返回相关 term
"""

import json
import sys
from functools import lru_cache
from pathlib import Path
from typing import Dict, List

sys.path.append(str(Path(__file__).parent.parent))

from embedding.parallel_build_call_chains import run_parallel
from embedding.build_enhanced_corpus import build_enhanced_corpus, summarize_corpus
from embedding.pairwise_term_reranker import PairwiseTermReranker, default_paths
from embedding.hybrid_term_embedding import HybridTermEmbedding


def build_corpus(
    project_name: str = 'youlai-boot-master',
    num_workers: int = 4,
    window_sizes: List[int] = None,
    full_chain_repeat: int = 1,
    local_window_repeat: int = 2,
    edge_repeat: int = 3,
) -> Dict[str, str]:
    """构建调用链语料。

    Steps:
    1. 通过 LSP 并行抽取调用链，生成 word2vec_call_chains.json
    2. 基于 word2vec_call_chains.json 构建 enhanced_call_chain_corpus.json

    Returns:
        paths: 包含 call_chains / enhanced_corpus 等路径的字典
    """
    if window_sizes is None:
        window_sizes = [2, 3]

    paths = default_paths(project_name)

    print("=== Step 1/2: Build word2vec_call_chains.json ===")
    run_parallel(num_workers=num_workers)

    call_chains_path = paths['call_chains']
    enhanced_corpus_path = paths['enhanced_corpus']

    print("\n=== Step 2/2: Build enhanced_call_chain_corpus.json ===")
    with open(call_chains_path, 'r', encoding='utf-8') as f:
        chains = json.load(f)

    corpus = build_enhanced_corpus(
        chains,
        window_sizes=window_sizes,
        full_chain_repeat=full_chain_repeat,
        local_window_repeat=local_window_repeat,
        edge_repeat=edge_repeat,
    )

    with open(enhanced_corpus_path, 'w', encoding='utf-8') as f:
        json.dump(corpus, f, ensure_ascii=False, indent=2)

    print(f"Enhanced corpus saved to: {enhanced_corpus_path}")
    summarize_corpus(corpus)

    return paths


def build_and_train(
    project_name: str = 'youlai-boot-master',
    co_weight: float = 0.2,
    sem_weight: float = 0.8,
    hybrid_weight: float = 0.0,
    pairwise_weight: float = 1.0,
) -> PairwiseTermReranker:
    """构建索引并训练/加载 embedding 模型。

    该函数会完成：
    - project vocab 构建/加载
    - ICF 构建/加载
    - FastText 训练/加载
    - semantic embedding index 构建/加载

    Pairwise 模型本身不训练，只在查询时加载预训练 cross-encoder。

    Returns:
        已经完成基础索引构建的 PairwiseTermReranker 实例
    """
    paths = default_paths(project_name)

    reranker = PairwiseTermReranker(
        co_weight=co_weight,
        sem_weight=sem_weight,
        hybrid_weight=hybrid_weight,
        pairwise_weight=pairwise_weight,
    )

    reranker.build_base_index(
        paths['call_chains'],
        paths['fasttext_model'],
        paths['icf'],
        paths['semantic_vocab'],
        paths['semantic_embeddings'],
        paths['project_vocab'],
        paths['enhanced_corpus'],
    )

    return reranker


def init_embedding(
        project_name: str = 'youlai-boot-master',
        co_weight: float = 0.2,
        sem_weight: float = 0.8,
        hybrid_weight: float = 0.0,
        pairwise_weight: float = 1.0,

):
    paths = default_paths(project_name)

    embedder = HybridTermEmbedding(
        co_weight=co_weight,
        sem_weight=sem_weight
    )

    embedder.build_index(
        paths['call_chains'],
        paths['fasttext_model'],
        paths['icf'],
        paths['semantic_vocab'],
        paths['semantic_embeddings'],
        paths['project_vocab'],
        paths['enhanced_corpus'],
    )

    embedder.load(
        paths['fasttext_model'],
        paths['icf'],
        paths['semantic_vocab'],
        paths['semantic_embeddings'],
        paths['project_vocab'],
    )
    return embedder

_EMBEDDER = None


def get_embedder() -> HybridTermEmbedding:
    """Lazy-load global embedding model on first use."""
    global _EMBEDDER
    if _EMBEDDER is None:
        _EMBEDDER = init_embedding()
    return _EMBEDDER


@lru_cache(maxsize=20000)
def _score_pair_cached(text_a: str, text_b: str) -> str:
    result = get_embedder().score_pair(text_a, text_b)
    return json.dumps(result, ensure_ascii=False, sort_keys=True)


def find_relative_terms(
    query: str,
    top_k: int = None,
    # embedder: HybridTermEmbedding = None,
) -> List[Dict[str, object]]:

    return get_embedder().find_related_terms(query,top_k)


def find_relative_terms_by_average_vector(
    query: str,
    top_k: int = None,
) -> List[Dict[str, object]]:
    return get_embedder().find_related_terms_by_average_vector(query, top_k)


@lru_cache(maxsize=20000)
def _score_pair_by_average_vector_cached(text_a: str, text_b: str) -> str:
    result = get_embedder().score_pair_by_average_vector(text_a, text_b)
    return json.dumps(result, ensure_ascii=False, sort_keys=True)


def score_pair_by_average_vector(
    text_a: str,
    text_b: str,
) -> Dict[str, object]:
    """对两个短语/短句分别取 FastText 平均向量，再结合 semantic 分数计算相关性。"""
    return json.loads(_score_pair_by_average_vector_cached(str(text_a), str(text_b)))


def score_pair(
    text_a: str,
    text_b: str,
    # embedder: HybridTermEmbedding = None,
) -> Dict[str, object]:
    """计算两个单词语义相似度。

    Args:
        text_a: 第一个术语
        text_b: 第二个术语
        embedder: 已初始化并加载的 HybridTermEmbedding 实例

    Returns:
        包含 co_score / sem_score / final_score 等字段的字典
    """
    return json.loads(_score_pair_cached(str(text_a), str(text_b)))

def main(project_name,query):
    # project_name = 'youlai-boot-master'
    # query="save dept"#输入用tokenizer分词 然后向量平均 检查co_score=0的case

    # build_corpus(project_name=project_name, num_workers=4)
    #
    # build_and_train(
    #     project_name=project_name,
    #     co_weight=0.2,
    #     sem_weight=0.8,
    #     hybrid_weight=0,
    #     pairwise_weight=1,
    # )
    init_embedding(project_name, co_weight=0.2, sem_weight=0.8)

    result = find_relative_terms_by_average_vector(
        query=query,
        top_k=None,
        # embedder=embedder
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))

    result = find_relative_terms(
        query=query,
        top_k=None,
        # embedder=embedder
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
