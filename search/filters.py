import json
from definition import OUTPUT_DIR, BASE_URL, API_KEY, BASE_MODEL
from utils.file_utils import load_res,save_res
import re
from openai import OpenAI
from parsers.read_tools import get_symbol_code

def filter_symbols_by_type(search_result_path: str, semQL_path: str) -> list:
    """
    根据 semQL 中的 target 字段对代码元素的结果进行类型过滤。

    :param search_result_path: 搜索结果列表文件的路径 (例如 invert_index_search_result.json)
    :param semQL_path: semQL 查询文件的路径 (例如 query_dsl_result.json)
    :return: 过滤后的结果列表
    """
    # 1. 从文件读取已获的搜索结果
    search_results = load_res(search_result_path)

    # 2. 从文件读取 semQL 字典对象
    semql_query = load_res(semQL_path)

    target = semql_query.get("target")

    # 如果 semQL 中没有指定 target，或者 target 是空的，直接返回全量结果
    if not target:
        return search_results

    if isinstance(target, str):
        allowed_types = {target.lower().strip()}
    elif isinstance(target, list):
        allowed_types = {str(t).lower().strip() for t in target}
    else:
        return search_results

    filtered_results = []
    exclude_results=[]

    for symbol in search_results:
        symbol_type = symbol.get("type", "")

        # 判断当前代码元素的 type 是否在目标 type 集合中
        # 忽略大小写进行匹配
        if symbol_type.lower().strip() in allowed_types:
            filtered_results.append(symbol)
        else:
            exclude_results.append(symbol)

        save_res(f'{OUTPUT_DIR}/youlai-boot-master/filtered_by_type.json', filtered_results)
        save_res(f'{OUTPUT_DIR}/youlai-boot-master/exclude_by_type.json', exclude_results)

    return filtered_results


def filter_symbols_semantically(search_result_path: str, semQL_path: str) -> list:
    """
    基于 LLM 的语义过滤方法，根据 semQL 对代码元素进行深度语义上的筛选。
    """
    search_results = load_res(search_result_path)
    semql_query = load_res(semQL_path)

    client = OpenAI(base_url=BASE_URL, api_key=API_KEY)

    # 提取有用的约束信息给 LLM，防止提示词过长
    constraints = {
        "intent": semql_query.get("intent"),
        "filters": semql_query.get("filters"),
        "exclude": semql_query.get("exclude"),
        "raw_query": semql_query.get("raw_query")
    }

    filtered_results = []
    exclude_results = []

    BATCH_SIZE = 10

    for i in range(0, len(search_results), BATCH_SIZE):
        batch = search_results[i:i + BATCH_SIZE]
        batch_context = []

        for idx, symbol in enumerate(batch):
            symbol_file = symbol.get("file", "")
            code_content = get_symbol_code("", symbol)
            batch_context.append({
                "id": str(idx),
                "name": symbol.get("name"),
                "type": symbol.get("type"),
                "filepath": symbol_file,
                "content": code_content
            })

        prompt = f"""
You are an expert code analyst.
Your task is to review a batch of provided code elements, generate a natural language summary (semantic tag) of core functionality for each, and determine if each satisfies the given search constraints.

Analysis Requirements:
1. Carefully analyze each code element and summarize its core functionality.
2. Evaluate whether the code element matches based on the `intent` (expected behavior and objects), `filters` (expected concepts, roles, or relationships), and `exclude` (functionalities that should be explicitly avoided). If the code implements anything listed in `exclude`, it should NOT be a match.
3. Output the result STRICTLY as a JSON array containing an evaluation object for each input element. You must return exactly {len(batch)} objects corresponding to each "id". Do not include any markdown formatting outside the JSON array.

Output Format Example:
[
  {{
    "id": "0",
    "semantic_summary": "This method is used to...",
    "matches_intent": true,
    "fails_exclude": false,
    "is_match": true,
    "reason": "Brief explanation..."
  }},
  {{
    "id": "1",
    ...
  }}
]

[Search Constraints]:
{json.dumps(constraints, ensure_ascii=False, indent=2)}

[Code Elements Batch]:
{json.dumps(batch_context, ensure_ascii=False, indent=2)}
"""

        try:
            resp = client.chat.completions.create(
                model=BASE_MODEL,
                messages=[
                    {"role": "system", "content": "You are a precise code analysis assistant. Output only a valid JSON array."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.0
            )
            raw_text = getattr(resp.choices[0].message, "content", "")

            # 匹配 JSON 数组
            m = re.search(r"\[[\s\S]*\]", raw_text)
            if m:
                payload = json.loads(m.group(0))
                # 将结果转为以 id 为 key 的字典以便快速查找
                result_map = {str(item.get("id")): item for item in payload if isinstance(item, dict)}

                for idx, symbol in enumerate(batch):
                    res = result_map.get(str(idx), {})
                    symbol["semantic_summary"] = res.get("semantic_summary", "")
                    symbol["filter_reason"] = res.get("reason", "")

                    if res.get("is_match") is True:
                        filtered_results.append(symbol)
                    else:
                        exclude_results.append(symbol)
            else:
                # 解析失败，把整批视为 exclude，防止阻断主流程
                exclude_results.extend(batch)

        except Exception as e:
            print(f"Error evaluating batch starting at index {i}: {e}")
            exclude_results.extend(batch)

    save_res(f'{OUTPUT_DIR}/youlai-boot-master/filtered_by_semantics.json', filtered_results)
    save_res(f'{OUTPUT_DIR}/youlai-boot-master/exclude_by_semantics.json', exclude_results)

    return filtered_results


# filtered_res=filter_symbols_by_type(f'{OUTPUT_DIR}/youlai-boot-master/invert_index_search_result.json',f'{OUTPUT_DIR}/youlai-boot-master/semQL.json')
filtered_res=filter_symbols_semantically(f'{OUTPUT_DIR}/youlai-boot-master/filtered_by_type.json',f'{OUTPUT_DIR}/youlai-boot-master/semQL.json')