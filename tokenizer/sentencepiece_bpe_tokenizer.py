import sentencepiece as spm
import os
from wordfreq import zipf_frequency
import re
import json
from definition import TOKENIZER_DIR,OUTPUT_DIR

corpus_file = f"{TOKENIZER_DIR}/code_symbols.txt"
model_prefix = f"{TOKENIZER_DIR}/sentencepiece_bpe"

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

def train_kernel_bpe(corpus_file,
                     model_prefix="kernel_bpe",
                     vocab_size=20000,
                     character_coverage=1.0,
                     model_type="bpe"):
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

    print(f"BPE 模型训练完成, 模型文件: {model_prefix}.model, 词表: {model_prefix}.vocab")


def load_tokenizer(model_file=f"{model_prefix}.model"):
    sp = spm.SentencePieceProcessor(model_file=model_file)
    return sp


def load_vocab_freq(vocab_file=f"{model_prefix}.vocab"):
    vocab = {}
    with open(vocab_file, "r", encoding="utf-8") as f:
        for line in f:
            if "\t" not in line:
                continue
            token, freq = line.strip().split("\t")
            vocab[token] = int(freq)
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


def bpe_tokenizer(sp, text):
    tokens = sp.encode(text, out_type=str)
    tokens[0] = tokens[0].replace('▁', '')
    print(f"Input: {text}")
    print(f"Tokens: {tokens}")
    return tokens


def bpe_tokenizer_post_process(sp, text):
    tokens = sp.encode(text, out_type=str)
    tokens[0] = tokens[0].replace('▁', '')
    vocab_freq = load_vocab_freq()
    tokens = merge_tokens_by_vocab(tokens, vocab_freq)
    # print(f"Input: {text}")
    # print(f"Tokens: {tokens}")

    return tokens


if __name__ == "__main__":
    train_kernel_bpe(corpus_file=corpus_file,
                     model_prefix=model_prefix,
                     vocab_size=4000,
                     character_coverage=0.98,#可以抛掉一些较少出现的如单个的字母
                     model_type="bpe"
                     )

    # sp = load_tokenizer(model_file=f"{model_prefix}.model")
    #
    # bpe_tokenizer(sp, "symtype")
    # bpe_tokenizer_post_process(sp, "symtype")
    # bpe_tokenizer(sp, "iostat")
    # bpe_tokenizer(sp, "ioctl")
    # bpe_tokenizer(sp, "io_uring")
    # bpe_tokenizer_post_process(sp, "ioctls")
