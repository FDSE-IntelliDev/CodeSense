import json

from evaluation.query_mining import mine_queries
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
                "reason": "It describes a state transition failure.",
                "anchor_terms": ["cursor", "page"],
                "semantic_constraints": ["leaves navigation state inconsistent"],
            }
        )


def test_resolved_trace_becomes_episode_query_with_hidden_result_and_patch() -> None:
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
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_call",
                        "name": "view",
                        "arguments": {"path": "src/main/java/Navigation.java"},
                    }
                ],
            },
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

    batch = mine_queries(case, generator)

    assert batch.skip_reasons == ()
    assert len(batch.queries) == 1
    payload = batch.queries[0].to_dict()
    assert payload["query_id"] == "trace-1:2"
    assert payload["answer"] == [{"file": "src/main/java/Navigation.java", "functions": []}]
    assert payload["source_event_indices"] == [1, 2]
    assert payload["result_event_indices"] == [3]
    assert len(generator.prompts) == 1
    assert "src/main/java/Navigation.java" not in generator.prompts[0]
    assert "withoutPage" not in generator.prompts[0]
    assert "diff --git" not in generator.prompts[0]
