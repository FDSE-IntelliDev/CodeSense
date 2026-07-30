"""
Surface condition schema for SemCon.

surface condition 用于描述所有基于字面文本的搜索条件，包括：
- 代码元素名搜索
- 代码行搜索
- 代码片段搜索
- 普通关键词 / 同义词 / ngram / 倒排索引搜索

字段说明：
- type: 固定为 surface，用于标识这是表层检索条件
- keyword_groups: 上下文相关的关键词概念组；每个 group 内部的 keywords/synonyms 是 OR 关系
  - group_id: 稳定的组 ID，供 group_logic 和后续 evidence 引用
  - property: include 表示正向召回概念；exclude 表示负向排除概念，NOT 逻辑可由该字段推断
  - keywords: 该概念组的核心关键词，通常来自 query 中的原始概念
  - synonyms: LLM 基于完整 query 上下文扩展出的相关词、近义词、实现词或领域词
  - reason: 解释该组为什么成立，便于调试和后续 evidence 展示
- group_logic: include groups 之间的原子图感知 AND pair rule；每个列表项只描述一对 group
  - groups: 恰好包含两个不同的 include group_id；pair 是无向的
  - graph_scope: 当前 pair 使用的单一代码关系范围，只能是 call 或 import
  - hop_count: 两个 group 的直接命中代码元素之间允许的最大非负图距离；0 表示同一代码元素
  - reason: 解释为什么该 pair 需要在 hop_count 范围内共同满足
  - 未参与任何 pair rule 的 include group 保持普通直接检索结果，并在执行阶段与各 pair result 取交集
- match_kind: 表示是否需要做代码元素、代码行、代码片段或未知类型的精准匹配
- code_element_type: 当 match_kind 为 code_element 时，约束目标代码元素类型
- code_text: 当 match_kind 为 code_line 或 code_snippet 时，应填入待搜索的完整代码文本；当 match_kind 为 code_element 时，可填入完整的限定路径或代码元素名；当 match_kind 为 unknown 时填 null 或空字符串即可
"""

from codesense.parsers.code_element_types import get_common_code_element_types

surface_condition = {
    "type": "surface",
    "keyword_groups": [
        {
            "group_id": "<stable group id, e.g. k1>",
            "property": "<include|exclude; include means positive retrieval, exclude means negative filtering>",
            "keywords": [
                "<core query concept keywords represented by this group>"
            ],
            "synonyms": [
                "<Words with the closest meaning to keywords under the current context>"
            ],
            "reason": "<brief reason why these terms belong to the same query concept>"
        }
    ],
    "group_logic": [
        {
            "groups": [
                "<first include group id>",
                "<second include group id>"
            ],
            "graph_scope": "<call|import, graph_scope=\"call\" uses call-chain distance; graph_scope=\"import\" uses file/import distance.>",
            "hop_count": "<non-negative integer>",
            "reason": "<why this pair should be connected within hop_count>"
        }
    ],
    "match_kind": "<code_element|code_snippet|code_line|unknown>",
    "code_element_type": (
        f"<when kind is code_element, choose from: "
        f"{get_common_code_element_types()}>"
    ),
    "code_text": (
        "<complete code text to match; use null or empty string "
        "when it is not required>"
    )
}
