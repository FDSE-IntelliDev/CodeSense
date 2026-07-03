import json
from search.full_term_matcher import FullTermMatcher
from definition import PROJECT_OUTPUT_DIR, QUERY_OUTPUT_DIR


def _has_requested_conditions(semql: dict, properties: tuple) -> bool:
    """Return whether grouped SemQL contains any requested property entries."""
    conditions = semql.get("conditions")
    if not isinstance(conditions, dict):
        return True

    for condition_group in conditions.values():
        if not isinstance(condition_group, dict):
            continue
        for property_name in properties:
            property_conditions = condition_group.get(property_name)
            if isinstance(property_conditions, (list, dict)) and property_conditions:
                return True
    return False


def invert_index_search4symbol(invert_index_path: str, ngramed_symbol_path: str, query_dsl_result_path: str, properties: tuple = ("include",)) -> list:
    """
    根据倒排索引和拆词符号进行检索，返回匹配的完整代码元素。

    properties: which property groups to match ("include",) or ("exclude",).
    """
    # 1. 从文件读取查询条件 (semQL)
    with open(query_dsl_result_path, 'r', encoding='utf-8') as f:
        semQL = json.load(f)

    # 请求的 include/exclude 没有任何条件时，无需加载索引和执行匹配。
    if not _has_requested_conditions(semQL, properties):
        return []

    # 2. 初始化匹配器
    matcher = FullTermMatcher(
        invert_index_path=invert_index_path,
        ngramed_symbol_path=ngramed_symbol_path,
    )

    # 3. 执行匹配
    result = matcher.match_ngram(semQL, properties)
    matched_subtokens = result.get('matched_subtokens', {})

    # 4. 读取 ngramed_symbols 以便查找完整信息
    with open(ngramed_symbol_path, 'r', encoding='utf-8') as f:
        ngramed_symbols = json.load(f)

    # 5. 提取所有匹配到的符号标识（支持列表或字典格式）
    matched_names = set()
    if isinstance(matched_subtokens, dict):
        for names in matched_subtokens.values():
            matched_names.update(names)
    elif isinstance(matched_subtokens, list):
        matched_names.update(matched_subtokens)

    # 6. 从 ngramed_symbols 中过滤出完整的代码元素信息
    # 假设你的代码元素有唯一个标识符比如 'id'
    full_matched_elements = []
    seen_ids = set()

    for ngramed_token, symbol in ngramed_symbols.items():
        if ngramed_token.lower().strip() in matched_names:
            symbol_list = symbol if isinstance(symbol, list) else [symbol]

            for item in symbol_list:
                item_id = item.get('symbol_id')
                if item_id not in seen_ids:
                    item['matched_subtokens'] = ngramed_token
                    seen_ids.add(item_id)
                    full_matched_elements.append(item)

    return full_matched_elements


if __name__ == "__main__":
    # 使用常量和定义好的路径调用函数
    invert_index_path = f"{PROJECT_OUTPUT_DIR}/invert_index.json"
    ngramed_symbol_path = f"{PROJECT_OUTPUT_DIR}/ngramed_symbol.json"
    query_dsl_result_path = f"{QUERY_OUTPUT_DIR}/semQL.json"

    # 执行搜索并返回结果
    elements = invert_index_search4symbol(
        invert_index_path=invert_index_path, 
        ngramed_symbol_path=ngramed_symbol_path, 
        query_dsl_result_path=query_dsl_result_path
    )

    print("\nFull Matched Elements:")
    print(json.dumps(elements, ensure_ascii=False, indent=2))
