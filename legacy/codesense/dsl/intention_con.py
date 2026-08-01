"""
Intention condition schema for SemCon.

intention condition 用于描述目标代码元素需要满足的语义功能、业务职责或领域含义。
它主要服务于 cluster_filter、embedding_filter、后续 LLM-as-a-Judge 语义重排等阶段。
"""

intention_condition = {
    "type": "intention",
    "property": "<include|exclude; code elements satisfying the following intention conditions will be included in or excluded from final results>",
    "intent": {
        "action": "<operation or behavior; may include synonyms or behavior variants if useful>",
        "object": "<entity/resource/domain object; may include synonyms or related entities if useful>"
    },
    "intent_statement": "<a declarative statement describing the required intent, answerable as Yes/No>",
    "aspect": "functional | non_functional | domain",
    "non_functional_type": "performance | security | reliability | maintainability | null",
    "keywords": [
        "<intention keyword or phrase describing required behavior/domain meaning>"
    ],
    "description": "<brief natural language explanation of this intention condition>"
}