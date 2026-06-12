"""
Relation condition schema for SemCon.

relation condition 用于描述目标代码元素需要满足的结构约束。

设计原则：
- 只把能够稳定通过现有静态索引/规则处理的结构条件放成显式字段
- 更复杂或语言/框架相关的结构条件交给 code_ql 字段表达
- graph_constraint 用于表达调用图上的关系约束（距离、调用者、被调用者）
- property 决定满足这些结构条件的代码元素会被 include 还是 exclude
"""

from parsers.code_element_types import get_common_code_element_types


relation_condition = {
    "type": "relation",
    "property": "<include|exclude; code elements satisfying the following relation conditions will be included in or excluded from final results>",
    "code_element_type": f"<target code element type; choose from common types: {get_common_code_element_types()}>",
    "file_path": "<file path / directory / package path constraint, or None>",
    "container": "<class/module/package/container constraint, or None>",
    "graph_constraint": {
        "anchor": "<anchor symbol, 'main_entry', or 'api_route'>",
        "relation": "caller_of | callee_of | distance_leq",
        "value": "<integer hop count or symbol name>"
    },
    "caller": "<expected caller code element, or None>",
    "callee": "<expected callee code element, or None>",
    "code_ql": "<optional executable CodeQL query. Use this for relation constraints not covered by the explicit fields above, such as annotations/decorators/attributes, signature details, modifiers, entry-point detection, inheritance, framework-specific handlers, or other language-specific structures. Use None if not needed>",
    "description": "<brief natural language explanation of this relation condition>"
}