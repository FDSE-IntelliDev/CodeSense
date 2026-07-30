"""把代码标识符切成子词。

先按驼峰/下划线拆（srctoolkit），拆不动、且拆出来的片段不是英文单词时，
再交给训练好的 sentencepiece 模型（BPE 或 unigram）继续拆。
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from srctoolkit.delimiter import Delimiter
from wordfreq import zipf_frequency

import codesense.tokenizer.sentencepiece_bpe_tokenizer as bpe_backend
import codesense.tokenizer.sentencepiece_unigram_tokenizer as unigram_backend
from codesense.config import TOKENIZER_DIR

#: 齐普夫频率高于这个值就当成英文单词，不再送去 sentencepiece。
DEFAULT_ENGLISH_THRESHOLD = 2.0

_NON_ALNUM = re.compile(r"[^a-zA-Z0-9 ]")
_ALPHA_ONLY = re.compile(r"[A-Za-z]+")


def is_english_word(word: str, threshold: float = DEFAULT_ENGLISH_THRESHOLD) -> bool:
    """纯函数，没有状态、也不会有第二种实现，所以保持函数形态（规则 1）。"""
    if not _ALPHA_ONLY.fullmatch(word):
        return False
    return zipf_frequency(word, "en") >= threshold


def delimiter(word: str) -> str:
    """按驼峰/下划线拆，返回全小写的分词字符串。同样是纯函数。"""
    return Delimiter.split_camel(word)


class CodeTokenizer:
    """标识符分词器。

    模型句柄是**需要复用的状态**（ARCHITECTURE.md 规则 1）。原来这里是一排
    模块级函数，每调用一次 ``bpe_delimiter`` 就重新从磁盘加载一次 sentencepiece
    模型、并重新读一遍 ``.vocab``——而建索引时每个符号都要调一次。
    现在句柄挂在实例上，加载一次用到底。

    用法::

        tk = CodeTokenizer()
        tk.split("YouLaiBootApplication")            # 默认 bpe
        tk.split("iomap", split_type="unigram")

    换模型目录或阈值就换个实例。构造参数都有默认值，无参构造即可。
    """

    def __init__(
        self,
        tokenizer_dir: Optional[str] = None,
        split_type: str = "bpe",
        english_threshold: float = DEFAULT_ENGLISH_THRESHOLD,
    ) -> None:
        self.tokenizer_dir = Path(tokenizer_dir or TOKENIZER_DIR)
        self.split_type = split_type
        self.english_threshold = english_threshold
        self._models: dict[str, Any] = {}

    # ---------------------------------------------------------- 模型句柄

    def _model(self, kind: str) -> Any:
        """按需加载并缓存 sentencepiece 模型。"""
        if kind not in self._models:
            backend = bpe_backend if kind == "bpe" else unigram_backend
            name = "sentencepiece_bpe" if kind == "bpe" else "sentencepiece_unigram"
            self._models[kind] = backend.load_tokenizer(
                str(self.tokenizer_dir / f"{name}.model")
            )
        return self._models[kind]

    # ---------------------------------------------------------- 对外接口

    def split(self, word: str, split_type: Optional[str] = None) -> str:
        """把一个标识符切成空格分隔的子词串。"""
        processed = delimiter(word)
        if not self._needs_subword_model(word, processed):
            return processed
        return self._subword_split(word, processed, split_type or self.split_type)

    # ---------------------------------------------------------- 内部

    def _is_english(self, word: str) -> bool:
        return is_english_word(word, self.english_threshold)

    def _needs_subword_model(self, raw_word: str, processed_word: str) -> bool:
        """驼峰拆不动、且拆出来的片段不是英文单词时，才值得动用训练模型。"""
        if raw_word.lower() != processed_word:
            return False  # 驼峰已经把它拆开了
        if " " in raw_word:
            return any(not self._is_english(w) for w in processed_word.split())
        return not self._is_english(processed_word)

    def _subword_split(self, word: str, processed_word: str, split_type: str) -> str:
        use_bpe = split_type.lower() == "bpe"
        backend = bpe_backend if use_bpe else unigram_backend
        post = (
            backend.bpe_tokenizer_post_process
            if use_bpe
            else backend.unigram_tokenizer_post_process
        )
        model = self._model("bpe" if use_bpe else "unigram")

        if " " not in word:
            tokens = post(model, word.lower())
        else:
            # 短语：只把其中不是英文单词的那部分再切
            tokens = processed_word.split()
            for i, w in enumerate(tokens):
                if not self._is_english(w):
                    tokens[i] = " ".join(post(model, w.lower()))
        return _NON_ALNUM.sub("", " ".join(tokens))


@lru_cache(maxsize=1)
def _default_tokenizer() -> CodeTokenizer:
    """默认实例。让下面那个函数式入口也复用同一份模型句柄。"""
    return CodeTokenizer()


def tokenizer(word: str, split_type: str = "bpe") -> str:
    """函数式入口，签名与原来一致——五处调用点无需改动。

    内部复用同一个 :class:`CodeTokenizer`，所以模型只加载一次。
    新代码建议直接持有 ``CodeTokenizer`` 并作为依赖注入（规则 5）。
    """
    return _default_tokenizer().split(word, split_type)
