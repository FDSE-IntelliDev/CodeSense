"""
Surface condition schema for SemCon.

surface condition 用于描述所有基于字面文本的搜索条件，包括：
- 代码元素名搜索
- 代码行搜索
- 代码片段搜索
- 普通关键词 / 同义词 / ngram / 倒排索引搜索
"""

from parsers.code_element_types import get_common_code_element_types

surface_condition = {
    "type": "surface",
    "property": "<include|exclude; code elements satisfying the following surface conditions will be included in or excluded from final results>",
    "keywords": [
        "<literal keyword/code text to match; can be code element name, code line, code snippet, or normal keyword>"
    ],
    "synonyms": [
        "<optional surface variants or synonyms; use empty list if not needed>"
    ],
    "match_kind": "<code_element|code_snippet|code_line|unknown>",
    "code_element_type": f"<when kind is code_element, choose from : {get_common_code_element_types()}"
}