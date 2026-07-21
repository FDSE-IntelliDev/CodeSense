import json
from definition import QUERY_OUTPUT_DIR
from typing import Any
from utils.file_utils import load_res

def filter_symbols_by_type(search_results: list, allowed_types: Any) -> list:
    """Filter in-memory symbol records by planner-normalized element types.

    SurfacePlanner already owns schema parsing and function/method
    normalization. The executor therefore passes ``match.code_element_types``
    directly; this filter deliberately has no legacy SemQL parsing path.
    """
    if not isinstance(search_results, list):
        return []

    allowed_types = {
        str(item).lower().strip()
        for item in (allowed_types or [])
        if str(item).strip()
    }
    allowed_types.discard("any")
    allowed_types.discard("null")
    if not allowed_types:
        return search_results

    filtered_results = []
    for symbol in search_results:
        if not isinstance(symbol, dict):
            continue
        symbol_type = symbol.get("type", "")
        if symbol_type.lower().strip() in allowed_types:
            filtered_results.append(symbol)

    return filtered_results


# def filter_symbols_semantically(search_result_path: str, semQL_path: str) -> list:
#     """
#     基于 LLM 的语义过滤方法，根据 semQL 对代码元素进行深度语义上的筛选。
#     """
#     search_results = load_res(search_result_path)
#     semql_query = load_res(semQL_path)
#
#     # 提取有用的约束信息给 LLM，防止提示词过长
#     constraints = {
#         "intent": extract_semql_terms(
#             semql_query,
#             properties=("include",),
#             condition_type="intention",
#             term_name="intent",
#         ),
#         "filters": extract_semql_terms(
#             semql_query,
#             properties=("include",),
#             condition_type=("surface", "relation"),
#             term_name=("keywords", "synonyms", "description", "code_element_type"),
#         ),
#         "exclude": extract_semql_terms(
#             semql_query,
#             properties=("exclude",),
#             condition_type=("surface", "intention", "relation"),
#             term_name=("keywords", "synonyms", "intent", "description", "code_element_type"),
#         ),
#         "raw_query": extract_semql_terms(semql_query, term_name="raw_query"),
#     }
#
#     filtered_results = []
#     exclude_results = []
#
#     BATCH_SIZE = 10
#
#     for i in range(0, len(search_results), BATCH_SIZE):
#         batch = search_results[i:i + BATCH_SIZE]
#         batch_context = []
#
#         for idx, symbol in enumerate(batch):
#             symbol_file = symbol.get("file", "")
#             code_content = get_symbol_code(PROJECT_PATH, symbol)
#             batch_context.append({
#                 "id": str(idx),
#                 "name": symbol.get("name"),
#                 "type": symbol.get("type"),
#                 "filepath": symbol_file,
#                 "content": code_content
#             })
#
#         prompt = f"""
# You are an expert code analyst.
# Your task is to review a batch of provided code elements, generate a natural language summary (semantic tag) of core functionality for each, and determine if each satisfies the given search constraints.
#
# Analysis Requirements:
# 1. Carefully analyze each code element and summarize its core functionality.
# 2. Evaluate whether the code element matches based on the `intent` (expected behavior and objects), `filters` (expected concepts, roles, or relationships), and `exclude` (functionalities that should be explicitly avoided). If the code implements anything listed in `exclude`, it should NOT be a match.
# 3. Output the result STRICTLY as a JSON array containing an evaluation object for each input element. You must return exactly {len(batch)} objects corresponding to each "id". Do not include any markdown formatting outside the JSON array.
#
# Output Format Example:
# [
#   {{
#     "id": "0",
#     "semantic_summary": "This method is used to...",
#     "matches_intent": true,
#     "fails_exclude": false,
#     "is_match": true,
#     "reason": "Brief explanation..."
#   }},
#   {{
#     "id": "1",
#     ...
#   }}
# ]
#
# [Search Constraints]:
# {json.dumps(constraints, ensure_ascii=False, indent=2)}
#
# [Code Elements Batch]:
# {json.dumps(batch_context, ensure_ascii=False, indent=2)}
# """
#
#         try:
#             messages: Any = [
#                 {"role": "system", "content": "You are a precise code analysis assistant. Output only a valid JSON array."},
#                 {"role": "user", "content": prompt}
#             ]
#             raw_text = call_chat_llm(
#                 model=BASE_MODEL,
#                 messages=messages,
#                 temperature=0.0,
#             )
#
#             # 匹配 JSON 数组
#             m = re.search(r"\[[\s\S]*]", raw_text)
#             if m:
#                 payload = json.loads(m.group(0))
#                 # 将结果转为以 id 为 key 的字典以便快速查找
#                 result_map = {str(item.get("id")): item for item in payload if isinstance(item, dict)}
#
#                 for idx, symbol in enumerate(batch):
#                     res = result_map.get(str(idx), {})
#                     symbol["semantic_summary"] = res.get("semantic_summary", "")
#                     symbol["filter_reason"] = res.get("reason", "")
#
#                     if res.get("is_match") is True:
#                         filtered_results.append(symbol)
#                     else:
#                         exclude_results.append(symbol)
#             else:
#                 # 解析失败，把整批视为 exclude，防止阻断主流程
#                 exclude_results.extend(batch)
#
#         except Exception as e:
#             print(f"Error evaluating batch starting at index {i}: {e}")
#             exclude_results.extend(batch)
#
#     save_res(f'{QUERY_OUTPUT_DIR}/filtered_by_semantics.json', filtered_results)
#     save_res(f'{QUERY_OUTPUT_DIR}/exclude_by_semantics.json', exclude_results)
#
#     return filtered_results


if __name__ == "__main__":
    filtered_res = filter_symbols_by_type(
        load_res(f"{QUERY_OUTPUT_DIR}/invert_index_search_result.json"),
        ["function", "method"],
    )
    print(json.dumps({"filtered": len(filtered_res)}, ensure_ascii=False, indent=2))
    # filtered_res=filter_symbols_semantically(f'{QUERY_OUTPUT_DIR}/filtered_by_type.json', f'{QUERY_OUTPUT_DIR}/semQL.json')
