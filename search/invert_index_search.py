from search.full_term_matcher import FullTermMatcher
from definition import OUTPUT_DIR
import json

matcher = FullTermMatcher(
    invert_index_path=f"{OUTPUT_DIR}/youlai-boot-master/invert_index.json",
    ngramed_symbol_path=f"{OUTPUT_DIR}/youlai-boot-master/ngramed_symbol.json",
)

with open(f"{OUTPUT_DIR}/youlai-boot-master/query_dsl_result.json", 'r') as f:
    semQL = json.load(f)

result = matcher.match_ngram(semQL)
print("Match Result:")
print(json.dumps(result, ensure_ascii=False, indent=2))

matched_subtokens = result.get('matched_subtokens', {})

with open(f"{OUTPUT_DIR}/youlai-boot-master/ngramed_symbol.json", 'r') as f:
    ngramed_symbols = json.load(f)

# 1. 提取所有匹配到的符号标识（支持 matched_subtokens 是列表或字典格式）
matched_names = set()
if isinstance(matched_subtokens, dict):
    for names in matched_subtokens.values():
        matched_names.update(names)
elif isinstance(matched_subtokens, list):
    matched_names.update(matched_subtokens)

# 2. 从 ngramed_symbols 中过滤出完整的代码元素信息
full_matched_elements = []
for symbol in ngramed_symbols:
    # 假设通过 name 匹配，如果使用的是 symbol_id，请将 'name' 替换为 'symbol_id'
    if symbol.get('name') in matched_names:
        full_matched_elements.append(symbol)

print("\nFull Matched Elements:")
print(json.dumps(full_matched_elements, ensure_ascii=False, indent=2))
