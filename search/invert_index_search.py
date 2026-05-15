from search.full_term_matcher import  FullTermMatcher
from definition import OUTPUT_DIR
import json

matcher = FullTermMatcher(
        invert_index_path=f"{OUTPUT_DIR}/youlai-boot-master/invert_index.json",
        ngramed_symbol_path=f"{OUTPUT_DIR}/youlai-boot-master/ngramed_symbol.json",
    )

with open(f"{OUTPUT_DIR}/youlai-boot-master/query_dsl_result.json", 'r') as f:
    semQL=json.load(f)
    