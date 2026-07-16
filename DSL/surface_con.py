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
- group_logic: include groups 之间的图感知 AND 配置；这里默认使用 and_hop，不再显式配置 op
  - groups: 参与 and_hop 的 include group_id 列表
  - graph_scope: and_hop 使用的代码关系范围，只能选择 call 或 import；同文件关系在 import/file scope 下视为 hop_count=0
  - default_hop_count: 未被 pairwise_hop_counts 覆盖的 include group pair 使用的默认 hop count
  - pairwise_hop_counts: 针对具体 group pair 的 hop count 覆盖，用于表达不同概念之间的关系强弱
  - reason: 解释为什么这些 include groups 需要通过 and_hop 合并
- match_kind: 表示是否需要做代码元素、代码行、代码片段或未知类型的精准匹配
- code_element_type: 当 match_kind 为 code_element 时，约束目标代码元素类型
- code_text: 当 match_kind 为 code_line 或 code_snippet 时，应填入待搜索的完整代码文本；当 match_kind 为 code_element 时，可填入完整的限定路径或代码元素名；当 match_kind 为 unknown 时填 null 或空字符串即可
"""

from parsers.code_element_types import get_common_code_element_types

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
                "<context-expanded related terms for this group, e.g. swap; keywords and synonyms inside one group are OR-ed>"
            ],
            "reason": "<brief reason why these terms belong to the same query concept>"
        }
    ],
    "group_logic": [
        {
            "groups": [
                "<include keyword_group ids participating in graph-aware AND, e.g. k1>",
                "<include keyword_group ids participating in graph-aware AND, e.g. k2>"
            ],
            "graph_scope": [
                "<call|import; call means call-chain relation, import means file/import relation and same-file is treated as hop_count=0>"
            ],
            "default_hop_count": "<non-negative integer; default hop count for include group pairs without a pairwise override>",
            "pairwise_hop_counts": [
                {
                    "groups": [
                        "<first include group id>",
                        "<second include group id>"
                    ],
                    "hop_count": "<non-negative integer for this include group pair>",
                    "reason": "<why this pair should use this hop count based on query context>"
                }
            ],
            "reason": "<why these include groups should be merged with graph-aware AND>"
        }
    ],
    "match_kind": "<code_element|code_snippet|code_line|unknown>",
    "code_element_type": f"<when kind is code_element, choose from : {get_common_code_element_types()}",
    "code_text": "<complete code text to match for exact/precise search; e.g. a full code line, code snippet, file path, or code element name. Use null or empty string when match_kind is unknown>"
}
