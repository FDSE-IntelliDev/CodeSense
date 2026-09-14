import json

from evaluation.query_mining import mine_query
from evaluation.trace_adapters.open_swe_traces import normalize_record


class _Generator:
    model = "fixture-model"

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return json.dumps(
            {
                "status": "valid",
                "query": (
                    "Find the logic that can leave navigation state inconsistent when "
                    "switching between cursor-based and page-based access."
                ),
                "reason": "It describes a state transition failure without code identifiers.",
            }
        )


def test_resolved_trace_becomes_one_semantic_query_with_hidden_patch_gold() -> None:
    record = {
        "repo": "owner/repo",
        "language": "java",
        "instance_id": "issue-1",
        "trajectory_id": "trace-1",
        "resolved": 1,
        "trajectory": [
            {"role": "user", "content": "Switching navigation modes can retain stale state."},
            {"role": "assistant", "content": "I will inspect navigation transitions."},
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_call",
                        "name": "rg",
                        "arguments": {"pattern": "cursor", "path": "src/main/java"},
                    }
                ],
            },
            {"role": "tool", "name": "rg", "content": "src/main/java/Navigation.java"},
        ],
        "metadata": {
            "reference_patch": {
                "patch": """\
diff --git a/src/main/java/Navigation.java b/src/main/java/Navigation.java
--- a/src/main/java/Navigation.java
+++ b/src/main/java/Navigation.java
@@ -10 +10 @@ public PageRequest afterCursor(Cursor cursor) {
-return withCursor(cursor);
+return withCursor(cursor).withoutPage();
"""
            }
        },
    }
    case = normalize_record(record)
    generator = _Generator()

    outcome = mine_query(case, generator)

    assert outcome.skip_reason is None
    assert outcome.query is not None
    payload = outcome.query.to_dict()
    assert payload["query_id"] == "trace-1"
    assert payload["answer"] == [
        {"file": "src/main/java/Navigation.java", "functions": ["afterCursor"]}
    ]
    assert payload["source_event_indices"] == [1, 2, 3]
    assert len(generator.prompts) == 1
    assert "withoutPage" not in generator.prompts[0]
    assert "diff --git" not in generator.prompts[0]
