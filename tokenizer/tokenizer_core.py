from srctoolkit.delimiter import Delimiter
from pathlib import Path
import sys

import tokenizer.sentencepiece_bpe_tokenizer as sentencepiece_bpe_tokenizer
import tokenizer.sentencepiece_unigram_tokenizer as sentencepiece_unigram_tokenizer
import re
import json
from wordfreq import zipf_frequency
from definition import TOKENIZER_DIR


def is_english_word(word, threshold=2.0):
    if not re.fullmatch(r"[A-Za-z]+", word):
        return False

    freq = zipf_frequency(word, "en")
    return freq >= threshold


def delimiter(word):
    # 返回全部小写的分词字符串
    result = Delimiter.split_camel(word)
    return result


# print(delimiter("ioctls"))
# print(delimiter("iomap"))
# print(delimiter("netlink"))
# print(delimiter("iostat"))

def bpe_delimiter(word, processed_word):
    tokenizer = sentencepiece_bpe_tokenizer.load_tokenizer(
        f"{TOKENIZER_DIR}/sentencepiece_bpe.model")
    # 如果是单词直接用bpe分词，如果是短语，找到那个不是英文单词的词再用bpe分词
    if ' ' not in word:
        tokens = sentencepiece_bpe_tokenizer.bpe_tokenizer_post_process(tokenizer, word.lower())
    else:
        words = processed_word.split()
        for i, w in enumerate(words):
            if not is_english_word(w):
                w = sentencepiece_bpe_tokenizer.bpe_tokenizer_post_process(tokenizer, w.lower())
                words[i] = ' '.join(w)
        tokens = words

    # with open('./line.txt','a') as f:
    #     f.write(f"BPE| {word} to {tokens}\n")
    #     f.flush()
    return re.sub(r'[^a-zA-Z0-9 ]', '', ' '.join(tokens))


def unigram_delimiter(word, processed_word):
    tokenizer = sentencepiece_unigram_tokenizer.load_tokenizer(
        f"{TOKENIZER_DIR}/sentencepiece_unigram.model")
    if ' ' not in word:
        tokens = sentencepiece_unigram_tokenizer.unigram_tokenizer_post_process(tokenizer, word.lower())
    else:
        words = processed_word.split()
        for i, w in enumerate(words):
            if not is_english_word(w):
                w = sentencepiece_unigram_tokenizer.unigram_tokenizer_post_process(tokenizer, w.lower())
                words[i] = ' '.join(w)
        tokens = words  # with open('./line.txt','a') as f:
    #     f.write(f"BPE| {word} to {tokens}\n")
    #     f.flush()
    return re.sub(r'[^a-zA-Z0-9 ]', '', ' '.join(tokens))


def enbale_trained_tokenizer(raw_word, processed_word):
    if raw_word == processed_word:  # 没有进行分词
        if ' ' in raw_word:  # raw_word是短语
            for word in processed_word.split():
                if not is_english_word(word):  # 如果处理后的短语内存在某个单词不是英文单词
                    return True
            return False
        else:  # raw_word是单词
            if is_english_word(processed_word):
                return False
            else:
                return True
    else:
        return False


def tokenizer(word, split_type="bpe"):
    processed_word = delimiter(word)
    if enbale_trained_tokenizer(word, processed_word):
        if split_type.lower() == "bpe":
            return bpe_delimiter(word, processed_word)
        else:
            return unigram_delimiter(word, processed_word)
    else:
        return processed_word

# print(tokenizer("login"))
# laam_tokenizer = AutoTokenizer.from_pretrained(
#     "/home/fdse/hzc/KernelConcept/models--mjbommar--linux-as-a-model-32M/snapshots/b4003a5b80ede5baf33b4dd3c6238a3955cfef21")
#
#
# def laam_delimiter(word):
#     return ' '.join(laam_tokenizer.tokenize(word))


if __name__ == "__main__":
    print(tokenizer("YouLaiBootApplication", 'bpe'))
    # with open('./code_identifier_ctags_parse_all.json', 'r') as f:
    #     datas = json.load(f)
    #
    #
    # def delimiter(word):
    #     # 返回全部小写的分词字符串
    #     result = Delimiter.split_camel(word)
    #     return result


    # result = []
    # for data in datas[:2000]:
    #     code_type = data["identifier_type"]
    #     split_camel = Delimiter.split_camel(data[f"{code_type}_name"])
    #     bpe_norm = kernel_delimiter(data[f"{code_type}_name"], "bpe")
    #     unigram_norm = kernel_delimiter(data[f"{code_type}_name"], "unigram")
    #     transformer_norm = laam_delimiter(data[f"{code_type}_name"])
    #     result.append({"name_en": data[f"{code_type}_name"], "eid": data["id"], "split_camel": split_camel,
    #                    "kernel_delimiter_bpe_norm": bpe_norm, "kernel_delimiter_unigram_norm": unigram_norm,
    #                    "transformer_norm": transformer_norm})
    # with open("./kernelTokenizer/code_identifier_split_comparison.json", "w") as f:
    #     json.dump(result, f, indent=4, ensure_ascii=False)

#     {'name_en': 'colorimetry', 'eid': 117, 'split_camel': 'colorimetry', 'kernel_delimiter_bpe_norm': 'col or im et ry', 'kernel_delimiter_unigram_norm': 'color im etry'}
# {'name_en': 'fmts', 'eid': 651, 'split_camel': 'fmts', 'kernel_delimiter_bpe_norm': 'fm ts', 'kernel_delimiter_unigram_norm': ' fmts'}
# {'name_en': 'symtype', 'eid': 1315, 'split_camel': 'symtype', 'kernel_delimiter_bpe_norm': 'sy mty pe', 'kernel_delimiter_unigram_norm': 'sy mty pe'}
# {'name_en': 'symtype', 'eid': 1316, 'split_camel': 'symtype', 'kernel_delimiter_bpe_norm': 'sy mty pe', 'kernel_delimiter_unigram_norm': 'sy mty pe'}

# with open("./kernelTokenizer/code_identifier_split_comparison.json","r")as f:
#     data=json.load(f)
# for d in data:
#     if d["split_camel"]!=d["kernel_delimiter_bpe_norm"] or d["split_camel"]!=d["kernel_delimiter_unigram_norm"]:
# print(d)