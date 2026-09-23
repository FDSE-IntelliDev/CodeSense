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


def test_resolved_trace_becomes_episode_query_with_observed_gold() -> None:
    record = {
        "repo": "owner/repo",
        "language": "java",
        "instance_id": "issue-1",
        "trajectory_id": "trace-1",
        "resolved": 1,
        "trajectory": [
            {"role": "user", "content": "Pool reuse order can change request behavior."},
            {"role": "assistant", "content": "I will inspect the pool ordering policy."},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "search-1",
                        "function": {
                            "name": "execute_bash",
                            "arguments": json.dumps({"command": "rg isPoolLifo src/main/java"}),
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "search-1",
                "content": "src/main/java/Pool.java\nsrc/main/java/PoolConfig.java",
            },
            {
                "role": "assistant",
                "content": "I will read the selection implementation.",
                "tool_calls": [
                    {
                        "id": "read-1",
                        "function": {
                            "name": "str_replace_editor",
                            "arguments": json.dumps(
                                {
                                    "command": "view",
                                    "path": "src/main/java/Pool.java",
                                }
                            ),
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "read-1",
                "content": "class Pool { void select() {} }",
            },
        ],
    }

    case = normalize_record(record)
    batch = mine_queries(case, _Generator())
    payload = batch.queries[0].to_dict()

    assert payload["answer"] == [{"file": "src/main/java/Pool.java", "functions": []}]
    assert payload["candidate_answers"] == [
        {"file": "src/main/java/PoolConfig.java", "functions": []}
    ]
    assert payload["query_id"] == "trace-1:2"
    assert "src/main/java/Pool.java" not in batch.queries[0].provenance["prompt"]
    assert "reference_patch" not in batch.queries[0].provenance["prompt"]
