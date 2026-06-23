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
    # "code_element_type": f"<target code element type; choose from common types: {get_common_code_element_types()}>",
    "file_path": "<file path / directory / package path constraint, or None>",
    # "container": "<class/module/package/container constraint, or None>", todo 后续优化一下container relation的设计
    "graph_constraint": {
        "role": "<entrypoint | leaf | isolated | None,entrypoint means in_degree = 0 and out_degree > 0; leaf means in_degree > 0 and out_degree = 0; isolated means in_degree = 0 and out_degree = 0. Use None if no graph-role constraint is needed.>"
    },
    "caller": "<expected caller code element and call-chain depth, or None; format: 'file_name:func_name:hop_count'. If any component is unspecified, put None in that position, e.g. 'AuthController.java:login:None', 'None:login:None', or 'None:login:2'. Use None if not applicable>",
    "callee": "<expected callee code element and call-chain depth, or None; format: 'file_name:func_name:hop_count'. If any component is unspecified, put None in that position, e.g. 'AuthService.java:login:None', 'None:login:None', or 'None:login:2'. Use None if not applicable>",
    "code_ql": "<optional executable CodeQL query. Use this for relation constraints not covered by the explicit fields above, such as annotations/decorators/attributes, signature details, modifiers, entry-point detection, inheritance, framework-specific handlers, or other language-specific structures. Use None if not needed>",
    "description": "<brief natural language explanation of this relation condition>"
}
