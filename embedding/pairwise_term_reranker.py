"""
Pairwise term reranker.

职责：
- 使用现有 hybrid term model 先召回候选词
- 对 (query, candidate) 词对做 pairwise 打分
- 输出 pairwise 重排后的 top-N 相关词
- 提供 A/B 词对相似度接口

说明：
- 该模块不替代现有单通道/双通道模型
- 它是建立在语义 + 共现融合结果之上的第三阶段 reranker
"""

import json
import math
import sys
from pathlib import Path
from typing import Dict, List, Tuple

sys.path.append(str(Path(__file__).parent.parent))
from definition import OUTPUT_DIR, CORPUS
from embedding.hybrid_term_embedding import HybridTermEmbedding

try:
    from sentence_transformers.cross_encoder import CrossEncoder
    CROSS_ENCODER_AVAILABLE = True
except ImportError:
    CROSS_ENCODER_AVAILABLE = False


class PairwiseTermReranker:
    DEFAULT_PAIRWISE_MODEL = 'cross-encoder/stsb-distilroberta-base'

    def __init__(
        self,
        pairwise_model_name: str = DEFAULT_PAIRWISE_MODEL,
        co_weight: float = 0.4,
        sem_weight: float = 0.6,
        hybrid_weight: float = 0.4,
        pairwise_weight: float = 0.6,
    ):
        self.hybrid_model = HybridTermEmbedding(co_weight=co_weight, sem_weight=sem_weight)
        self.pairwise_model_name = pairwise_model_name
        self.pairwise_model = None
        self.hybrid_weight = hybrid_weight
        self.pairwise_weight = pairwise_weight

    def _ensure_pairwise_model(self):
        if self.pairwise_model is not None:
            return self.pairwise_model
        if not CROSS_ENCODER_AVAILABLE:
            raise ImportError('sentence-transformers cross encoder is required. Install: pip install sentence-transformers')
        self.pairwise_model = CrossEncoder(self.pairwise_model_name)
        return self.pairwise_model

    def _normalize_pair_score(self, raw_score: float) -> float:
        score = float(raw_score)
        if 0.0 <= score <= 1.0:
            return score
        if 0.0 <= score <= 5.0:
            return score / 5.0
        return 1.0 / (1.0 + math.exp(-score))

    def _score_pairwise_only(self, text_a: str, text_b: str) -> float:
        model = self._ensure_pairwise_model()
        raw = model.predict([(text_a, text_b)])
        if isinstance(raw, list):
            raw_score = raw[0]
        else:
            raw_score = raw[0] if hasattr(raw, '__len__') else raw
        return max(0.0, self._normalize_pair_score(float(raw_score)))

    def _fuse_scores(self, hybrid_score: float, pair_score: float) -> Tuple[float, str]:
        total = 0.0
        sources = []

        if hybrid_score > 0:
            total += self.hybrid_weight * hybrid_score
            sources.append('hybrid')
        if pair_score > 0:
            total += self.pairwise_weight * pair_score
            sources.append('pairwise')

        return total, '_and_'.join(sources)

    def build_base_index(
        self,
        call_chains_path: str,
        co_model_path: str,
        icf_path: str,
        semantic_vocab_path: str,
        semantic_embeddings_path: str,
        project_vocab_path: str = None,
        enhanced_corpus_path: str = None,
    ):
        self.hybrid_model.build_index(
            call_chains_path,
            co_model_path,
            icf_path,
            semantic_vocab_path,
            semantic_embeddings_path,
            project_vocab_path,
            enhanced_corpus_path,
        )

    def load(
        self,
        co_model_path: str,
        icf_path: str,
        semantic_vocab_path: str,
        semantic_embeddings_path: str,
        project_vocab_path: str = None,
    ):
        self.hybrid_model.load(
            co_model_path,
            icf_path,
            semantic_vocab_path,
            semantic_embeddings_path,
            project_vocab_path,
        )

    def _compute_top_k(self, query: str, min_k: int = 3, max_k: int = 10) -> int:
        resolved = self.hybrid_model.co_model._resolve_project_term(query)
        if resolved is None:
            return max_k

        chain_count = len(self.hybrid_model.co_model.icf_calc.term_in_chains.get(resolved, set()))
        total_chains = self.hybrid_model.co_model.icf_calc.total_chains

        if total_chains <= 0:
            return max_k

        max_chain_count = 0
        for chains in self.hybrid_model.co_model.icf_calc.term_in_chains.values():
            if len(chains) > max_chain_count:
                max_chain_count = len(chains)

        if max_chain_count <= 0:
            return max_k

        relative = chain_count / max_chain_count

        if relative >= 0.5:
            return max_k
        if relative >= 0.25:
            return 8
        if relative >= 0.1:
            return 5
        return min_k

    def find_related_terms(self, query: str, top_k: int = None, candidate_pool_k: int = 30) -> List[Dict[str, object]]:
        if top_k is None:
            top_k = self._compute_top_k(query)
        hybrid_candidates = self.hybrid_model.find_related_terms(query, top_k=max(candidate_pool_k, top_k * 3))
        print(json.dumps(hybrid_candidates[:top_k], indent=2))
        print("="*20)
        reranked = []
        for item in hybrid_candidates:
            pair_score = self._score_pairwise_only(query, item['term'])
            final_score, rank_source = self._fuse_scores(float(item.get('final_score', 0.0)), pair_score)
            reranked.append({
                **item,
                'hybrid_score': item.get('final_score', 0.0),
                'pair_score': round(pair_score, 6),
                'final_score': round(final_score, 6),
                'rank_source': rank_source,
            })

        reranked.sort(key=lambda item: item['final_score'], reverse=True)
        return reranked[:top_k]

    # def score_pair(self, text_a: str, text_b: str) -> Dict[str, object]:
    #     hybrid_result = self.hybrid_model.score_pair(text_a, text_b)
    #     pair_score = self._score_pairwise_only(text_a, text_b)
    #     final_score, rank_source = self._fuse_scores(float(hybrid_result.get('final_score', 0.0)), pair_score)
    #     return {
    #         **hybrid_result,
    #         'hybrid_score': hybrid_result.get('final_score', 0.0),
    #         'pair_score': round(pair_score, 6),
    #         'final_score': round(final_score, 6),
    #         'rank_source': rank_source,
    #     }

def default_paths(project_name: str = 'youlai-boot-master') -> Dict[str, str]:
    base = Path(OUTPUT_DIR) / project_name
    return {
        'project_vocab': str(base / 'term_project_vocab.json'),
        'call_chains': str(base / 'word2vec_call_chains.json'),
        'enhanced_corpus': str(base / CORPUS),
        'fasttext_model': str(base / 'term_icf_fasttext.model'),
        'icf': str(base / 'term_icf.npz'),
        'semantic_vocab': str(base / 'term_project_vocab.json'),
        'semantic_embeddings': str(base / 'term_semantic_embeddings.npz'),
    }


if __name__ == '__main__':

    paths = default_paths('youlai-boot-master')
    reranker = PairwiseTermReranker(
        co_weight=0.2,
        sem_weight=0.8,
        hybrid_weight=0,
        pairwise_weight=1,
    )
    query="security"
    top_k=None
    candidate_k=30

    # if args.build:
    reranker.build_base_index(
            paths['call_chains'],
            paths['fasttext_model'],
            paths['icf'],
            paths['semantic_vocab'],
            paths['semantic_embeddings'],
            paths['project_vocab'],
            paths['enhanced_corpus'],
        )
    # elif args.related or (args.pair_a and args.pair_b):
    reranker.load(
            paths['fasttext_model'],
            paths['icf'],
            paths['semantic_vocab'],
            paths['semantic_embeddings'],
            paths['project_vocab'],
        )
        # if args.related:
    print(json.dumps(reranker.find_related_terms(query, top_k, candidate_pool_k=candidate_k), ensure_ascii=False, indent=2))
        # if args.pair_a and args.pair_b:
