import json
from parsers.code_element_types import get_common_code_element_types

query_dsl={
  "intent": {
    "action": {
      "term": "<operation or behavior applied to the object>",
      "synonyms": [
        "<semantically similar operations or behaviors>"
      ]
    },
    "object": {
      "term": "<core entity or resource involved in the query>",
      "synonyms": [
        "<semantically related entities>"
      ]
    }
  },
  "has_exact_code": "<0|1, 1 if the query may contain code element content, otherwise 0>",
  "exact_code": [
    {
      "text": "<literal code-like string explicitly mentioned by the query>",
      "kind": "code_element|code_snippet|code_line|unknown",
      "code_element_type": f"<when kind is code_element, choose from : {get_common_code_element_types()}",
      "match_mode": "exact_match|fuzzy_match",
      "source": "<short explanation of where this literal appears in the raw query>"
    }
  ],
  "keywords": [
    {
      "term": "<important keyword or semantic phrase from the query>",
      "synonyms": [
        "<natural language or code-level variations>"
      ]
    }
  ],
  "target": [
    "<expected code element type such as function/class/file/module>"
  ],
  "filters": [
    {
      "concept": "<contextual constraint or domain concept>",
      "relation": "<relationship between the concept and the searching target>"
    }
  ],
  "exclude": [
    "<terms or concepts that should be excluded from retrieval>"
  ],
  "raw_query": "<original natural language query>"
}
