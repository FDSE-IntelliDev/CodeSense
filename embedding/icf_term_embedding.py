"""
单通道术语 Embedding：FastText + ICF

职责：
- 基于项目调用链训练 FastText
- 用 ICF 对高频词伪相关做惩罚
- 提供两个接口：
  1. find_related_terms(query, top_k)
  2. score_pair(text_a, text_b)
"""

import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

sys.path.append(str(Path(__file__).parent.parent))
from definition import OUTPUT_DIR
from tokenizer.tokenizer_core import tokenizer

try:
    from gensim.models import FastText
    GENSIM_AVAILABLE = True
except ImportError:
    GENSIM_AVAILABLE = False


class ICFCalculator:
    """计算术语的 ICF 值。"""

    STOPWORDS = {
        'by', 'to', 'of', 'in', 'on', 'at', 'for', 'from', 'with',
        'and', 'or', 'is', 'has', 'have', 'do', 'the', 'a', 'an',
    }

    def __init__(self):
        self.term_in_chains = defaultdict(set)
        self.total_chains = 0
        self.icf_scores: Dict[str, float] = {}
        self.high_freq_ratio = 0.1
        self.high_freq_terms = set()
        self.high_freq_cutoff_icf: Optional[float] = None

    def _tokenize_text(self, text: str) -> List[str]:
        raw = tokenizer(text).split(' ')
        return [t.lower() for t in raw if len(t) > 1 and t.lower() not in self.STOPWORDS]

    def extract_terms(self, func_name: str) -> List[str]:
        name = func_name.split('(')[0].split(':')[0].strip()
        return self._tokenize_text(name)

    def extract_query_terms(self, text: str) -> List[str]:
        return self._tokenize_text(text.strip())

    def normalize_query_text(self, text: str) -> str:
        terms = self.extract_query_terms(text)
        if terms:
            return ' '.join(terms)
        return text.strip().lower()

    def compute_from_chains(self, call_chains_path: str):
        with open(call_chains_path, 'r', encoding='utf-8') as f:
            chains = json.load(f)

        self.total_chains = len(chains)
        self.term_in_chains = defaultdict(set)
        self.icf_scores = {}

        for chain_id, chain in enumerate(chains):
            chain_terms = set()
            for item in chain:
                terms = self.extract_terms(item['func_name'])
                chain_terms.update(terms)
            for term in chain_terms:
                self.term_in_chains[term].add(chain_id)

        for term, term_chains in self.term_in_chains.items():
            if len(term_chains) > 0:
                self.icf_scores[term] = math.log(self.total_chains / len(term_chains))
            else:
                self.icf_scores[term] = 0.0

        self._refresh_high_freq_terms()

        print(f"ICF computed: {len(self.icf_scores)} terms")
        print(f"  Total chains: {self.total_chains}")
        print(f"  High frequency ratio: top {int(self.high_freq_ratio * 100)}% by chain coverage")
        print(f"  High frequency terms: {len(self.high_freq_terms)}")
        if self.high_freq_cutoff_icf is not None:
            print(f"  High frequency cutoff ICF: {self.high_freq_cutoff_icf:.4f}")

        return self.icf_scores

    def _refresh_high_freq_terms(self):
        if not self.icf_scores:
            self.high_freq_terms = set()
            self.high_freq_cutoff_icf = None
            return

        sorted_terms = sorted(self.icf_scores.items(), key=lambda item: item[1])
        top_k = max(1, math.ceil(len(sorted_terms) * self.high_freq_ratio))
        self.high_freq_terms = {term for term, _ in sorted_terms[:top_k]}
        self.high_freq_cutoff_icf = sorted_terms[top_k - 1][1]

    def get_icf(self, term: str) -> float:
        if term in self.icf_scores:
            return self.icf_scores[term]
        if self.high_freq_cutoff_icf is not None:
            return self.high_freq_cutoff_icf
        return float('inf')

    def is_high_freq(self, term: str) -> bool:
        return term in self.high_freq_terms

    def get_high_freq_terms(self) -> List[str]:
        return sorted(self.high_freq_terms, key=lambda t: self.icf_scores.get(t, float('inf')))

    def save(self, output_path: str):
        np.savez(
            output_path,
            icf_scores=np.array(list(self.icf_scores.items()), dtype=object),
            high_freq_ratio=self.high_freq_ratio,
            high_freq_terms=np.array(sorted(self.high_freq_terms), dtype=object),
            high_freq_cutoff_icf=self.high_freq_cutoff_icf if self.high_freq_cutoff_icf is not None else np.nan,
            total_chains=self.total_chains,
        )
        print(f"ICF saved to: {output_path}")

    def load(self, input_path: str):
        data = np.load(input_path, allow_pickle=True)
        self.icf_scores = dict(data['icf_scores'])
        self.high_freq_ratio = float(data['high_freq_ratio']) if 'high_freq_ratio' in data else 0.1
        self.high_freq_terms = set(data['high_freq_terms'].tolist()) if 'high_freq_terms' in data else set()
        if 'high_freq_cutoff_icf' in data and not np.isnan(float(data['high_freq_cutoff_icf'])):
            self.high_freq_cutoff_icf = float(data['high_freq_cutoff_icf'])
        else:
            self.high_freq_cutoff_icf = None
        self.total_chains = int(data['total_chains'])
        if not self.high_freq_terms:
            self._refresh_high_freq_terms()
        print(f"ICF loaded: {len(self.icf_scores)} terms")
        print(f"  High frequency ratio: top {int(self.high_freq_ratio * 100)}% by chain coverage")


def weighted_similarity(
    sim: float,
    icf1: float,
    icf2: float,
    is_high_freq1: bool,
    is_high_freq2: bool,
    high_freq_cutoff_icf: Optional[float],
) -> float:
    sim = max(0.0, float(sim))
    if is_high_freq1 and is_high_freq2:
        denom = high_freq_cutoff_icf if high_freq_cutoff_icf and high_freq_cutoff_icf > 0 else 1.0
        penalty = min((icf1 + icf2) / 2 / denom, 1.0)
        return sim * penalty
    return sim


class ICFTermEmbedding:
    """单通道：FastText + ICF。"""

    def __init__(self):
        self.icf_calc = ICFCalculator()
        self.fasttext_model = None
        self.project_vocab = set()

    def _resolve_project_term(self, text: str) -> Optional[str]:
        tokens = self.icf_calc.extract_query_terms(text)
        if len(tokens) == 1 and tokens[0] in self.project_vocab:
            return tokens[0]
        normalized = self.icf_calc.normalize_query_text(text)
        if normalized in self.project_vocab:
            return normalized
        return None

    def _train_fasttext(self, call_chains_path: str, output_path: str):
        with open(call_chains_path, 'r', encoding='utf-8') as f:
            chains = json.load(f)

        corpus = []
        for chain in chains:
            chain_terms = []
            for item in chain:
                terms = self.icf_calc.extract_terms(item['func_name'])
                chain_terms.extend(terms)
            if len(chain_terms) >= 2:
                corpus.append(chain_terms)

        self.fasttext_model = FastText(
            sentences=corpus,
            vector_size=128,
            window=5,
            min_count=1,
            min_n=3,
            max_n=6,
            epochs=100,
            workers=4,
        )
        self.fasttext_model.save(output_path)
        self.project_vocab = set(self.fasttext_model.wv.key_to_index.keys())
        print(f"FastText vocab: {len(self.project_vocab)}")

    def train(self, call_chains_path: str, model_output_path: str, icf_output_path: str):
        self.icf_calc.compute_from_chains(call_chains_path)
        self.icf_calc.save(icf_output_path)
        if not GENSIM_AVAILABLE:
            raise ImportError('gensim is required. Install: pip install gensim')
        self._train_fasttext(call_chains_path, model_output_path)

    def load(self, model_path: str, icf_path: str):
        self.icf_calc.load(icf_path)
        if not GENSIM_AVAILABLE:
            raise ImportError('gensim is required. Install: pip install gensim')
        self.fasttext_model = FastText.load(model_path)
        self.project_vocab = set(self.fasttext_model.wv.key_to_index.keys())

    def _find_co_candidates(self, query: str, top_k: int = 20) -> Dict[str, float]:
        if not self.fasttext_model:
            return {}

        query_tokens = self.icf_calc.extract_query_terms(query)
        excluded = set(query_tokens)
        excluded.add(self.icf_calc.normalize_query_text(query))

        candidate_scores: Dict[str, float] = {}
        for token in query_tokens:
            if token not in self.project_vocab:
                continue
            try:
                neighbors = self.fasttext_model.wv.most_similar(token, topn=max(top_k * 3, 20))
            except KeyError:
                continue

            token_icf = self.icf_calc.get_icf(token)
            for word, sim in neighbors:
                if word not in self.project_vocab or word in excluded:
                    continue
                score = weighted_similarity(
                    sim,
                    token_icf,
                    self.icf_calc.get_icf(word),
                    self.icf_calc.is_high_freq(token),
                    self.icf_calc.is_high_freq(word),
                    self.icf_calc.high_freq_cutoff_icf,
                )
                if score > candidate_scores.get(word, 0.0):
                    candidate_scores[word] = score

        ranked = sorted(candidate_scores.items(), key=lambda item: item[1], reverse=True)
        return dict(ranked[:top_k])

    def find_similar(self, term: str, top_k: int = 10) -> List[Tuple[str, float]]:
        return list(self._find_co_candidates(term, top_k=top_k).items())

    def find_related_terms(self, query: str, top_k: int = 10) -> List[Dict[str, object]]:
        candidates = self._find_co_candidates(query, top_k=max(20, top_k * 3))
        results = []
        for term, score in candidates.items():
            results.append({
                'term': term,
                'co_score': round(score, 6),
                'final_score': round(score, 6),
                'rank_source': 'co',
                'is_high_freq': self.icf_calc.is_high_freq(term),
                'icf': round(self.icf_calc.get_icf(term), 6),
            })
        results.sort(key=lambda item: item['final_score'], reverse=True)
        return results[:top_k]

    def score_pair(self, text_a: str, text_b: str) -> Dict[str, object]:
        term_a = self._resolve_project_term(text_a)
        term_b = self._resolve_project_term(text_b)
        score = 0.0
        if term_a and term_b:
            raw = float(self.fasttext_model.wv.similarity(term_a, term_b))
            score = weighted_similarity(
                raw,
                self.icf_calc.get_icf(term_a),
                self.icf_calc.get_icf(term_b),
                self.icf_calc.is_high_freq(term_a),
                self.icf_calc.is_high_freq(term_b),
                self.icf_calc.high_freq_cutoff_icf,
            )

        score = max(0.0, score)
        return {
            'text_a': text_a,
            'text_b': text_b,
            'resolved_term_a': term_a,
            'resolved_term_b': term_b,
            'a_in_project_vocab': term_a is not None,
            'b_in_project_vocab': term_b is not None,
            'co_score': round(score, 6),
            'final_score': round(score, 6),
            'rank_source': 'co',
        }


def default_paths(project_name: str = 'youlai-boot-master') -> Dict[str, str]:
    base = Path(OUTPUT_DIR) / project_name
    return {
        'call_chains': str(base / 'word2vec_call_chains.json'),
        'fasttext_model': str(base / 'term_icf_fasttext.model'),
        'icf': str(base / 'term_icf.npz'),
    }


def demo():
    print('=' * 60)
    print('Single-channel Co-occurrence Demo')
    print('=' * 60)

    paths = default_paths()
    embedder = ICFTermEmbedding()
    embedder.train(paths['call_chains'], paths['fasttext_model'], paths['icf'])

    for query in ['auth', 'save', 'get']:
        print(f"\nQuery: {query}")
        for item in embedder.find_related_terms(query, top_k=10):
            print(f"  {item['term']:15} score={item['final_score']:.4f}")

    print('\nPair scores:')
    for a, b in [('auth', 'authenticate'), ('get', 'role')]:
        result = embedder.score_pair(a, b)
        print(f"  {a:12} ↔ {b:12} | score={result['final_score']:.4f}")


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Single-channel co-occurrence term embedding')
    parser.add_argument('--train', action='store_true')
    parser.add_argument('--demo', action='store_true')
    parser.add_argument('--related', type=str)
    parser.add_argument('--pair-a', type=str)
    parser.add_argument('--pair-b', type=str)
    parser.add_argument('--top-k', type=int, default=10)
    parser.add_argument('--project', type=str, default='youlai-boot-master')
    args = parser.parse_args(
        [
            '--demo',
        ]
    )

    paths = default_paths(args.project)
    embedder = ICFTermEmbedding()

    if args.train:
        embedder.train(paths['call_chains'], paths['fasttext_model'], paths['icf'])
    elif args.related or (args.pair_a and args.pair_b):
        embedder.load(paths['fasttext_model'], paths['icf'])
        if args.related:
            print(json.dumps(embedder.find_related_terms(args.related, top_k=args.top_k), ensure_ascii=False, indent=2))
        if args.pair_a and args.pair_b:
            print(json.dumps(embedder.score_pair(args.pair_a, args.pair_b), ensure_ascii=False, indent=2))
    else:
        demo()
