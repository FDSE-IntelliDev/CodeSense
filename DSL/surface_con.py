"""
Surface condition schema for SemCon.

surface condition 用于描述所有基于字面文本的搜索条件，包括：
- 代码元素名搜索
- 代码行搜索
- 代码片段搜索
- 普通关键词 / 同义词 / ngram / 倒排索引搜索

字段说明：
- keywords: 搜索关键词或代码文本的简短表示，始终用于 keyword / ngram / 倒排索引等通用词法检索路径
- code_text: 当 match_kind 为 code_line 或 code_snippet 时，应填入待搜索的完整代码文本（如一行代码、一个代码片段）；当 match_kind 为 code_element 时，可填入完整的限定路径或代码元素名；当 match_kind 为 unknown 时填 null 或空字符串即可。code_text 仅用于 exact / 精准搜索路径，与 keywords 互补
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
    "code_element_type": f"<when kind is code_element, choose from : {get_common_code_element_types()}",
    "code_text": "<complete code text to match for exact/precise search; e.g. a full code line, code snippet, file path, or code element name. Use null or empty string when match_kind is unknown>"
}