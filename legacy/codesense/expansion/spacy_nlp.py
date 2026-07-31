#!/usr/bin/env python
# -*- encoding: utf-8 -*-
'''
@Author  :   Chong Wang
@Contact :   chongwang18@fudan.edu.cn
@Time    :   2020/01/26
'''

import spacy
from nltk.stem import WordNetLemmatizer
import re
import functools


class SpacyNLP:
    __INSTANCE = None

    def __init__(self, disable=["ner"]):
        nlp = spacy.load("en_core_web_sm", disable=disable)
        # hyphen_re = re.compile(r"[A-Za-z\d]+-[A-Za-z\d]+|'[a-z]+|''|id|Id|ID")
        # 认为-——_等都算连词符 所以词性还原的时候不把这些单独提取出来 但是/这种肯定不是连词符就要单独提取出来作为一个特殊符号
        hyphen_re = re.compile(
            r"[A-Za-z\d]+(?:[-–—][A-Za-z\d]+)+|'[a-z]+|''|id|Id|ID"
        )
        prefix_re = spacy.util.compile_prefix_regex(nlp.Defaults.prefixes)
        infix_re = spacy.util.compile_infix_regex(nlp.Defaults.infixes)
        suffix_re = spacy.util.compile_suffix_regex(nlp.Defaults.suffixes)
        nlp.tokenizer = spacy.tokenizer.Tokenizer(nlp.vocab, prefix_search=prefix_re.search, infix_finditer=infix_re.finditer,
                                                suffix_search=suffix_re.search, token_match=hyphen_re.match)
        self.nlp = nlp
        self.WordNet_lemmatizer = WordNetLemmatizer()
        self.spacy_lemmatizer = spacy.load('en_core_web_sm')


    @functools.lru_cache(maxsize=100000)
    def parse(self, text):
        doc = self.nlp(text)
        return doc

    @functools.lru_cache(maxsize=100000)
    def WordNet_lemmatize(self, noun):
        lower = noun.lower()
        lemma = self.WordNet_lemmatizer.lemmatize(lower, "n")
        return lemma

    @functools.lru_cache(maxsize=100000)
    def spacy_lemmatize(self, noun):
        # doc = self.nlp(noun)
        # token = list(doc)[0]
        # return token.lemma_
        doc = self.nlp(noun)
        lemmas=[]
        for token in doc:
            if not token.is_punct and not token.is_space:
                lemmas.append(token.lemma_)
        # lemmas = [token.lemma_ for token in doc if not token.is_punct and not token.is_space]
        if len(lemmas) == 1 and len(lemmas[0])==1:
            return noun
        return " ".join(lemmas).lower()

    def lemma_phrase(self,text):
        doc = self.nlp(text)
        out = []
        for tok in doc:
            lemma = self.WordNet_lemmatizer.lemmatize(tok.text.lower(), "n")
            out.append(lemma)
        return " ".join(out)

    @classmethod
    def get_inst(cls):
        if not cls.__INSTANCE:
            cls.__INSTANCE = cls()
        return cls.__INSTANCE

#
s=SpacyNLP.get_inst()
# # # print(s.lemma_phrase('intel_pstate'))
# print(s.spacy_lemmatize("Kcontrols"))
# print(s.spacy_lemmatize("asoc/sof/pci/intel"))
# print(s.WordNet_lemmatize("kernel controls"))