"""
项目术语表构建与加载工具。

职责：
- 从 word2vec_call_chains.json 中抽取项目术语表
- 优先复用 output 中已经存在的词表文件
- 为 semantic / co-occurrence 通道提供共同的词表与分词逻辑
"""

import json
import sys
from pathlib import Path
from typing import List, Set

from codesense.tokenizer.tokenizer_core import tokenizer


STOPWORDS = {
    'by', 'to', 'of', 'in', 'on', 'at', 'for', 'from', 'with',
    'and', 'or', 'is', 'has', 'have', 'do', 'the', 'a', 'an',
}


def tokenize_text(text: str) -> List[str]:
    """将普通字符串或标识符切分为项目术语 token。"""
    raw = tokenizer(text).split(' ')
    return [t.lower() for t in raw if len(t) > 1 and t.lower() not in STOPWORDS]


def extract_terms_from_func_name(func_name: str) -> List[str]:
    """从函数签名/函数名中抽取术语。"""
    name = func_name.split('(')[0].split(':')[0].strip()
    return tokenize_text(name)


def normalize_query_text(text: str) -> str:
    """将输入字符串规范化为 token 空格拼接形式。"""
    terms = tokenize_text(text.strip())
    if terms:
        return ' '.join(terms)
    return text.strip().lower()


def build_project_terms_from_call_chains(call_chains_path: str) -> List[str]:
    """从调用链数据中构建项目术语表。"""
    with open(call_chains_path, 'r', encoding='utf-8') as f:
        chains = json.load(f)

    terms: Set[str] = set()
    for chain in chains:
        for item in chain:
            terms.update(extract_terms_from_func_name(item.get('func_name', '')))
    return sorted(terms)


def load_project_terms(vocab_path: str) -> List[str]:
    """加载已有项目术语表。"""
    with open(vocab_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(f'Project vocab file should be a list: {vocab_path}')
    return sorted({str(term).strip().lower() for term in data if str(term).strip()})


def save_project_terms(vocab_path: str, terms: List[str]):
    """保存项目术语表。"""
    path = Path(vocab_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(sorted(set(terms)), f, ensure_ascii=False, indent=2)


def load_or_build_project_terms(call_chains_path: str, vocab_path: str) -> List[str]:
    """优先加载已有词表；不存在时从调用链构建并保存。"""
    if Path(vocab_path).exists():
        terms = load_project_terms(vocab_path)
        print(f"Loaded project vocab from: {vocab_path}")
        print(f"  Project terms: {len(terms)}")
        return terms

    terms = build_project_terms_from_call_chains(call_chains_path)
    save_project_terms(vocab_path, terms)
    print(f"Built project vocab from call chains: {call_chains_path}")
    print(f"Project vocab saved to: {vocab_path}")
    print(f"  Project terms: {len(terms)}")
    return terms
