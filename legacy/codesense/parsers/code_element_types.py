"""
Language-specific code element type registry.

用途：
- 统一记录不同语言中可能出现的代码元素类型
- 供 DSL exact_code.element_name 字段、parser、search/filter 阶段复用
- common 表示不同语言之间较稳定的通用交集；具体语言可以有更细类型
"""

from typing import Dict, List, Set


# 跨语言通用抽象类型，尽量只保留大多数语言都能对应的元素类型。
# 注意：file 表示文件名或文件路径，不一定是语言 AST 节点。
COMMON_CODE_ELEMENT_TYPES: List[str] = [
    "function",
    "class",
    "variable",
    "file_path"
]


# 各语言更细粒度的代码元素类型。
LANGUAGE_CODE_ELEMENT_TYPES: Dict[str, List[str]] = {
    "java": [
        "package",
        "class",
        "interface",
        "enum",
        "annotation",
        "method",
        "constructor",
        "field",
        "local_variable",
        "parameter",
        "constant",
        "generic_type_parameter",
        "file",
    ],
    "python": [
        "module",
        "package",
        "class",
        "function",
        "method",
        "decorator",
        "property",
        "variable",
        "local_variable",
        "parameter",
        "constant",
        "import_alias",
        "file",
    ],
    "javascript": [
        "module",
        "class",
        "function",
        "arrow_function",
        "method",
        "constructor",
        "property",
        "variable",
        "parameter",
        "constant",
        "export",
        "import_alias",
        "file",
    ],
    "typescript": [
        "module",
        "namespace",
        "class",
        "interface",
        "enum",
        "type_alias",
        "function",
        "arrow_function",
        "method",
        "constructor",
        "property",
        "variable",
        "parameter",
        "constant",
        "decorator",
        "generic_type_parameter",
        "export",
        "import_alias",
        "file",
    ],
    "go": [
        "package",
        "function",
        "method",
        "struct",
        "interface",
        "field",
        "variable",
        "local_variable",
        "parameter",
        "constant",
        "type_alias",
        "file",
    ],
    "c": [
        "function",
        "struct",
        "union",
        "enum",
        "field",
        "variable",
        "local_variable",
        "parameter",
        "constant",
        "macro",
        "typedef",
        "file",
    ],
    "cpp": [
        "namespace",
        "class",
        "struct",
        "union",
        "enum",
        "function",
        "method",
        "constructor",
        "destructor",
        "field",
        "variable",
        "local_variable",
        "parameter",
        "constant",
        "macro",
        "typedef",
        "template_parameter",
        "file",
    ],
    "rust": [
        "module",
        "function",
        "method",
        "struct",
        "enum",
        "trait",
        "impl",
        "field",
        "variable",
        "local_variable",
        "parameter",
        "constant",
        "macro",
        "type_alias",
        "lifetime_parameter",
        "generic_type_parameter",
        "file",
    ],
}


def get_common_code_element_types() -> List[str]:
    return list(COMMON_CODE_ELEMENT_TYPES)


def get_language_code_element_types(language: str) -> List[str]:
    if not language:
        return get_common_code_element_types()
    return LANGUAGE_CODE_ELEMENT_TYPES.get(language.lower(), get_common_code_element_types())


def get_all_code_element_types() -> List[str]:
    element_types: Set[str] = set(COMMON_CODE_ELEMENT_TYPES)
    for items in LANGUAGE_CODE_ELEMENT_TYPES.values():
        element_types.update(items)
    return sorted(element_types)


def is_valid_code_element_type(element_type: str, language: str = "") -> bool:
    if not element_type:
        return False
    return element_type.lower() in set(get_language_code_element_types(language))
