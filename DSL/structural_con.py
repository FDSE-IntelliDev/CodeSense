"""
Structural condition schema for SemCon.

structural condition 用于描述目标代码元素需要满足的结构约束，
例如目标是函数/类/变量，或者可以通过 CodeQL 表达的结构条件。
"""

from parsers.code_element_types import get_common_code_element_types


structural_condition = {
    "type": "structural",
    "property": "<include|exclude>",
    "code_element_type": f"<target code element type; choose from common types: {get_common_code_element_types()}>",
    "code_ql": "<optional CodeQL query if a suitable structural query can be generated; otherwise empty string>",
    "description": "<brief natural language explanation of this structural condition>"
}
