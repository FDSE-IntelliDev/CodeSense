from collections import defaultdict
import json
from tokenizer.tokenizer_core import tokenizer
from definition import OUTPUT_DIR
import re
from typing import Iterable, List, Set

MEANINGLESS_WORDS = {
    "a", "an", "the",
    "and", "or", "but",
    "in", "on", "at", "of", "to", "for", "by", "with", "from", "into", "onto", "as",
    "is", "am", "are", "was", "were", "be", "been", "being",
    "do", "does", "did", "done",
    "have", "has", "had",
    "this", "that", "these", "those",
    "it", "its", "he", "she", "they", "them", "his", "her", "their",
    "if", "else", "then", "than", "not", "no", "yes",
}

# Protect code-domain tokens from accidental filtering.
CODE_WHITELIST = {
    "id", "uid", "uuid",
    "api", "sdk", "cli", "rpc", "http", "https", "tcp", "udp",
    "sql", "db", "dao", "dto", "vo", "po", "bo", "impl", "svc",
    "auth", "oauth", "jwt", "json", "xml", "yaml", "yml", "csv",
    "url", "uri", "ip", "dns",
    "cpu", "gpu", "ram",
    "env", "cfg", "conf",
}

def _normalize_token(token: str) -> str:
    return token.strip().lower()


def _looks_numeric(token: str) -> bool:
    # numeric-like fragments: "2", "3.14", "v2" (v2 is not pure numeric -> keep)
    return bool(re.fullmatch(r"\d+(\.\d+)?", token))

def _is_alpha(token: str) -> bool:
    return token.isalpha()


def _is_function_word_by_wordnet(token: str) -> bool:
    """
    Conservative optional check:
    - Uses nltk.wordnet if available.
    - If token has no noun/verb/adjective/adverb senses but has very limited lexical signal,
      it is likely a function word.
    - This is only a weak fallback and should not override whitelist.
    """
    try:
        from nltk.corpus import wordnet as wn  # type: ignore
    except Exception:
        return False

    # If wordnet data is missing, fail closed.
    try:
        noun = wn.synsets(token, pos=wn.NOUN)
        verb = wn.synsets(token, pos=wn.VERB)
        adj = wn.synsets(token, pos=wn.ADJ)
        adv = wn.synsets(token, pos=wn.ADV)
    except Exception:
        return False

    # If token has no content-word senses and is short, treat as weak token.
    if not noun and not verb and not adj and not adv and len(token) <= 4:
        return True
    return False

def is_meaningless_word(token: str, use_wordnet: bool = False) -> bool:
    """
    Hybrid decision:
    - Keep whitelist first.
    - Rule-based strong filters.
    - Optional WordNet fallback (conservative).
    """
    if not isinstance(token, str):
        return True

    t = _normalize_token(token)
    if not t:
        return True

    if t in CODE_WHITELIST:
        return False

    # Strong rule filters.
    if t in MEANINGLESS_WORDS:
        return True
    if _looks_numeric(t):
        return True
    # Very short alpha tokens are often noise, but preserve whitelist above.
    if len(t) == 1 and _is_alpha(t):
        return True

    # Optional NLP fallback (only for alpha tokens).
    if use_wordnet and _is_alpha(t):
        if _is_function_word_by_wordnet(t):
            return True

    return False

# print(is_meaningless_word('dto'))

def filter_sub_tokens(tokens: Iterable[str], use_wordnet: bool = False) -> List[str]:
    """
    Filter empty/noisy/low-semantic tokens while keeping domain-important terms.
    Deduplicate while preserving order.
    """
    result: List[str] = []
    seen: Set[str] = set()

    for tok in tokens:
        t = _normalize_token(tok)
        if is_meaningless_word(t, use_wordnet=use_wordnet):
            continue
        if t not in seen:
            seen.add(t)
            result.append(t)
    return result

class SymbolNgramer:
    def __init__(self, symbols_index_path: str, output_path: str | None = None):
        self.symbols_index_path = symbols_index_path
        self.ngramed_symbol_path = output_path
    def build_ngramed_symbol(self, use_wordnet: bool = False
    ) :
        """
        从 symbols_index.json 读取代码元素 name，分词后构建倒排索引：
          子词 -> [原始代码标识符, ...]
        """
        invert_index = defaultdict(list)
        seen = defaultdict(set)  # token -> {unique_key}

        with open(self.symbols_index_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        for item in data:
            name = item.get("name", "")
            if not isinstance(name, str) or not name.strip():
                continue

            original_name = name.strip()
            sub_tokens = tokenizer(original_name, "bpe").split(" ")
            sub_tokens = filter_sub_tokens(sub_tokens, use_wordnet=use_wordnet)

            # 用稳定键去重，避免同一 symbol 被重复写入同一 token
            file_ = item.get("file", "")
            rng = item.get("range", {}) or {}
            start_line = rng.get("start_line", -1)
            end_line = rng.get("end_line", -1)
            unique_key = f"{name}|{file_}|{start_line}|{end_line}"

            for tok in sub_tokens:
                if unique_key in seen[tok]:
                    continue
                seen[tok].add(unique_key)
                invert_index[tok].append(item)

        with open(self.ngramed_symbol_path, "w", encoding="utf-8") as f:
            # dict value 是 list[dict]，不做 sorted，保持原始顺序
            json.dump(dict(invert_index), f, ensure_ascii=False, indent=4)


def get_ngramed_symbol(self):
        with open(self.ngramed_symbol_path, "r", encoding="utf-8") as f:
            return json.load(f)

ngramer=SymbolNgramer(symbols_index_path=f"{OUTPUT_DIR}/youlai-boot-master/symbols_index.json",output_path=f"{OUTPUT_DIR}/youlai-boot-master/ngramed_symbol.json")
ngramer.build_ngramed_symbol()