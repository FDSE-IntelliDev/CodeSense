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
from pathlib import Path
from typing import Dict, List

sys.path.append(str(Path(__file__).parent.parent))

from embedding.parallel_build_call_chains import run_parallel
from embedding.build_enhanced_corpus import build_enhanced_corpus, summarize_corpus
from embedding.pairwise_term_reranker import PairwiseTermReranker, default_paths


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


def find_relative_terms(
    query: str,
    project_name: str = 'youlai-boot-master',
    top_k: int = 10,
    candidate_k: int = 30,
    co_weight: float = 0.2,
    sem_weight: float = 0.8,
    hybrid_weight: float = 0.0,
    pairwise_weight: float = 1.0,
) -> List[Dict[str, object]]:
    """加载训练好的双通道 embedding 模型，返回 query 相关 term。

    Args:
        query: 输入查询字符串
        project_name: output 下的项目目录名
        top_k: 最终返回 term 数量
        candidate_k: pairwise rerank 前的 hybrid 候选池大小
        co_weight: hybrid 中 co-occurrence 通道权重
        sem_weight: hybrid 中 semantic 通道权重
        hybrid_weight: pairwise 阶段中 hybrid score 权重
        pairwise_weight: pairwise 阶段中 pairwise score 权重

    Returns:
        reranker.find_related_terms(...) 的结果
    """
    paths = default_paths(project_name)

    reranker = PairwiseTermReranker(
        co_weight=co_weight,
        sem_weight=sem_weight,
        hybrid_weight=hybrid_weight,
        pairwise_weight=pairwise_weight,
    )

    reranker.load(
        paths['fasttext_model'],
        paths['icf'],
        paths['semantic_vocab'],
        paths['semantic_embeddings'],
        paths['project_vocab'],
    )

    return reranker.find_related_terms(query, top_k, candidate_pool_k=candidate_k)


def main():
    project_name = 'youlai-boot-master'
    query="save dept"

    # build_corpus(project_name=project_name, num_workers=4)
    #
    # build_and_train(
    #     project_name=project_name,
    #     co_weight=0.2,
    #     sem_weight=0.8,
    #     hybrid_weight=0,
    #     pairwise_weight=1,
    # )

    result = find_relative_terms(
        query=query,
        project_name=project_name,
        top_k=10,
        candidate_k=30,
        co_weight=0.2,
        sem_weight=0.8,
        hybrid_weight=0,
        pairwise_weight=1,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
