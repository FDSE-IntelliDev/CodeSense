import sys,json
from pathlib import Path
from typing import Dict, List

sys.path.append(str(Path(__file__).parent.parent))

from embedding.pairwise_term_reranker import PairwiseTermReranker, default_paths
from definition import PROJECT_NAME
from embedding.hybrid_term_embedding import HybridTermEmbedding
from embedding.embedding_main import init_embedding,find_relative_terms

def keyword_expand(
    keywords: List[str],
    candidate_k: int = 30,
    co_weight: float = 0.2,
    sem_weight: float = 0.8,
    hybrid_weight: float = 0.0,
    pairwise_weight: float = 1.0,
) -> List[Dict[str, object]]:
    """输入 keyword 列表，输出 embedding 扩写后的结果列表。

    Args:
        keywords: 原始 keyword 列表
        project_name: output 下的项目目录名
        top_k: 每个 keyword 返回的扩写 term 数量
        candidate_k: pairwise 重排前的候选池大小
        co_weight: hybrid 中 co-occurrence 通道权重
        sem_weight: hybrid 中 semantic 通道权重
        hybrid_weight: pairwise 阶段中 hybrid score 权重
        pairwise_weight: pairwise 阶段中 pairwise score 权重

    Returns:
        扩写结果列表，每个元素包含 keyword、expanded_terms 等字段
    """
    embedder=init_embedding(PROJECT_NAME, co_weight=co_weight, sem_weight=sem_weight)

    results = []
    for keyword in keywords:
        terms = find_relative_terms(embedder=embedder,query=keyword)
        results.append({
            'keyword': keyword,
            'expanded_terms': terms,
        })

    return results


print(json.dumps(keyword_expand(["save","department","security"]),indent=2))