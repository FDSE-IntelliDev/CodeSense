"""
Semantic condition schema for SemCon.

semantic condition 用于描述目标代码元素需要满足的语义功能、业务职责或领域含义。
它主要服务于 cluster_filter、embedding_filter、后续语义重排等阶段。
"""

semantic_condition = {
    "type": "semantic",
    "property": "<include|exclude>",
    "intent": {
        "action": "<operation or behavior; may include synonyms or behavior variants if useful>",
        "object": "<entity/resource/domain object; may include synonyms or related entities if useful>"
    },
    "keywords": [
        "<semantic keyword or phrase describing required behavior/domain meaning>"
    ],
    "semantic_labels": [
        "<optional high-level semantic labels, e.g. user authentication, token refresh>"
    ],
    "description": "<brief natural language explanation of this semantic condition>"
}
