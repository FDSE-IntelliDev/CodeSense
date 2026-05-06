"""
Abbreviation generation module for phrase matching.

This module provides functions to generate abbreviations from phrases using various
strategies including prefix generation, syllable-based splitting, and consonant skeleton
extraction.
"""
from multiprocessing import Pool, cpu_count
from functools import partial
from tqdm import tqdm
import itertools
from datetime import datetime
import re
import itertools
from functools import lru_cache
from typing import Set, List, Dict, Tuple
import nltk
from nltk.corpus import stopwords
from srctoolkit.delimiter import Delimiter
from tqdm import tqdm
from collections import defaultdict
from tokenizer.tokenizer_core import tokenizer
import time
from expansion.NameHandler import NameHandler
# from NameHandler import NameHandler
import json,os
from definition import EXPANSION_DIR

from nltk.corpus import wordnet as wn
from nltk.stem import WordNetLemmatizer

lemmatizer = WordNetLemmatizer()


def is_noun(word: str) -> bool:
    """
    判断一个词是否可以作为名词存在于 WordNet
    """
    return bool(wn.synsets(word, pos=wn.NOUN))


def is_verb(word: str) -> bool:
    return bool(wn.synsets(word, pos=wn.VERB))


def normalize_word(word: str) -> str:
    """
    - 如果 word 是“仅名词”，返回名词单数形式
    - 如果 word 是“仅动词”，返回动词原形
    - 如果同时是名词和动词，或不是名词/动词，直接返回原词
    - 空输入返回 ""
    """
    if not word:
        return ""

    w = word.lower()

    is_n = is_noun(w)
    is_v = is_verb(w)

    norm_res = set()
    norm_res.add(word)
    # 仅为名词 → 名词单数化
    if is_n and not is_v and w.endswith('s'):
        norm_res.add(lemmatizer.lemmatize(w, pos=wn.NOUN))

    # 为动词 → 动词原形
    if is_v:
        norm_res.add(lemmatizer.lemmatize(w, pos=wn.VERB))

    # 名词/动词歧义词，或其他词性 → 不处理
    return norm_res


# print(normalize_word("freezing"))

def is_english_word(word: str) -> bool:
    if not word:
        return False
    return bool(wn.synsets(word.lower()))


# print(is_english_word("CPU"))
# -------------------------
# Constants
# -------------------------
# Download stopwords if not already available
try:
    STOP_WORDS = frozenset(stopwords.words('english'))
except LookupError:
    nltk.download('stopwords', quiet=True)
    STOP_WORDS = frozenset(stopwords.words('english'))

VOWELS = frozenset("aeiou")
NON_ALPHA_PATTERN = re.compile(r"[^a-zA-Z]+")
TIME_OUT = 1
VALID_ABBR_SET_PATH = f'{EXPANSION_DIR}/dataset/valid_abbr.json'

with open(VALID_ABBR_SET_PATH, 'r') as f:
    VALID_ABBR_SET = json.load(f)

handler = NameHandler.get_inst()


# =========================
# 1. phrase tokenizer
# =========================

def tokenize(phrase: str, split_type=False) -> List[str]:
    """
    Tokenize a phrase into lowercase alphabetic tokens.

    Args:
        phrase: Input phrase to tokenize

    Returns:
        List of lowercase alphabetic tokens
    """
    phrase = phrase.strip()
    if not split_type:
        return phrase.lower().split()

    tokens = []
    if split_type=="camel":
        for word in phrase.split():
            for part in Delimiter.split_camel(word).split():
                for token in NON_ALPHA_PATTERN.split(part):
                    if token:
                        tokens.append(token.lower())
        return tokens

    if split_type=="tokenizer":
        for word in phrase.split():
            for part in tokenizer(word).split():
                for token in NON_ALPHA_PATTERN.split(part):
                    if token:
                        tokens.append(token.lower())

        return tokens

# =========================
# 2. Prefix family generation
# =========================
def prefix_family(word: str, max_len: int = 8) -> Tuple[str, ...]:
    """Generate all prefixes of a word up to max_len. Cached for performance."""
    return tuple(word[:i] for i in range(1, min(len(word), max_len) + 1))


# # =========================
# # 3. Syllable-like prefix
# # =========================
# def syllable_like_prefix(word: str, max_len: int = 8) -> str:
#     """
#     Extract a syllable-like prefix from a word.

#     Returns prefix up to the second vowel or max_len, whichever is shorter.
#     Returns None if word has no vowels.
#     """
#     if not word:
#         return None

#     first_vowel_idx = next((i for i, ch in enumerate(word) if ch in VOWELS), -1)
#     if first_vowel_idx == -1:
#         return None

#     second_vowel_idx = next(
#         (j for j in range(first_vowel_idx + 1, len(word)) if word[j] in VOWELS),
#         -1
#     )

#     if second_vowel_idx == -1:
#         return word[:max_len]

#     return word[:min(second_vowel_idx, max_len)]

# =========================
# 4. Consonant skeleton + subsequence variants
# =========================
def consonant_subsequence_variants(word: str, max_len: int = 8) -> Tuple[str, ...]:
    """
    Generate consonant skeleton subsequences.

    All generated sequences start with the original word's first letter.
    Uses DFS to generate subsequences with length between 2 and max_len.

    Args:
        word: Input word to process
        max_len: Maximum length of generated variants

    Returns:
        Sorted list of consonant skeleton variants
    """
    if not word:
        return []

    word_lower = word.lower()
    first_letter = word_lower[0]
    consonants = [c for i, c in enumerate(word_lower) if c not in VOWELS or word_lower[i - 1] in {'/'}]

    if not consonants:
        return []

    variants = set()
    num_consonants = len(consonants)

    start = time.time()

    def dfs(idx: int, path: List[str]) -> None:
        """Generate subsequences using depth-first search."""
        path_len = len(path)
        end = time.time()
        if 1 < path_len <= max_len:
            variants.add("".join(path))

        if idx >= num_consonants or path_len >= max_len:
            return

        if end - start > TIME_OUT:  # 超时强制截断，避免指数爆炸堵死
            return

            # Include current consonant
        dfs(idx + 1, path + [consonants[idx]])
        # Skip current consonant
        dfs(idx + 1, path)

    # Start DFS from second consonant with first letter fixed
    dfs(1, [first_letter])

    # Add full consonant skeleton truncated to max_len
    variants.add("".join(consonants[:max_len]))

    # Special case: if first letter is vowel and second is consonant, add second consonant. Eg., "extensible" -> "x"
    if len(word_lower) > 1 and word_lower[0] not in consonants and word_lower[1] in consonants:
        variants.add(word_lower[1])

    return tuple(sorted(variants))


# =========================
# 5. Candidates for each word
# =========================
@lru_cache(maxsize=4096)
def word_candidates(word: str, max_len: int = 8) -> Tuple[str, ...]:
    """
    Generate all abbreviation candidates for a single word.

    Combines prefixes, syllable-based prefixes, and consonant skeleton variants.
    Stop words return simplified candidates. Cached for performance.

    Args:
        word: Word to generate candidates for
        max_len: Maximum length for candidates

    Returns:
        Tuple of abbreviation candidates (sorted by length)
    """
    # Limit max_len to 70% of word length
    effective_max_len = min(max_len, int(len(word) * 0.75))

    # 大写 小写 复数加s
    def is_word_valid_abbr(word):
        upper_word = word.upper()
        lower_word = word.lower()
        word_set = [upper_word, lower_word]
        for w in word_set:
            if w in VALID_ABBR_SET or (word.endswith('s') and w[:-1] in VALID_ABBR_SET):
                return True
        return False

    if is_word_valid_abbr(word):
        candidates = set()
        candidates.add(word)
        return candidates

    if not is_english_word(word):
        candidates = set()
        candidates.add(word)
        if len(word) >= 3:
            candidates.update(prefix_family(word, 3))
        candidates.add(word[0])
        return candidates

    if word in STOP_WORDS:
        candidates = {'', word[0], word}
        if word in {"and", "or", "for", "to"}:
            candidates.add('/')
        if word == "to":
            candidates.add('2')
        if word == "for":
            candidates.add('4')
        return tuple(sorted(candidates, key=len))

    candidates = set()

    # Add prefix family
    candidates.update(prefix_family(word, effective_max_len))

    # # Add syllable-like prefix
    # syllable_prefix = syllable_like_prefix(word, effective_max_len)
    # if syllable_prefix:
    #     candidates.add(syllable_prefix)

    # Add consonant skeleton subsequences
    candidates.update(consonant_subsequence_variants(word, effective_max_len))

    # Only truncate candidates that exceed max_len (optimization)
    if effective_max_len < max_len:
        candidates = {c[:max_len] if len(c) > max_len else c for c in candidates}

    # 给名词单独添加单数形式,动词单独添加原型形式，因为max_len限制，名词的单数形式的子序列可能会被过滤掉，而动词如freezing可能在缩写中体现为freeze所以要还原为原型
    norm_w = normalize_word(word)
    candidates.update(norm_w)

    return tuple(sorted(candidates, key=len))

# =========================
# 6. Combine tokens into multi-word abbreviations
# =========================
def combine_word_candidates(
        word_candidate_dict,
        max_len,
        max_part_len
) -> List[str]:
    """
    Generate multi-word abbreviations by combining candidates from each word.

    Generates combinations using:
    - All abbreviated parts
    - First word full + rest abbreviated
    - Abbreviated + last word full
    - First and last words full + middle abbreviated

    Args:
        word_candidate_dict: Dict or list of (word, candidates) tuples preserving order
        max_len: Maximum length for combined abbreviations

    Returns:
        Sorted list of valid abbreviation combinations
    """
    results = set()

    # Handle both dict and list input
    if isinstance(word_candidate_dict, dict):
        words, candidate_lists = zip(*word_candidate_dict.items())
    else:
        words, candidate_lists = zip(*word_candidate_dict)
    first_word = words[0]
    last_word = words[-1]
    is_multi_word = len(words) > 1
    num_words = len(words)

    # Detect consecutive words with same first letter (e.g., "SQL Standard Scalable" -> "s3")
    consecutive_pattern = None
    consecutive_start_idx = -1
    consecutive_end_idx = -1

    if num_words >= 3:
        # Build acronym from first letters
        first_letters = [word[0].lower() for word in words]

        # Find longest sequence of identical consecutive letters (minimum 3)
        for i in range(num_words - 2):
            letter = first_letters[i]
            count = 1

            # Count consecutive occurrences
            for j in range(i + 1, num_words):
                if first_letters[j] == letter:
                    count += 1
                else:
                    break

            # If we found 3+ consecutive same letters, create pattern
            if count >= 3 and (consecutive_pattern is None or count > int(consecutive_pattern[1:])):
                consecutive_pattern = letter + str(count)
                consecutive_start_idx = i
                consecutive_end_idx = i + count
    start = time.time()
    for combination in itertools.product(*candidate_lists):
        # Calculate total length once for reuse
        total_len = sum(len(c) for c in combination)
        # if combination==('pg','table','cache'):
        #     a=1
        # All parts abbreviated
        if total_len <= max_len:
            results.add("".join(combination))

        # Add consecutive letter pattern abbreviation (e.g., "as3ap" for "ANSI SQL Standard Scalable and Portable")
        if consecutive_pattern and consecutive_start_idx >= 0:
            # Build: prefix + pattern + suffix
            prefix_parts = combination[:consecutive_start_idx]
            suffix_parts = combination[consecutive_end_idx:]
            new_combination = prefix_parts + (consecutive_pattern,) + suffix_parts

            new_total_len = sum(len(c) for c in new_combination)
            if new_total_len <= max_len:
                results.add("".join(new_combination))

        if is_multi_word:
            comb_first_len = len(combination[0])
            comb_last_len = len(combination[-1])

            # First word full + rest abbreviated
            new_len = total_len - comb_first_len + 1
            if new_len <= max_len:
                results.add(first_word + "".join(combination[1:]))

            # Abbreviated + last word full
            new_len = total_len - comb_last_len + 1
            if new_len <= max_len:
                results.add("".join(combination[:-1]) + last_word)

            # First and last full + middle abbreviated
            new_len = total_len - comb_first_len - comb_last_len + 2
            if new_len <= max_len:
                results.add(first_word + "".join(combination[1:-1]) + last_word)
        end = time.time()
        if end - start > TIME_OUT:
            break

    return sorted(results, key=lambda x: (len(x), x))

SPECIAL_CHAR = ['-', '/']
def normalize_entity(item: str):
    normalize_result = set()
    if len(item) > 40 and '_' in item:
        return list(normalize_result)

    item = re.sub(r'[(\[{][^(){}[\]]*[)\]}]', '', item).strip()  # 去除所有括号及括号中内容
    item = re.sub(r'[.,\\!@#$%^*=+`~\"\';:<>?]', ' ', item).strip()  # 去除除/ - _ &外的特殊字符
    normalized = re.sub(r'[/]', ' ', item)  # 把/换为空格
    # normalized = re.split(r'[\[\(<{]', normalized)[0].strip()
    normalized = re.sub(r'-', ' ', normalized).lower()  # 把连字符-换为空格
    normalized = normalized.strip(".,/\\!@#$%^&*()-_=+`~\"';:<>?") # 去除字符串首尾的标点符号和特殊字符
    normalized = re.sub(r'\s+', ' ', normalized)
    normalize_result.add(normalized.strip())

    if any(special_symbol in item for special_symbol in SPECIAL_CHAR):
        normalized = item
        for special_symbol in SPECIAL_CHAR:
            if special_symbol in normalized:
                normalized = normalized.replace(special_symbol, '')
        normalized = normalized.strip(".,/\\!@#$%^&*()-_=+`~\"';:<>?")
        normalized = re.sub(r'\s+', ' ', normalized)
        normalize_result.add(normalized.strip())

    normalize_result.add(item.replace('_',' '))

    return list(normalize_result)

# =========================
# 7. Main abbreviation function
# =========================
def abbreviate(
        phrase: str,
        max_part_len: int = 8,
        max_abbr_len: int = 8
) -> Set[str]:
    """
    Generate all possible abbreviations for a phrase.

    Args:
        phrase: Input phrase to abbreviate
        max_part_len: Maximum length for individual word abbreviations
        max_abbr_len: Maximum length for combined abbreviations

    Returns:
        Set of valid abbreviations (excluding the original phrase)
    """
    phrase_stripped = phrase.strip()

    if len(phrase_stripped) <= 3:
        return set(phrase_stripped)

    def _abbreviate_tokens(tokens: Tuple[str]) -> Set[str]:
        """Helper to abbreviate based on token list."""
        if not tokens:
            return set()

        if len(tokens) == 1:
            abbreviations = word_candidates(tokens[0], max_part_len)
        else:
            # Use list to preserve order and duplicates
            candidate_groups = [
                (word, word_candidates(word, max_part_len))
                for word in tokens
            ]
            abbreviations = combine_word_candidates(candidate_groups, max_abbr_len, max_part_len)

        # Exclude the original phrase (normalized)
        res = []
        for abbr in abbreviations:
            if len(re.sub(r'[^a-zA-Z0-9]', '', abbr.lower())) < len(abbr) / 2:  # 特殊情况如__s_，符号数过多
                continue
            if abbr != phrase.lower() and abbr != phrase.replace(' ', '').lower() or (
                    abbr == phrase.replace(' ', '').lower() and len(abbr) < 8):
                if handler.check_abbr_simple(phrase.lower(), abbr, True):
                    res.append(abbr)
        return res
        # return {abbr for abbr in abbreviations if abbr != phrase.lower()and handler.check_abbr(phrase.lower(),abbr,True)}
    # 目前通过ngram_split的normalize_token函数处理之后所有代码元素都会变成小写形式，所以这里的驼峰分词应该没用
    abbreviations = set()
    tokens_without_splitting_camelcase = tuple(tokenize(phrase, split_type=False))
    abbreviations.update(_abbreviate_tokens(tokens_without_splitting_camelcase))
    tokens_with_splitting_camelcase = tuple(tokenize(phrase, split_type="camel"))
    if tokens_with_splitting_camelcase != tokens_without_splitting_camelcase:
        abbreviations.update(_abbreviate_tokens(tokens_with_splitting_camelcase))
    tokens_with_splitting_tokenizer=tuple(tokenize(phrase,split_type="tokenizer"))
    if tokens_with_splitting_tokenizer != tokens_without_splitting_camelcase and tokens_with_splitting_tokenizer != tokens_with_splitting_camelcase:
        abbreviations.update(_abbreviate_tokens(tokens_with_splitting_tokenizer))
    return abbreviations


def abbreviate_new(
        phrase: str,
        max_part_len: int = 8,
        max_abbr_len: int = 8
) -> Set[str]:
    """
    New abbreviation generator for CodeSearch:
    keep high recall from original abbreviate() while adding pragmatic constraints
    to reduce low-quality/noisy candidates.

    Input/Output format is the same as abbreviate():
      - input: phrase, max_part_len, max_abbr_len
      - output: Set[str]
    """
    phrase_stripped = phrase.strip()
    if not phrase_stripped:
        return set()

    # Keep old behavior contract for very short phrase.
    if len(phrase_stripped) <= 3:
        return set(phrase_stripped)

    phrase_lower = phrase_stripped.lower()
    phrase_compact = re.sub(r"\s+", "", phrase_lower)

    # Code-search oriented soft constraints
    MIN_ABBR_LEN = 2
    # Upper bound is adaptive: allow a bit longer than max_abbr_len for multi-word full/partial forms.
    ABS_MAX_LEN = max(max_abbr_len + 2, int(len(phrase_compact) * 0.9))
    # Require abbreviation to be sufficiently shorter than full phrase in compact form.
    # (except very short phrases)
    MAX_RELATIVE_RATIO = 0.9
    # Non-alnum too high => noisy candidate
    MIN_ALNUM_RATIO = 0.6

    def _tokenize_variants(p: str) -> List[Tuple[str, ...]]:
        variants: List[Tuple[str, ...]] = []
        t0 = tuple(tokenize(p, split_type=False))
        if t0:
            variants.append(t0)

        t1 = tuple(tokenize(p, split_type="camel"))
        if t1 and t1 != t0:
            variants.append(t1)

        t2 = tuple(tokenize(p, split_type="tokenizer"))
        if t2 and t2 != t0 and t2 != t1:
            variants.append(t2)

        return variants

    def _is_noise_candidate(abbr: str, tokens: Tuple[str, ...]) -> bool:
        a = abbr.strip()
        if not a:
            return True

        compact = re.sub(r"[^a-zA-Z0-9]", "", a.lower())
        if not compact:
            return True

        # length constraints
        if len(compact) < MIN_ABBR_LEN:
            return True
        if len(compact) > ABS_MAX_LEN:
            return True

        # symbol ratio constraint
        if len(compact) < len(a) * MIN_ALNUM_RATIO:
            return True

        # Should usually be shorter than full phrase
        if len(phrase_compact) >= 6 and len(compact) >= int(len(phrase_compact) * MAX_RELATIVE_RATIO):
            return True

        # Exclude identical full forms
        if compact == phrase_compact:
            return True

        # Remove obvious stop-word dominated artifacts for multi-word phrases:
        # if all alphabetic chars come from stop-words and abbreviation length is short.
        if len(tokens) > 1:
            content_tokens = [t for t in tokens if t and t not in STOP_WORDS]
            if not content_tokens and len(compact) <= 3:
                return True

        # Very weak patterns: repeated same char like "aaa", "__", etc.
        alpha_num = re.sub(r"[^a-zA-Z0-9]", "", a)
        if len(alpha_num) >= 3 and len(set(alpha_num.lower())) == 1:
            return True

        return False

    def _collect_from_tokens(tokens: Tuple[str, ...]) -> Set[str]:
        if not tokens:
            return set()

        if len(tokens) == 1:
            raw_candidates = word_candidates(tokens[0], max_part_len)
        else:
            candidate_groups = [
                (word, word_candidates(word, max_part_len))
                for word in tokens
            ]
            raw_candidates = combine_word_candidates(candidate_groups, max_abbr_len, max_part_len)

        cleaned: Set[str] = set()
        for abbr in raw_candidates:
            if _is_noise_candidate(abbr, tokens):
                continue

            # Final validity check reused from current pipeline
            if handler.check_abbr_simple(phrase_lower, abbr, True):
                cleaned.add(abbr)

        # Add first-letter acronym for multi-word phrases (high value in code-search)
        if len(tokens) >= 2:
            initials = "".join(t[0] for t in tokens if t)
            if initials and not _is_noise_candidate(initials, tokens):
                if handler.check_abbr_simple(phrase_lower, initials, True):
                    cleaned.add(initials)

        return cleaned

    result: Set[str] = set()
    for tv in _tokenize_variants(phrase_stripped):
        result.update(_collect_from_tokens(tv))

    # Safety post-filter: keep deterministic stable outputs.
    # 1) Remove overly long surface forms.
    # 2) Keep only meaningful alnum ratio.
    final_set = {
        x for x in result
        if x
        and len(re.sub(r"[^a-zA-Z0-9]", "", x)) >= MIN_ABBR_LEN
        and len(re.sub(r"[^a-zA-Z0-9]", "", x)) <= ABS_MAX_LEN
        and len(re.sub(r"[^a-zA-Z0-9]", "", x)) >= len(x) * MIN_ALNUM_RATIO
    }

    return final_set

# a=abbreviate_new("auth_check")
# b=abbreviate("auth_check")
# c=1
# def build_corpus_maps(corpus):
#     normalized_to_original = {}
#     for item in corpus:
#         if len(item) > 40 and '_' in item:
#             continue
#         normalized = re.sub(r'[/]', ' ', item)
#         normalized = re.split(r'[\[\(<]', normalized)[0].strip()
#         normalized = re.sub(r'-', ' ', normalized).lower()
#         normalized = normalized.strip(".,/\\!@#$%^&*()-_=+`~\"';:<>?")
#         normalized = re.sub(r'\s+', ' ', normalized)
#         normalized_to_original.setdefault(normalized, []).append(item)
#         # 特殊情况："High-Level Data Link Control"->HighLevel Data Link Control
#         if '-' in item:
#             normalized = item.replace('-', '')
#             normalized = re.split(r'[\[\(<]', normalized)[0].strip()
#             normalized = normalized.strip(".,/\\!@#$%^&*()-_=+`~\"';:<>?")
#             normalized = re.sub(r'\s+', ' ', normalized)
#             normalized_to_original.setdefault(normalized, []).append(item)
#         if '/' in item:  # 特殊情况："High Speed Printer/Processor"->high speed printerprocessor
#             normalized = item.replace('/', '')
#             normalized = re.split(r'[\[\(<]', normalized)[0].strip()
#             normalized = normalized.strip(".,/\\!@#$%^&*()-_=+`~\"';:<>?")
#             normalized = re.sub(r'\s+', ' ', normalized)
#             normalized_to_original.setdefault(normalized, []).append(item)
#
#     normalized_corpus = frozenset(normalized_to_original.keys())
#     compact_to_original = {}
#
#     for nrm in normalized_corpus:
#         compact = nrm.replace(' ', '')
#         compact_to_original.setdefault(compact, []).extend(
#             normalized_to_original[nrm]
#         )
#
#     compact_corpus = frozenset(compact_to_original.keys())
#     return normalized_corpus, normalized_to_original, compact_to_original, compact_corpus


def pair(corpus: Set[str], verbose: bool = False) -> List[Tuple[str, str]]:
    """
    Find phrase-abbreviation pairs within a corpus.

    For each phrase, generates abbreviations and checks if any match other phrases
    in the corpus (with spaces normalized).

    Args:
        corpus: Set of phrases to analyze
        verbose: Whether to print progress information

    Returns:
        List of (phrase, matching_abbreviation) tuples
    """
    corpus = set(corpus)

    # Build normalized corpus mapping: normalized -> [original items]
    normalized_corpus, normalized_to_original, compact_to_original, compact_corpus = build_corpus_maps(corpus)

    results = defaultdict(list)
    for phrase in tqdm(normalized_corpus):
        abbreviations = abbreviate(phrase, max_part_len=8, max_abbr_len=8)
        if verbose:
            print(f"Phrase: {normalized_to_original[phrase]} => Abbrs: {abbreviations}")

        # Find matches in normalized corpus (set intersection is faster than loop)
        matching_abbrs = abbreviations & normalized_corpus

        # Collect all matches at once
        for abbr in matching_abbrs:
            for full, abbr in itertools.product(normalized_to_original[phrase], compact_to_original[abbr]):
                if full != abbr:
                    results[full].append(abbr)
    return results


ABBR_RESULT_DIR=""
EXPERIMENT_LABEL = "0106"
MATCH_TYPE = "entity_to_code_identifier"
DETECT_FINAL_RESULT_DIR = f"{ABBR_RESULT_DIR}/{EXPERIMENT_LABEL}/{MATCH_TYPE}"
CHECKPOINT_FILE = f"{ABBR_RESULT_DIR}/{EXPERIMENT_LABEL}/{MATCH_TYPE}/checkpoint.json"
CHUNK_OUTPUT_DIR = f"{ABBR_RESULT_DIR}/{EXPERIMENT_LABEL}/{MATCH_TYPE}/chunk_outputs"
DETECT_RESULT_OUTPUT_FILE = f"{ABBR_RESULT_DIR}/{EXPERIMENT_LABEL}/{MATCH_TYPE}/final_result_all.json"
DETECT_FINAL_RESULT_POST_PROCESSED_PATH = f'{DETECT_FINAL_RESULT_DIR}/final_result_all_post_processed.json'
REFLECT_RESULT_PATH = f"{ABBR_RESULT_DIR}/{EXPERIMENT_LABEL}/{MATCH_TYPE}/reflect_name_str_to_db_entity_id_all.json"
REFLECT_CHUNK_OUTPUT_DIR = f"{ABBR_RESULT_DIR}/{EXPERIMENT_LABEL}/{MATCH_TYPE}/reflect_chunk_outputs"
PROCESSED_CHUNK_ID = []
CHUNK_LOG_DIR = f"{ABBR_RESULT_DIR}/{EXPERIMENT_LABEL}/{MATCH_TYPE}/log"
SPECIAL_CHAR = ['-', '/']
# FULL_NAME_NUMBER=0


def gen_special_char_combinations():
    results = []
    for mask in itertools.product([0, 1], repeat=len(SPECIAL_CHAR)):
        selected = [ch for ch, m in zip(SPECIAL_CHAR, mask) if m]
        results.append(selected)
    return results




# 给用于生成子序列的实体normalize（这里不需要compact，因为对于短语来说进行compact会导致多个词变为一个词，生成子序列可能会生成很多无效子序列）
def build_detect_maps(entities):
    normalized_to_original = {}
    for item in entities:
        normalize_result = normalize_entity(item)
        for normalized in normalize_result:
            normalized_to_original.setdefault(normalized, []).append(item)
    normalized_corpus = list(normalized_to_original.keys())
    return normalized_corpus, normalized_to_original


# 给corpus内的实体normalize
def build_corpus_maps(corpus):
    normalized_to_original = {}
    for item in corpus:
        normalize_result = normalize_entity(item)
        for normalized in normalize_result:
            normalized_to_original.setdefault(normalized, []).append(item)

    compact_to_original = {}
    for normalized, originals in normalized_to_original.items():
        compact = normalized.replace(" ", "")
        compact_to_original.setdefault(compact, []).extend(originals)

    compact_corpus = frozenset(compact_to_original.keys())
    return compact_corpus, compact_to_original

def pair_worker(chunk_id, chunk, compact_corpus_set, normalized_detect_entity_to_original,
                compact_corpus_entity_to_original):
    local_results = defaultdict(set)
    log = open(f"{CHUNK_LOG_DIR}/chunk_{chunk_id}.log", "a")
    log.write(f"[START] chunk {chunk_id} | Time:{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    log.flush()
    for i, phrase in enumerate(chunk):
        log.write(
            f"[PROCESS] phrase {phrase} {i + 1}/{len(chunk)} | Time:{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        log.flush()
        if len(phrase) > 50:
            continue

        import math
        max_abbr_len = math.ceil(len(phrase.replace(' ', '')) * 0.8)
        abbrs = abbreviate(phrase, max_part_len=8 if 8 <= max_abbr_len else int(max_abbr_len * 0.8),
                           max_abbr_len=max_abbr_len)
        matches = abbrs & compact_corpus_set

        if matches:
            full_items = normalized_detect_entity_to_original[phrase]
            for abbr in matches:
                for full, ab in itertools.product(full_items, compact_corpus_entity_to_original[abbr]):
                    if full != ab:
                        local_results[full].add(ab)

    out_path = f"{CHUNK_OUTPUT_DIR}/chunk_{chunk_id}.json"
    with open(out_path, "w") as f:
        json.dump(
            {k: list(v) for k, v in local_results.items()},
            f,
            ensure_ascii=False
        )

    log.write(f"[FINISH] chunk {chunk_id} | Time:{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    log.flush()
    return chunk_id


def load_checkpoint():
    if not os.path.exists(CHECKPOINT_FILE):
        return {"processed_chunks": []}
    with open(CHECKPOINT_FILE) as f:
        return json.load(f)


def save_checkpoint(checkpoint):
    with open(CHECKPOINT_FILE, "w") as f:
        json.dump(checkpoint, f, indent=2)


def parallel_pair(entities_to_detect, corpus, n_process=None, chunk_size=5000):
    if n_process is None:
        n_process = cpu_count()

    # 构造映射关系
    print("Building mapping...")
    detect_norm, detect_entity_norm_to_original = build_detect_maps(entities_to_detect)
    compact_corpus, compact_courpus_entity_to_original = build_corpus_maps(corpus)  # 缩写形式内不可能有空格，所以用于找缩写的集合里的元素应该把空格去掉

    # chunks = [
    #     normalized_corpus[i:i+chunk_size]
    #     for i in range(0, len(normalized_corpus), chunk_size)
    # ]
    chunks = [
        detect_norm[i:i + chunk_size]
        for i in range(0, len(detect_norm), chunk_size)
    ]

    print(f"Launching {n_process} processes, {len(chunks)} chunks...")
    cp = load_checkpoint()
    processed_chunks = set(cp.get("processed_chunks", []))

    todo = [(i, chunks[i]) for i in range(len(chunks)) if i not in PROCESSED_CHUNK_ID]
    print(f"Need to process {len(todo)} chunks")

    worker = partial(
        pair_worker,
        compact_corpus_set=compact_corpus,
        # normalized_to_original=normalized_to_original,
        normalized_detect_entity_to_original=detect_entity_norm_to_original,
        # compact_to_original=compact_to_original
        compact_corpus_entity_to_original=compact_courpus_entity_to_original
    )

    with Pool(processes=n_process) as pool:
        for chunk_id in tqdm(pool.starmap(worker, todo), total=len(todo)):
            processed_chunks.add(chunk_id)
            save_checkpoint({"processed_chunks": list(processed_chunks)})

    print("All chunks processed.")
    return processed_chunks


# 把所有chunk的结果合并起来（可能不同chunk的字典内有些相同的key，这种要把结果合并）
def merge_chunk_files():
    final_result = defaultdict(set)

    for fname in os.listdir(CHUNK_OUTPUT_DIR):
        if not fname.endswith(".json"):
            continue
        with open(f"{CHUNK_OUTPUT_DIR}/{fname}", 'r') as f:
            data = json.load(f)  # {entityname1:[abbrs],entityname2:[abbrs]}
        for k, v in data.items():
            post_processed_v = []
            for abbr in v:
                processed_abbr = abbr.strip(".,/\\!@#$%^&*()-_=+`~\"';:<>?")
                if len(abbr.strip()) == 1:  # 去除缩写长度为1的
                    continue
                if re.match(rf'^{re.escape(processed_abbr)}($|[^A-Za-z])',
                            k) is None:  # 后处理，去除abbr为全称的前缀加一个符号的情况，如tes和tes_AL
                    post_processed_v.append(abbr)
            final_result[k].update(post_processed_v)

    final_result = {
        k: sorted(list(v))
        for k, v in final_result.items()
    }

    with open(DETECT_RESULT_OUTPUT_FILE, "w") as f:
        json.dump(final_result, f, indent=2, ensure_ascii=False)

    print(f"Final merged result saved to {DETECT_RESULT_OUTPUT_FILE}")



import random, json


def test(sample_num=100):
    nums = random.sample(range(0, 3701), sample_num)
    with open('./abbreviation_dict.json', 'r') as f:
        datas = json.load(f)
        sample_keys = [list(datas.keys())[i] for i in nums]
        ground_truth = []
        corpus = []
        for key in sample_keys:
            if '+' in datas[key]:
                corpus.append(key)
                for ans in datas[key].split('+'):
                    if len(ans) > 50:
                        continue
                    ground_truth.append((ans.strip(), key.strip()))
                    corpus.append(ans.strip())
                continue

            if len(key) > 50 or len(datas[key]) > 50 and '_' in datas[key]:
                continue

            ground_truth.append((datas[key].strip(), key.strip()))
            corpus.append(datas[key].strip())
            corpus.append(key.strip())

        match_res = pair(corpus)
    matched = []
    for long_name, abbr_res in match_res.items():
        for res in abbr_res:
            if (long_name, res) in ground_truth:
                matched.append((long_name, res))
    print(len(matched) / len(ground_truth))

    miss_res = []
    for gt in ground_truth:
        if gt not in matched:
            miss_res.append(gt)
    with open('./test_result_3000_1217.json', 'w') as f:
        json.dump({"matched": matched, "missed": miss_res, "match_res": match_res, "corpus": corpus,
                   "recall": len(matched) / len(ground_truth)}, f, indent=4,
                  ensure_ascii=False)


# =========================
# 8. Main execution
# =========================
if __name__ == "__main__":
    # print(pair([
    #         "Dual In-line Package",
    #         "dip"
    #     ]))
    # abbreviate("dpcsrx_rx_cntl__dpcs_rx_lane0_en_mask")
    test(3000)
