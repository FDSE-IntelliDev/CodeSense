"""
双通道术语 Embedding：Hybrid

职责：
- 组合单通道共现模型与单通道语义模型
- 提供两个接口：
  1. find_related_terms(query, top_k)
  2. score_pair(text_a, text_b)
"""

import json
import sys
from pathlib import Path
from typing import Dict, List, Tuple

sys.path.append(str(Path(__file__).parent.parent))
from definition import OUTPUT_DIR
from embedding.icf_term_embedding import ICFTermEmbedding
from embedding.semantic_term_embedding import SemanticTermEmbedding


class HybridTermEmbedding:
    CO_WEIGHT = 0.4
    SEM_WEIGHT = 0.6

    def __init__(self, semantic_model_name: str = SemanticTermEmbedding.DEFAULT_SEMANTIC_MODEL):
        self.co_model = ICFTermEmbedding()
        self.sem_model = SemanticTermEmbedding(semantic_model_name=semantic_model_name)

    def _fuse_scores(self, co_score: float, sem_score: float) -> Tuple[float, str]:
        available_weight = 0.0
        total = 0.0
        sources = []

        if sem_score > 0:
            total += self.SEM_WEIGHT * sem_score
            available_weight += self.SEM_WEIGHT
            sources.append('semantic')
        if co_score > 0:
            total += self.CO_WEIGHT * co_score
            available_weight += self.CO_WEIGHT
            sources.append('co')

        if available_weight <= 0:
            return 0.0, 'none'
        return total / available_weight, '_and_'.join(sources)

    def build_index(
        self,
        call_chains_path: str,
        co_model_path: str,
        icf_path: str,
        semantic_vocab_path: str,
        semantic_embeddings_path: str,
    ):
        self.co_model.train(call_chains_path, co_model_path, icf_path)
        self.sem_model.build_index(call_chains_path, semantic_vocab_path, semantic_embeddings_path)

    def load(
        self,
        co_model_path: str,
        icf_path: str,
        semantic_vocab_path: str,
        semantic_embeddings_path: str,
    ):
        self.co_model.load(co_model_path, icf_path)
        self.sem_model.load(semantic_vocab_path, semantic_embeddings_path)

    def find_related_terms(self, query: str, top_k: int = 10) -> List[Dict[str, object]]:
        co_results = self.co_model.find_related_terms(query, top_k=max(20, top_k * 3))
        sem_results = self.sem_model.find_related_terms(query, top_k=max(20, top_k * 3))

        co_map = {item['term']: item for item in co_results}
        sem_map = {item['term']: item for item in sem_results}

        all_terms = set(co_map.keys()) | set(sem_map.keys())
        results = []
        for term in all_terms:
            co_score = float(co_map.get(term, {}).get('co_score', 0.0))
            sem_score = float(sem_map.get(term, {}).get('sem_score', 0.0))
            final_score, rank_source = self._fuse_scores(co_score, sem_score)
            if final_score <= 0:
                continue
            results.append({
                'term': term,
                'co_score': round(co_score, 6),
                'sem_score': round(sem_score, 6),
                'final_score': round(final_score, 6),
                'rank_source': rank_source,
                'is_high_freq': bool(co_map.get(term, {}).get('is_high_freq', False)),
                'icf': co_map.get(term, {}).get('icf'),
            })

        results.sort(key=lambda item: item['final_score'], reverse=True)
        return results[:top_k]

    def score_pair(self, text_a: str, text_b: str) -> Dict[str, object]:
        co_result = self.co_model.score_pair(text_a, text_b)
        sem_result = self.sem_model.score_pair(text_a, text_b)

        co_score = float(co_result.get('co_score', 0.0))
        sem_score = float(sem_result.get('sem_score', 0.0))
        final_score, rank_source = self._fuse_scores(co_score, sem_score)

        return {
            'text_a': text_a,
            'text_b': text_b,
            'resolved_term_a': co_result.get('resolved_term_a') or sem_result.get('resolved_term_a'),
            'resolved_term_b': co_result.get('resolved_term_b') or sem_result.get('resolved_term_b'),
            'a_in_project_vocab': bool(co_result.get('a_in_project_vocab') or sem_result.get('a_in_project_vocab')),
            'b_in_project_vocab': bool(co_result.get('b_in_project_vocab') or sem_result.get('b_in_project_vocab')),
            'co_score': round(co_score, 6),
            'sem_score': round(sem_score, 6),
            'final_score': round(final_score, 6),
            'rank_source': rank_source,
        }


def default_paths(project_name: str = 'youlai-boot-master') -> Dict[str, str]:
    base = Path(OUTPUT_DIR) / project_name
    return {
        'call_chains': str(base / 'word2vec_call_chains.json'),
        'fasttext_model': str(base / 'term_icf_fasttext.model'),
        'icf': str(base / 'term_icf.npz'),
        'semantic_vocab': str(base / 'term_semantic_vocab.json'),
        'semantic_embeddings': str(base / 'term_semantic_embeddings.npz'),
    }


def demo():
    print('=' * 60)
    print('Hybrid Term Embedding Demo')
    print('=' * 60)

    paths = default_paths()
    embedder = HybridTermEmbedding()
    embedder.build_index(
        paths['call_chains'],
        paths['fasttext_model'],
        paths['icf'],
        paths['semantic_vocab'],
        paths['semantic_embeddings'],
    )

    for query in ['auth', 'save', 'get', 'user login']:
        print(f"\nQuery: {query}")
        for item in embedder.find_related_terms(query, top_k=8):
            print(
                f"  {item['term']:15} final={item['final_score']:.4f} "
                f"sem={item['sem_score']:.4f} co={item['co_score']:.4f}"
            )

    print('\nPair scores:')
    for a, b in [('auth', 'authenticate'), ('get', 'role'), ('save', 'delete')]:
        result = embedder.score_pair(a, b)
        print(
            f"  {a:12} ↔ {b:12} | final={result['final_score']:.4f} "
            f"sem={result['sem_score']:.4f} co={result['co_score']:.4f}"
        )


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Hybrid dual-channel term embedding')
    parser.add_argument('--train', action='store_true')
    parser.add_argument('--demo', action='store_true')
    parser.add_argument('--related', type=str)
    parser.add_argument('--pair-a', type=str)
    parser.add_argument('--pair-b', type=str)
    parser.add_argument('--top-k', type=int, default=10)
    parser.add_argument('--project', type=str, default='youlai-boot-master')
    args = parser.parse_args()

    paths = default_paths(args.project)
    embedder = HybridTermEmbedding()

    if args.train:
        embedder.build_index(
            paths['call_chains'],
            paths['fasttext_model'],
            paths['icf'],
            paths['semantic_vocab'],
            paths['semantic_embeddings'],
        )
    elif args.related or (args.pair_a and args.pair_b):
        embedder.load(
            paths['fasttext_model'],
            paths['icf'],
            paths['semantic_vocab'],
            paths['semantic_embeddings'],
        )
        if args.related:
            print(json.dumps(embedder.find_related_terms(args.related, top_k=args.top_k), ensure_ascii=False, indent=2))
        if args.pair_a and args.pair_b:
            print(json.dumps(embedder.score_pair(args.pair_a, args.pair_b), ensure_ascii=False, indent=2))
    else:
        demo()
