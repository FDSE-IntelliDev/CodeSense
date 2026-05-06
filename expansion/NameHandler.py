#!/usr/bin/env python
# -*- coding: utf-8 -*-

import re
import textdistance
import itertools
import functools
import numpy as np

from expansion.spacy_nlp import SpacyNLP
import srctoolkit
from srctoolkit.delimiter import Delimiter
from tokenizer.tokenizer_core import tokenizer


class NameHandler:
    __INSTANCE = None

    def __init__(self):
        self.DLDis = textdistance.DamerauLevenshtein()
        self.nlp = SpacyNLP.get_inst()
        self.delimiter = srctoolkit.delimiter.Delimiter

    @functools.lru_cache(maxsize=10000)
    def WordNet_normalize(self, term):
        lemma = self.nlp.WordNet_lemmatize(term)
        name = Delimiter.split_camel(lemma)
        return name

    @functools.lru_cache(maxsize=10000)
    def spacy_normalize(self, term):
        lemma = self.nlp.spacy_lemmatize(term)
        if '/' in term:
            if " ".join(term.lower().split('/')) == lemma:
                return lemma
        # name = name_split(lemma)
        name =tokenizer(lemma)
        return name

    def check_synonym(self, long_term, short_term, max_dis=2, threshold=0.25):
        if long_term.isupper() and short_term.isupper():
            return False
        # if long_term.lower().rstrip("s") == short_term.lower().rstrip("s"):
        #     return True

        long_name = self.spacy_normalize(long_term)
        short_name = self.spacy_normalize(short_term)

        if long_name == short_name:
            return True
        if long_name.replace(" ", "") == short_name.replace(" ", ""):
            return True

        long_words = long_name.split()
        short_words = short_name.split()

        if len(long_words) != len(short_words):
            return False
        for word1, word2 in zip(long_words, short_words):
            if word1[0] != word2[0]:
                return False
            if re.findall(r'[0-9]+', word1) != re.findall(r'[0-9]+', word2):
                return False
            if self.DLDis.distance(word1, word2) > max_dis or self.DLDis.normalized_distance(word1, word2) >= threshold:
                return False
        return True

    def check_synonym_simplified(self, long_term, short_term, max_dis=2, threshold=0.25, is_normed=False):
        if not is_normed:
            long_name = self.spacy_normalize(long_term)
            short_name = self.spacy_normalize(short_term)
        else:
            long_name = long_term
            short_name = short_term

        if long_name == short_name:
            return True
        if long_name.replace(" ", "") == short_name.replace(" ", ""):
            return True

        long_words = long_name.split()
        short_words = short_name.split()

        for word1, word2 in zip(long_words, short_words):
            if self.DLDis.distance(word1, word2) > max_dis or self.DLDis.normalized_distance(word1, word2) >= threshold:
                return False
        return True

    @staticmethod
    def __check_prefix(long_name, short_name):
        if len(short_name) >= 2 and len(short_name) / len(long_name) < 2 / 3 and long_name.startswith(short_name):
            return True
        return False

    @staticmethod
    def __is_subsequence(a: str, b: str) -> bool:  # a是否为b的子序列,如pg应该是page的缩写，虽然pg不是前缀
        a = a.lower()
        b = b.lower()
        i = 0
        for ch in b:
            if i < len(a) and a[i] == ch:
                i += 1
            if i == len(a):
                return True
        return i == len(a)

    @staticmethod
    def __check_word_word(long_word, word):
        if (len(word) == 1 and word[0] == long_word[0]) or NameHandler.__check_prefix(long_word,
                                                                                      word) or NameHandler.__is_subsequence(
                word, long_word):
            return True
        return False

    @staticmethod
    def __check_phrase_word(phrase, word):
        words = phrase.split()
        if len(word) < len(words):
            return False
        if len(words) == 1:
            return NameHandler.__check_word_word(phrase, word)
        else:
            cur_word = words.pop(0)
            for indices in itertools.combinations([i for i in range(1, len(word), 1)], len(words) - 1):
                indices = (0,) + indices + (len(word),)
                pairs = [(beg, end) for (beg, end) in zip(indices[:-1], indices[1:])]
                beg, end = pairs.pop(0)
                cur_chars = word[beg:end]

                # print(cur_chars, cur_word)
                if cur_chars[0] == cur_word[0]:
                    if not NameHandler.__check_word_word(cur_word, cur_chars):
                        continue
                elif cur_word[0] in set("aeiou") and len(cur_word) > 1 and cur_chars[0] == cur_word[1]:
                    if end != 1:
                        continue
                else:
                    continue

                for (beg, end), cur_word in zip(pairs, words):
                    cur_chars = word[beg:end]
                    # print(cur_chars, cur_word)
                    if not NameHandler.__check_word_word(cur_word, cur_chars):
                        break
                else:
                    return True

            return False

    @staticmethod
    def __check_phrase_word_new(phrase, word):
        words = phrase.split()
        n = len(words)

        # 如果 word 长度 < 单词数量，必不可能构成缩写
        if len(word) < n:
            return False

        # 仅一个单词时，直接使用原子判断
        if n == 1:
            return NameHandler.__check_word_word(words[0], word)

        # 枚举所有切分方式，把 word 分成 n 段
        # 切分点数量 = n - 1
        L = len(word)
        cut_pattern = itertools.combinations(range(1, L), n - 1)
        for cuts in cut_pattern:
            indices = (0,) + cuts + (L,)
            segments = [word[indices[i]:indices[i + 1]] for i in range(n)]

            # 每段必须对应一个单词，否则拒绝
            ok = True
            for seg, w in zip(segments, words):
                # 段不能为空
                if not seg:
                    ok = False
                    break
                # 必须是合法缩写
                if not NameHandler.__check_word_word(w, seg):
                    ok = False
                    break

            if ok:
                return True

        return False

    def check_abbr(self, long_term, short_term, is_normed=False):
        long_term = re.sub(r'[(\[{][^(){}[\]]*[)\]}]', '', long_term).strip()
        short_term = re.sub(r'[(\[{][^(){}[\]]*[)\]}]', '', short_term).strip()
        if not is_normed:
            long_name = self.spacy_normalize(long_term)
            short_name = self.spacy_normalize(short_term)
        else:
            long_name = long_term
            short_name = short_term

        # 检查是否一方包含数字而另一方不包含
        long_has_digit = any(c.isdigit() for c in long_name)
        short_has_digit = any(c.isdigit() for c in short_name)
        if long_has_digit != short_has_digit:
            return False

        # 检查是否一方包含字母而另一方不包含
        long_has_alpha = any(c.isalpha() for c in long_name)
        short_has_alpha = any(c.isalpha() for c in short_name)
        if long_has_alpha != short_has_alpha:
            return False

        # 如果二者的数字部分也不相同则也不可能时缩写
        long_digit = "".join([c for c in long_name if c.isdigit()])
        short_digit = "".join([c for c in short_name if c.isdigit()])
        if long_digit != short_digit:
            return False

        # #如du和dump不互为缩写 但是dup和dump可能互为缩写 该规则有误，如memory和mem
        # if long_name.startswith(short_name):
        #     return False
        # print(long_name, short_name)

        if len(short_name.split()) == 1:
            return NameHandler.__check_phrase_word_new(long_name, short_name)
        else:
            short_words = short_name.split()
            long_words = long_name.split()
            if len(long_words) < len(short_words):
                return False
            while len(short_words) > 0 and short_words[0] == long_words[0]:
                short_words.pop(0)
                long_words.pop(0)
            while len(short_words) > 0 and short_words[-1] == long_words[-1]:
                short_words.pop(-1)
                long_words.pop(-1)
            if len(short_words) == 1:
                # print(long_words, short_words)
                return NameHandler.__check_phrase_word(" ".join(long_words), short_words[0])
            elif len(short_words) == len(long_words):
                for word1, word2 in zip(long_words, short_words):
                    if not NameHandler.__check_word_word(word1, word2):
                        return False
                return True
            return False

    def check_abbr_simple(self, long_term, short_term, is_normed=False):
        if not is_normed:
            long_name = self.spacy_normalize(long_term)
            short_name = self.spacy_normalize(short_term)
        else:
            long_name = long_term
            short_name = short_term

        # 检查是否一方包含数字而另一方不包含
        long_has_digit = any(c.isdigit() for c in long_name)
        short_has_digit = any(c.isdigit() for c in short_name)
        if long_has_digit != short_has_digit:
            return False

        # 检查是否一方包含字母而另一方不包含
        long_has_alpha = any(c.isalpha() for c in long_name)
        short_has_alpha = any(c.isalpha() for c in short_name)
        if long_has_alpha != short_has_alpha:
            return False

        # 如果二者的数字部分也不相同则也不可能时缩写
        long_digit = "".join([c for c in long_name if c.isdigit()])
        short_digit = "".join([c for c in short_name if c.isdigit()])
        if long_digit != short_digit:
            return False

        return True

    @classmethod
    def get_inst(cls):
        if not cls.__INSTANCE:
            cls.__INSTANCE = cls()
        return cls.__INSTANCE

# page table+pg table,page+pg
# nh=NameHandler()
# print(nh.check_abbr('tmiofb',"tmio",False))
# # # # # s=SpacyNLP.get_inst()
# print(nh.spacy_normalize("amdi12345"))
# print(nh.spacy_normalize("input/output"))
# # # # # # print(s.spacy_lemmatize("Type–length–value"))
# print(nh.check_abbr(nh.spacy_normalize('device drivers'),nh.spacy_normalize('device driver'),True))