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
from definition import OUTPUT_DIR, CORPUS
from embedding.icf_term_embedding import ICFTermEmbedding
from embedding.semantic_term_embedding import SemanticTermEmbedding


class HybridTermEmbedding:
    def __init__(
        self,
        semantic_model_name: str = SemanticTermEmbedding.DEFAULT_SEMANTIC_MODEL,
        co_weight: float = 0.4,
        sem_weight: float = 0.6,
    ):
        self.co_weight = co_weight
        self.sem_weight = sem_weight
        self.co_model = ICFTermEmbedding()
        self.sem_model = SemanticTermEmbedding(semantic_model_name=semantic_model_name)

    def _fuse_scores(self, co_score: float, sem_score: float) -> Tuple[float, str]:
        total = 0.0
        sources = []

        if sem_score > 0:
            total += self.sem_weight * sem_score
            sources.append('semantic')
        if co_score > 0:
            total += self.co_weight * co_score
            sources.append('co')
        return total , '_and_'.join(sources)

    def build_index(
        self,
        call_chains_path: str,
        co_model_path: str,
        icf_path: str,
        semantic_vocab_path: str,
        semantic_embeddings_path: str,
        project_vocab_path: str = None,
        enhanced_corpus_path: str = None,
    ):
        self.co_model.train(call_chains_path, co_model_path, icf_path, project_vocab_path, enhanced_corpus_path)
        self.sem_model.build_index(
            call_chains_path,
            semantic_vocab_path,
            semantic_embeddings_path,
            project_vocab_path=project_vocab_path,
        )

    def load(
        self,
        co_model_path: str,
        icf_path: str,
        semantic_vocab_path: str,
        semantic_embeddings_path: str,
        project_vocab_path: str = None,
    ):
        self.co_model.load(co_model_path, icf_path, project_vocab_path)
        self.sem_model.load(semantic_vocab_path, semantic_embeddings_path)

    def _compute_top_k(self, query: str, min_k: int = 3, max_k: int = 10) -> int:
        resolved = self.co_model._resolve_project_term(query)
        if resolved is None:
            return max_k

        chain_count = len(self.co_model.icf_calc.term_in_chains.get(resolved, set()))
        total_chains = self.co_model.icf_calc.total_chains

        if total_chains <= 0:
            return max_k

        max_chain_count = 0
        for chains in self.co_model.icf_calc.term_in_chains.values():
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

    def find_related_terms(self, query: str, top_k: int = None) -> List[Dict[str, object]]:
        if top_k is None:
            top_k = self._compute_top_k(query)

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
        'project_vocab': str(base / 'term_project_vocab.json'),
        'call_chains': str(base / 'word2vec_call_chains.json'),
        'enhanced_corpus': str(base / CORPUS),
        'fasttext_model': str(base / 'term_icf_fasttext.model'),
        'icf': str(base / 'term_icf.npz'),
        'semantic_vocab': str(base / 'term_project_vocab.json'),
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
        paths['project_vocab'],
        paths['enhanced_corpus'],
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

    paths = default_paths('youlai-boot-master')
    embedder = HybridTermEmbedding()
    query=""
    top_k=None
    str_a,str_b="",""


    # if args.train:
    embedder.build_index(
            paths['call_chains'],
            paths['fasttext_model'],
            paths['icf'],
            paths['semantic_vocab'],
            paths['semantic_embeddings'],
            paths['project_vocab'],
            paths['enhanced_corpus'],
        )
    # elif args.related or (args.pair_a and args.pair_b):
    embedder.load(
            paths['fasttext_model'],
            paths['icf'],
            paths['semantic_vocab'],
            paths['semantic_embeddings'],
            paths['project_vocab'],
        )
        # if args.related:
    print(json.dumps(embedder.find_related_terms(query,top_k), ensure_ascii=False, indent=2))
        # if args.pair_a and args.pair_b:
    print(json.dumps(embedder.score_pair(str_a,str_b), ensure_ascii=False, indent=2))
    # else:
    demo()
