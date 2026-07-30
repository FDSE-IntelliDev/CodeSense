from functools import lru_cache

import sentencepiece as spm
import os
import re
from wordfreq import zipf_frequency
import json
from codesense.config import TOKENIZER_DIR
from codesense.config import OUTPUT_DIR
corpus_file = f"{TOKENIZER_DIR}/code_symbols.txt"
model_prefix = f"{TOKENIZER_DIR}/sentencepiece_unigram"

def build_corpus(symbols_json_path=f"{OUTPUT_DIR}/symbols_index.json", output_file=corpus_file):

    if not os.path.exists(symbols_json_path):
        raise FileNotFoundError(f"未找到符号索引文件: {symbols_json_path}")

    with open(symbols_json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    names = []
    seen = set()

    for item in data:
        name = item.get("name", "")
        if not isinstance(name, str):
            continue

        name = name.strip()
        if not name:
            continue

        # # 轻量过滤：避免把非常规噪声写入语料
        # # 允许字母、数字、下划线、点（如 package.Class.method）
        # if not re.fullmatch(r"[A-Za-z0-9_.]+", name):
        #     continue

        if name not in seen:
            seen.add(name)
            names.append(name)

    os.makedirs(os.path.dirname(output_file) or ".", exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as f:
        for n in names:
            f.write(n + "\n")

    print(f"语料构建完成: {output_file}")
    print(f"共写入代码元素名: {len(names)}")
    return {
        "input_file": symbols_json_path,
        "output_file": output_file,
        "count": len(names),
    }

# build_corpus('../output/youlai-boot-master-gt/symbols_index.json', corpus_file)


def train_kernel_unigram(corpus_file,
                         model_prefix="kernel_unigram",
                         vocab_size=7000,
                         character_coverage=0.98,
                         model_type="unigram"):
    spm.SentencePieceTrainer.train(
        input=corpus_file,
        model_prefix=model_prefix,
        vocab_size=vocab_size,
        character_coverage=character_coverage,
        model_type=model_type,
        user_defined_symbols=[],
        pad_id=0, unk_id=1, bos_id=-1, eos_id=-1,
        unk_piece="[UNK]",
        train_extremely_large_corpus=True,
        byte_fallback=False
    )

    print(f"Unigram模型训练完成, 模型文件: {model_prefix}.model, 词表: {model_prefix}.vocab")


@lru_cache(maxsize=4)
def load_tokenizer(model_file=f"{model_prefix}.model"):
    sp = spm.SentencePieceProcessor(model_file=model_file)
    return sp


# 词表是文件内容的纯函数，缓存它。原来 *_tokenizer_post_process 每调用一次
# 就要把整个 .vocab 重读并重新解析一遍，而建索引时每个符号都会走到这里。
@lru_cache(maxsize=4)
def load_vocab_freq(vocab_file=f"{model_prefix}.vocab"):
    vocab = {}
    with open(vocab_file, "r", encoding="utf-8") as f:
        for line in f:
            if "\t" not in line:
                continue
            token, freq = line.strip().split("\t")
            vocab[token] = float(freq)
    return vocab


def is_english_word(word, threshold=2.0):
    if not re.fullmatch(r"[A-Za-z]+", word):
        return False

    freq = zipf_frequency(word, "en")
    return freq >= threshold


def merge_tokens_by_vocab(tokens, vocab_freq):
    i = 0
    while i < len(tokens):
        tok = tokens[i]

        # 若不是单字符就跳过
        if len(tok) != 1:
            i += 1
            continue

        left_word = None
        right_word = None

        if not tok.isdigit() and not tok.isalpha():
            del tokens[i]
            continue
        # 如果左右两边都有子词，则比较两种合并方式所得新子词的概率
        if i > 0 and i < len(tokens) - 1:
            left_word = tokens[i - 1] + tok
            right_word = tok + tokens[i + 1]

            # 查询频率
            left_freq = vocab_freq.get(left_word, 1e9)
            right_freq = vocab_freq.get(right_word, 1e9)

            # logp数值越小越频繁
            if left_freq < right_freq or (not tokens[i + 1].isdigit() and not tokens[i + 1].isalpha()):
                org_left = tokens[i - 1]
                # 本来左侧子词为英文单词 但添加当前字母后左侧变得不是单词了 则不能给他添加
                # if not (is_english_word(org_left) and not is_english_word(left_word)):
                # 合并到左边
                tokens[i - 1] = left_word
                del tokens[i]
                # else:
                #     #无法合并 则直接删除单个字符
                #     del tokens[i]
            elif left_freq > right_freq or (not tokens[i - 1].isdigit() and not tokens[i - 1].isalpha()):
                org_right = tokens[i + 1]
                # 本来左侧子词为英文单词 但添加当前字母后不是单词了 则不能给他添加
                # if not (is_english_word(org_right) and not is_english_word(right_word)):
                # 合并到右边
                tokens[i + 1] = right_word
                del tokens[i]
                # else:
                #     del tokens[i]
            else:
                if (is_english_word(left_word) and is_english_word(right_word)) or (
                        not is_english_word(left_word) and not is_english_word(right_word)):
                    if zipf_frequency(left_word, "en") > zipf_frequency(right_word, "en"):
                        tokens[i - 1] = left_word
                        del tokens[i]
                    else:
                        tokens[i + 1] = right_word
                        del tokens[i]
                else:
                    if is_english_word(left_word):
                        tokens[i - 1] = left_word
                        del tokens[i]
                    else:
                        tokens[i + 1] = right_word
                        del tokens[i]
        elif i == 0:
            tokens[i + 1] = tok + tokens[i + 1]
            del tokens[i]
        else:
            tokens[i - 1] = tokens[i - 1] + tok
            del tokens[i]

    return tokens


def unigram_tokenizer(sp, text):
    tokens = sp.encode(text, out_type=str)
    tokens[0] = tokens[0].replace('▁', '')
    print(f"Input: {text}")
    print(f"Tokens: {tokens}")
    return tokens


def unigram_tokenizer_post_process(sp, text):
    tokens = sp.encode(text, out_type=str)
    tokens[0] = tokens[0].replace('▁', '')
    vocab_freq = load_vocab_freq()
    tokens = merge_tokens_by_vocab(tokens, vocab_freq)
    # print(f"Input: {text}")
    # print(f"Tokens: {tokens}")

    return tokens


def rerank_segmentations(sp, text, nbest=10, bonus_word=5.0):
    """
    使用 unigram N-best 分词 + 英文词 bonus 的 re-ranking
    """
    # 获取 N-best 分词结果（返回类似 [(pieces, score), ...]）
    try:
        nbests = sp.nbest_encode_as_pieces(text, nbest)
    except AttributeError:
        # 有些 sentencepiece 版本可能使用不同命名，尝试另外的接口
        # 也可以使用 sp.encode(text, out_type=str, nbest_size=nbest)（若支持）
        nbests = sp.encode(text, out_type=str, nbest_size=nbest)

    best_seg = None
    best_score = -1e18  # 负无穷

    for pieces, score in nbests:
        # score 是负的 logprob → 越小越差
        new_score = -score  # 越大越好

        # 遍历所有片段，根据英文词给予奖励
        for p in pieces:
            token = p.replace("▁", "")
            if is_english_word(token):
                new_score += bonus_word

        # 保存最佳 segmentation
        if new_score > best_score:
            best_score = new_score
            best_seg = pieces

    return best_seg


if __name__ == "__main__":
    train_kernel_unigram(corpus_file=corpus_file,
                     model_prefix=model_prefix,
                     vocab_size=700,
                     character_coverage=0.98,
                     model_type="unigram"
                     )

    # sp = load_tokenizer(model_file=f"{model_prefix}.model")
    # res = rerank_segmentations(sp, 'netlink', 10, 5.0)
    # unigram_tokenizer(sp, "iomap")
    # unigram_tokenizer(sp, "netlink")
    # unigram_tokenizer(sp, "iostat")
    # unigram_tokenizer(sp, "ioctl")
    # unigram_tokenizer(sp, "io_uring")
    # unigram_tokenizer(sp, "ioctls")
