from datetime import datetime
from multiprocessing import Pool, cpu_count
from functools import partial
from tqdm import tqdm
import itertools
import re
import json
import os
from collections import defaultdict

from codesense.expansion.abbreviate import pair, abbreviate

from codesense.config import ABBR_RESULT_DIR
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


def normalize_entity(item: str):
    normalize_result = set()
    if len(item) > 40 and '_' in item:
        return list(normalize_result)

    item = re.sub(r'[(\[{][^(){}[\]]*[)\]}]', '', item).strip()  # 去除所有括号及括号中内容
    item = re.sub(r'[.,\\!@#$%^*=+`~\"\';:<>?]', ' ', item).strip()  # 去除除/ - _ &外的特殊字符
    normalized = re.sub(r'[/]', ' ', item)  # 把/换为空格
    # normalized = re.split(r'[\[\(<{]', normalized)[0].strip()
    normalized = re.sub(r'-', ' ', normalized).lower()  # 把连字符-换为空格
    normalized = normalized.strip(".,/\\!@#$%^&*()-_=+`~\"';:<>?")
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
    return list(normalize_result)


# normalize_entity("asoc/intel")

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
        # print(phrase)
        # if phrase=="page table cache":
        #     a=1
        log.write(
            f"[PROCESS] phrase {phrase} {i + 1}/{len(chunk)} | Time:{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        log.flush()
        if len(phrase) > 50:
            # FULL_NAME_NUMBER-=1
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
    # phrases = entity_results
    # normalized_corpus, normalized_to_original, compact_to_original, compact_corpus = build_corpus_maps(phrases)
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


# 把匹配到的实体名用名字到实体的字典映射回具体的数据库表内的实体项
def reflect_name_str_to_DBentity(detect_abbr_final_result_path,
                                 full_name_to_db_entity_dict_path,
                                 abbr_to_db_entity_dict_path,
                                 refelct_result_path,
                                 detect_entity_is_ngramed=False,
                                 corpus_entity_is_ngramed=False,
                                 detect_entity_ngram_dict_path=None,
                                 corpus_entity_ngram_dict_path=None
                                 ):
    with open(full_name_to_db_entity_dict_path, 'r') as f:
        full_name_dict = json.load(f)
    if full_name_to_db_entity_dict_path == abbr_to_db_entity_dict_path:
        abbr_dict = full_name_dict
    else:
        with open(abbr_to_db_entity_dict_path, 'r') as f:
            abbr_dict = json.load(f)
    with open(detect_abbr_final_result_path, 'r') as f:
        detect_result = json.load(f)
    reflect_result = {}
    # todo
    # detect_entity在结果中为full_name，若输入的detect_entity为ngram后的实体名，则需把它们映射回原本的数据库实体，加载相关的映射文件
    if detect_entity_is_ngramed:
        # {"full_name":full_name,"ngram_origin":detect_entity_ngram_to_origin_dict[full_name]}
        with open(detect_entity_ngram_dict_path, 'r') as f:
            detect_entity_ngram_dict = json.load(f)

    # todo
    # corpus_entity在结果中为full_name，若输入的detect_entity为ngram后的实体名，则需把它们映射回原本的数据库实体，加载相关的映射文件
    if corpus_entity_is_ngramed:
        # {"abbreviation":abbreviation,"ngram_origin":corpus_entity_ngram_to_origin_dict[abbreviation]}
        with open(corpus_entity_ngram_dict_path, 'r') as f:
            corpus_entity_ngram_dict = json.load(f)

    for full_name, short_name_list in tqdm(detect_result.items()):
        full_name_db_entities = []
        short_name_db_entities = []
        short_name_db_entities_dict = defaultdict(set)
        if detect_entity_is_ngramed:
            # todo
            # 把full_name映射回所有原始实体名，并记录这些实体名经ngram可以得到full_name，而full_name是匹配结果中的全称，记录这种转换关系
            full_name_ngram_to_origin_list = detect_entity_ngram_dict[
                full_name]  # 找到所有ngram之后为full_name的origin name（一个list）
            for origin_full_name in full_name_ngram_to_origin_list:  # 对于每一个origin name，找到实体名为这个name的实体，实体名为这个name的实体也是一个list
                full_name_origin_entities = full_name_dict[origin_full_name]
                full_name_entities_list = [entity["id"] for entity in full_name_origin_entities]
                full_name_db_entities = {"id": full_name_entities_list, "ngram": full_name}
                # full_name_db_entities.append({"id":entity["id"],"ngram":full_name})#在找到实体的id之后，还要记录其ngram分词后的子词，因为在匹配关系中是这个子词被当成了全称
        else:
            full_name_entities_list = [entity["id"] for entity in full_name_dict[full_name]]
            full_name_db_entities = {"id": full_name_entities_list}

        if corpus_entity_is_ngramed:
            # todo
            # 把short_name_list内的每个abbr映射回所有原始实体名，并记录这些实体名经ngram可以得到abbr，而abbr是匹配结果中的缩写列表中的其中之一，记录这种转换关系
            for abbr in short_name_list:
                abbr_ngram_to_origin_list = corpus_entity_ngram_dict[abbr]  # 找到所有ngram之后为abbr的origin name（一个list）
                for origin_abbr in abbr_ngram_to_origin_list:  # 对于每一个origin name，找到实体名为这个name的实体，实体名为这个name的实体也是一个list
                    abbr_origin_entities = abbr_dict[origin_abbr]
                    for entity in abbr_origin_entities:
                        short_name_db_entities_dict[abbr].add(
                            entity["id"])  # 一个abbr肯定会有很多实体都能经过ngram变为这个abbr，所以这里以abbr作为key，value不断添加这些实体的id能减少内存消耗
                        # short_name_db_entities.append({"id":entity["id"],"ngram":abbr})#在找到实体的id之后，还要记录其ngram分词后的子词，因为在匹配关系中是这个子词被当成了缩写

        else:
            for i, abbr in enumerate(short_name_list):
                short_name_entities_list = [entity["id"] for entity in abbr_dict[abbr]]
                short_name_db_entities.append({"id": short_name_entities_list})

        if len(short_name_db_entities) == 0:
            for k, v in short_name_db_entities_dict.items():
                short_name_db_entities.append({"id": list(v), "ngram": k})
        reflect_result[full_name] = {"full_name": full_name_db_entities, "abbreviation": short_name_db_entities}

    with open(refelct_result_path, 'w') as f:
        json.dump(reflect_result, f, indent=4, ensure_ascii=False)


def reflect_worker(
        chunk_id,
        items,  # List[(full_name, short_name_list)]
        full_name_dict,
        abbr_dict,
        detect_entity_is_ngramed,
        corpus_entity_is_ngramed,
        detect_entity_ngram_dict,
        corpus_entity_ngram_dict,
        reflect_chunk_output_dir=REFLECT_CHUNK_OUTPUT_DIR
):
    local_result = {}
    print("start mapping")
    for full_name, short_name_list in tqdm(items):
        full_name_db_entities = {}
        short_name_db_entities = []
        short_name_db_entities_dict = defaultdict(set)

        # ===== full_name 映射 =====
        if detect_entity_is_ngramed:
            origin_list = detect_entity_ngram_dict[full_name]
            all_ids = []
            for origin_name in origin_list:
                all_ids.extend(
                    entity["id"] for entity in full_name_dict[origin_name]
                )
            full_name_db_entities = {
                "id": all_ids,
                "ngram": full_name
            }
        else:
            full_name_db_entities = {
                "id": [e["id"] for e in full_name_dict[full_name]]
            }

        # ===== short_name 映射 =====
        if corpus_entity_is_ngramed:
            for abbr in short_name_list:
                origin_list = corpus_entity_ngram_dict[abbr]
                for origin in origin_list:
                    if origin not in list(abbr_dict.keys()):
                        continue
                    for entity in abbr_dict[origin]:
                        short_name_db_entities_dict[abbr].add(entity["id"])

            for k, v in short_name_db_entities_dict.items():
                short_name_db_entities.append({
                    "id": list(v),
                    "ngram": k
                })
        else:
            for abbr in short_name_list:
                short_name_db_entities.append({
                    "id": [e["id"] for e in abbr_dict[abbr]],
                    "name": abbr
                })

        local_result[full_name] = {
            "full_name": full_name_db_entities,
            "abbreviation": short_name_db_entities
        }

    out_path = f"{reflect_chunk_output_dir}/chunk_{chunk_id}.json"
    with open(out_path, "w") as f:
        json.dump(local_result, f, indent=2, ensure_ascii=False)

    return chunk_id


def parallel_reflect(
        detect_abbr_final_result_path,
        full_name_to_db_entity_dict_path,
        abbr_to_db_entity_dict_path,
        refelct_result_path,
        detect_entity_is_ngramed=False,
        corpus_entity_is_ngramed=False,
        detect_entity_ngram_dict_path=None,
        corpus_entity_ngram_dict_path=None,
        n_process=None,
        chunk_size=500
):
    if n_process is None:
        n_process = cpu_count()

    with open(full_name_to_db_entity_dict_path, 'r') as f:
        full_name_dict = json.load(f)
    if full_name_to_db_entity_dict_path == abbr_to_db_entity_dict_path:
        abbr_dict = full_name_dict
    else:
        with open(abbr_to_db_entity_dict_path, 'r') as f:
            abbr_dict = json.load(f)
    with open(detect_abbr_final_result_path, 'r') as f:
        detect_result = json.load(f)

    # todo
    # detect_entity在结果中为full_name，若输入的detect_entity为ngram后的实体名，则需把它们映射回原本的数据库实体，加载相关的映射文件
    detect_entity_ngram_dict = None
    if detect_entity_is_ngramed:
        # {"full_name":full_name,"ngram_origin":detect_entity_ngram_to_origin_dict[full_name]}
        with open(detect_entity_ngram_dict_path, 'r') as f:
            detect_entity_ngram_dict = json.load(f)

    # print(abbr_dict['sctp_sf_do_5_1B_init'])
    # todo
    # corpus_entity在结果中为full_name，若输入的detect_entity为ngram后的实体名，则需把它们映射回原本的数据库实体，加载相关的映射文件
    corpus_entity_ngram_dict = None
    if corpus_entity_is_ngramed:
        # {"abbreviation":abbreviation,"ngram_origin":corpus_entity_ngram_to_origin_dict[abbreviation]}
        with open(corpus_entity_ngram_dict_path, 'r') as f:
            corpus_entity_ngram_dict = json.load(f)

    def chunked_iterable(iterable, size):
        it = iter(iterable)
        while True:
            chunk = list(itertools.islice(it, size))
            if not chunk:
                return
            yield chunk

    items = list(detect_result.items())
    chunks = list(chunked_iterable(items, chunk_size))
    print(f"Need to process {len(chunks)} chunk")

    # final_result = {}

    worker_args = [
        (
            chunk_id,
            chunk,
            full_name_dict,
            abbr_dict,
            detect_entity_is_ngramed,
            corpus_entity_is_ngramed,
            detect_entity_ngram_dict,
            corpus_entity_ngram_dict
        )
        for chunk_id, chunk in enumerate(chunks)
    ]

    with Pool(n_process) as pool:
        for partial_res in tqdm(
                pool.starmap(reflect_worker, worker_args),
                total=len(worker_args)
        ):
            # final_result.update(partial_res)
            pass

    # with open(refelct_result_path,'w') as f:
    #     json.dump(final_result,f,indetn=4,ensure_ascii=False)
    print("All reflect chunks finished.")
    # return final_result


def merge_reflect_chunks(
        chunk_dir=REFLECT_CHUNK_OUTPUT_DIR,
        output_path=REFLECT_RESULT_PATH
):
    final_result = {}

    for fname in os.listdir(chunk_dir):
        if not fname.endswith(".json"):
            continue

        with open(os.path.join(chunk_dir, fname), "r") as f:
            data = json.load(f)

        for k, v in data.items():
            if k not in final_result:
                final_result[k] = v
            else:
                final_result[k]["abbreviation"].extend(
                    v.get("abbreviation", [])
                )

    with open(output_path, "w") as f:
        json.dump(final_result, f, indent=2, ensure_ascii=False)

    print(f"Final reflect result saved to {output_path}")


# 3027632
# ps -ef | grep python | grep -v grep


# 原来这里有一个 __main__ 块：内嵌 def test_in_benchmark()、for 循环，
# 并读 './benchmark.json' 等仓库里并不存在的文件——是早期跑基准留下的草稿。
# 那是一段程序，不是入口。已移除；要跑基准请在 scripts/ 下写正式入口，
# 数据放 data/ 并在 data/README.md 里写清怎么拿。
