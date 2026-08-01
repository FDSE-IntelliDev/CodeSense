"""
从 word2vec_call_chains.json 构建增强版调用链训练 corpus。

当前只使用函数名术语，不处理 code 字段。

增强点：
1. 保留整条调用链句子
2. 按函数距离构造局部窗口句子，例如 A B、B C、A B C
3. 构造相邻调用边句子，例如 A B

输出：
- 默认保存为 output/<project>/enhanced_call_chain_corpus.json
- JSON 格式为 List[List[str]]，可直接作为 gensim FastText/Word2Vec 的 sentences 输入
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Dict, List

from codesense.config import OUTPUT_DIR, CORPUS
from codesense.embedding.project_term_vocab import extract_terms_from_func_name


def build_function_terms(item: Dict) -> List[str]:
    """从单个调用链节点中提取函数名术语。"""
    return extract_terms_from_func_name(item.get('func_name', ''))


def add_repeated(corpus: List[List[str]], sentence: List[str], repeat: int = 1):
    """向 corpus 添加句子，可用重复次数模拟权重。"""
    clean = [token for token in sentence if token]
    if len(clean) < 2:
        return
    for _ in range(max(1, repeat)):
        corpus.append(clean)


def flatten_profiles(profiles: List[List[str]]) -> List[str]:
    """将多个函数 profile 展平成一个 token 序列。"""
    sentence: List[str] = []
    for profile in profiles:
        sentence.extend(profile)
    return sentence


def build_enhanced_corpus(
    chains: List[List[Dict]],
    include_full_chain: bool = True,
    include_local_windows: bool = True,
    include_edges: bool = True,
    window_sizes: List[int] = None,
    full_chain_repeat: int = 1, # 整体业务流程，弱权重
    local_window_repeat: int = 2, # 近距离调用关系，中权重
    edge_repeat: int = 3, # 直接调用边，强权重
) -> List[List[str]]:
    """构建增强调用链 corpus。"""
    if window_sizes is None:
        window_sizes = [2, 3]

    corpus: List[List[str]] = []

    for chain in chains:
        profiles = [build_function_terms(item) for item in chain]
        profiles = [profile for profile in profiles if profile]
        if not profiles:
            continue

        # 1. 整链句子：保留完整业务流程级共现。
        if include_full_chain:
            add_repeated(corpus, flatten_profiles(profiles), repeat=full_chain_repeat)

        # 2. 局部窗口句子：强化短距离调用关系。
        if include_local_windows:
            for window_size in window_sizes:
                if window_size <= 1:
                    continue
                if len(profiles) < window_size:
                    continue
                for start in range(len(profiles) - window_size + 1):
                    window_profiles = profiles[start:start + window_size]
                    add_repeated(corpus, flatten_profiles(window_profiles), repeat=local_window_repeat)

        # 3. 相邻调用边句子：强化 caller 与 callee 的直接共现关系。
        if include_edges and len(profiles) >= 2:
            for i in range(len(profiles) - 1):
                edge_sentence = profiles[i] + profiles[i + 1]
                add_repeated(corpus, edge_sentence, repeat=edge_repeat)

    return corpus


def summarize_corpus(corpus: List[List[str]]):
    token_counter = Counter()
    lengths = []
    for sentence in corpus:
        token_counter.update(sentence)
        lengths.append(len(sentence))

    if not lengths:
        print('Corpus is empty.')
        return

    print('Enhanced corpus summary:')
    print(f'  sentences: {len(corpus)}')
    print(f'  unique tokens: {len(token_counter)}')
    print(f'  avg sentence length: {sum(lengths) / len(lengths):.2f}')
    print(f'  max sentence length: {max(lengths)}')
    print(f'  top tokens: {token_counter.most_common(20)}')


def default_paths(project_name: str = 'youlai-boot-master') -> Dict[str, str]:
    base = Path(OUTPUT_DIR) / project_name
    return {
        'call_chains': str(base / 'word2vec_call_chains.json'),
        'enhanced_corpus': str(base / CORPUS),
    }


def main():
    paths = default_paths('youlai-boot-master')
    input_path = paths['call_chains']
    output_path = paths['enhanced_corpus']
    window_sizes = [2,3]

    with open(input_path, 'r', encoding='utf-8') as f:
        chains = json.load(f)

    corpus = build_enhanced_corpus(
        chains,
        window_sizes=window_sizes,
    )

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(corpus, f, ensure_ascii=False, indent=2)

    print(f'Enhanced corpus saved to: {output_path}')
    summarize_corpus(corpus)


if __name__ == '__main__':
    main()
