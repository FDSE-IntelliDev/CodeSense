"""
单通道术语 Embedding：Semantic only

职责：
- 为项目术语表预计算 sentence-transformer embedding
- 提供两个接口：
  1. find_related_terms(query, top_k)
  2. score_pair(text_a, text_b)
"""

import json
import sys
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

sys.path.append(str(Path(__file__).parent.parent))
from definition import OUTPUT_DIR,PROJECT_PATH
from embedding.project_term_vocab import load_or_build_project_terms, normalize_query_text, save_project_terms, tokenize_text

try:
    from sentence_transformers import SentenceTransformer
    SENTENCE_TRANSFORMERS_AVAILABLE = True
except ImportError:
    SENTENCE_TRANSFORMERS_AVAILABLE = False


class SemanticTermEmbedding:
    DEFAULT_SEMANTIC_MODEL = 'all-MiniLM-L6-v2'

    def __init__(self, semantic_model_name: str = DEFAULT_SEMANTIC_MODEL):
        self.semantic_model_name = semantic_model_name
        self.semantic_model = None
        self.project_vocab = set()
        self.semantic_terms: List[str] = []
        self.semantic_term_to_idx: Dict[str, int] = {}
        self.semantic_embeddings: Optional[np.ndarray] = None
        self._text_embedding_cache: Dict[str, np.ndarray] = {}

    def _ensure_semantic_model(self):
        if self.semantic_model is not None:
            return self.semantic_model
        if not SENTENCE_TRANSFORMERS_AVAILABLE:
            raise ImportError('sentence-transformers is required. Install: pip install sentence-transformers')
        self.semantic_model = SentenceTransformer(self.semantic_model_name)
        return self.semantic_model

    def _encode_texts(self, texts: List[str]) -> np.ndarray:
        model = self._ensure_semantic_model()
        embeddings = model.encode(
            texts,
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return embeddings.astype(np.float32)

    def _get_text_embedding(self, text: str) -> np.ndarray:
        normalized = normalize_query_text(text)
        cached = self._text_embedding_cache.get(normalized)
        if cached is not None:
            return cached
        embedding = self._encode_texts([normalized])[0]
        self._text_embedding_cache[normalized] = embedding
        return embedding

    def _resolve_project_term(self, text: str) -> Optional[str]:
        tokens = tokenize_text(text)
        if len(tokens) == 1 and tokens[0] in self.project_vocab:
            return tokens[0]
        normalized = normalize_query_text(text)
        if normalized in self.project_vocab:
            return normalized
        return None

    def build_index(
        self,
        call_chains_path: str,
        vocab_output_path: str,
        embeddings_output_path: str,
        model_name: Optional[str] = None,
        project_vocab_path: Optional[str] = None,
    ):
        vocab_source_path = project_vocab_path or vocab_output_path
        self.semantic_terms = load_or_build_project_terms(call_chains_path, vocab_source_path)
        self.project_vocab = set(self.semantic_terms)

        if vocab_output_path != vocab_source_path:
            save_project_terms(vocab_output_path, self.semantic_terms)

        if model_name:
            self.semantic_model_name = model_name

        self.semantic_term_to_idx = {term: idx for idx, term in enumerate(self.semantic_terms)}

        if not self.semantic_terms:
            raise ValueError('No project terms available to build semantic index.')

        embeddings_path = Path(embeddings_output_path)
        if embeddings_path.exists():
            data = np.load(embeddings_path, allow_pickle=True)
            existing_embeddings = data['embeddings'].astype(np.float32)
            existing_model_name = str(data['model_name']) if 'model_name' in data else self.semantic_model_name

            vocab_size_matches = existing_embeddings.shape[0] == len(self.semantic_terms)
            model_matches = existing_model_name == self.semantic_model_name

            if vocab_size_matches and model_matches:
                self.semantic_embeddings = existing_embeddings
                print(f"Semantic embeddings loaded from existing file: {embeddings_output_path}")
                print(f"  Semantic terms: {len(self.semantic_terms)}")
                print(f"  Embedding dim: {self.semantic_embeddings.shape[1]}")
                return

            print("Existing semantic embeddings are incompatible; rebuilding.")
            print(f"  vocab_size_matches={vocab_size_matches}, model_matches={model_matches}")

        self.semantic_embeddings = self._encode_texts(self.semantic_terms)

        np.savez(
            embeddings_output_path,
            embeddings=self.semantic_embeddings,
            model_name=np.array(self.semantic_model_name, dtype=object),
            normalized=np.array(True),
            dim=np.array(self.semantic_embeddings.shape[1]),
        )
        print(f"Semantic embeddings saved to: {embeddings_output_path}")
        print(f"  Semantic terms: {len(self.semantic_terms)}")
        print(f"  Embedding dim: {self.semantic_embeddings.shape[1]}")

    def load(self, vocab_path: str, embeddings_path: str):
        with open(vocab_path, 'r', encoding='utf-8') as f:
            self.semantic_terms = json.load(f)
        data = np.load(embeddings_path, allow_pickle=True)
        self.semantic_embeddings = data['embeddings'].astype(np.float32)
        if 'model_name' in data:
            self.semantic_model_name = str(data['model_name'])
        self.semantic_term_to_idx = {term: idx for idx, term in enumerate(self.semantic_terms)}
        self.project_vocab = set(self.semantic_terms)

    def find_related_terms(self, query: str, top_k: int = 10) -> List[Dict[str, object]]:
        if self.semantic_embeddings is None or not self.semantic_terms:
            return []

        normalized_query = normalize_query_text(query)
        query_vec = self._get_text_embedding(query)
        scores = np.dot(self.semantic_embeddings, query_vec)

        excluded = set(tokenize_text(query))
        excluded.add(normalized_query)

        ranked_indices = np.argsort(scores)[::-1]
        results: List[Dict[str, object]] = []
        for idx in ranked_indices:
            term = self.semantic_terms[int(idx)]
            if term in excluded:
                continue
            score = max(0.0, float(scores[int(idx)]))
            results.append({
                'term': term,
                'sem_score': round(score, 6),
                'final_score': round(score, 6),
                'rank_source': 'semantic',
            })
            if len(results) >= top_k:
                break
        return results

    def score_pair(self, text_a: str, text_b: str) -> Dict[str, object]:
        vec_a = self._get_text_embedding(text_a)
        vec_b = self._get_text_embedding(text_b)

        denom = np.linalg.norm(vec_a) * np.linalg.norm(vec_b)
        if denom <= 0:
            score = 0.0
        else:
            score = max(0.0, float(np.dot(vec_a, vec_b) / denom))

        resolved_a = self._resolve_project_term(text_a)
        resolved_b = self._resolve_project_term(text_b)
        return {
            'text_a': text_a,
            'text_b': text_b,
            'resolved_term_a': resolved_a,
            'resolved_term_b': resolved_b,
            'a_in_project_vocab': resolved_a is not None,
            'b_in_project_vocab': resolved_b is not None,
            'sem_score': round(score, 6),
            'final_score': round(score, 6),
            'rank_source': 'semantic',
        }


def default_paths(project_name: str = 'youlai-boot-master') -> Dict[str, str]:
    base = Path(OUTPUT_DIR) / project_name
    return {
        'project_vocab': str(base / 'term_project_vocab.json'),
        'call_chains': str(base / 'word2vec_call_chains.json'),
        'semantic_vocab': str(base / 'term_project_vocab.json'),
        'semantic_embeddings': str(base / 'term_semantic_embeddings.npz'),
    }


def demo():
    print('=' * 60)
    print('Single-channel Semantic Demo')
    print('=' * 60)

    paths = default_paths()
    embedder = SemanticTermEmbedding()
    embedder.build_index(paths['call_chains'], paths['semantic_vocab'], paths['semantic_embeddings'])

    for query in ['auth', 'save', 'get']:
        print(f"\nQuery: {query}")
        for item in embedder.find_related_terms(query, top_k=8):
            print(f"  {item['term']:15} score={item['final_score']:.4f}")

    print('\nPair scores:')
    for a, b in [('auth', 'authenticate'), ('get', 'role')]:
        result = embedder.score_pair(a, b)
        print(f"  {a:12} ↔ {b:12} | score={result['final_score']:.4f}")


if __name__ == '__main__':
    # parser = argparse.ArgumentParser(description='Single-channel semantic term embedding')
    # parser.add_argument('--build', action='store_true')
    # parser.add_argument('--demo', action='store_true')
    # parser.add_argument('--related', type=str)
    # parser.add_argument('--pair-a', type=str)
    # parser.add_argument('--pair-b', type=str)
    # parser.add_argument('--top-k', type=int, default=10)
    # parser.add_argument('--project', type=str, default='youlai-boot-master')
    # args = parser.parse_args()

    paths = default_paths('youlai-boot-master')
    embedder = SemanticTermEmbedding()


    query="save"
    top_k=10
    str_a,str_b="",""
    # if args.build:
    embedder.build_index(paths['call_chains'], paths['semantic_vocab'], paths['semantic_embeddings'])
    # elif args.related or (args.pair_a and args.pair_b):
    embedder.load(paths['semantic_vocab'], paths['semantic_embeddings'])
    print(json.dumps(embedder.find_related_terms(query, top_k=top_k), ensure_ascii=False, indent=2))
        # if args.pair_a and args.pair_b:
    print(json.dumps(embedder.score_pair(str_a,str_b), ensure_ascii=False, indent=2))
    # else:
    demo()
