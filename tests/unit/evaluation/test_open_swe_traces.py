import json
from pathlib import Path

import pytest

from evaluation.models import CodeLocation
from evaluation.trace_adapters.open_swe_traces import iter_jsonl, normalize_record


def _record() -> dict[str, object]:
    return {
        "repo": "acme/project",
        "language": "java",
        "instance_id": "project-1",
        "trajectory_id": "trace-1",
        "resolved": 1,
        "base_commit": "abc123",
        "metadata": {
            "reference_patch": {
                "patch": """diff --git a/src/main/java/example/Navigation.java \
b/src/main/java/example/Navigation.java
--- a/src/main/java/example/Navigation.java
+++ b/src/main/java/example/Navigation.java
@@ -10,3 +10,4 @@ public PageRequest afterCursor(Cursor cursor) {
-    return withCursor(cursor);
+    return withCursor(cursor).withoutPage();
 }
diff --git a/src/main/java/example/Config.java b/src/main/java/example/Config.java
--- a/src/main/java/example/Config.java
+++ b/src/main/java/example/Config.java
@@ -3,2 +3,2 @@ class Config {
-    private int page = 1;
+    private int page = 0;
diff --git a/src/test/java/example/NavigationTest.java b/src/test/java/example/NavigationTest.java
--- a/src/test/java/example/NavigationTest.java
+++ b/src/test/java/example/NavigationTest.java
@@ -5,2 +5,2 @@ void switchesMode() {
-    assertOld();
+    assertNew();
diff --git a/src/main/java/example/NewMode.java b/src/main/java/example/NewMode.java
new file mode 100644
--- /dev/null
+++ b/src/main/java/example/NewMode.java
@@ -0,0 +1 @@
+class NewMode {}
"""
            }
        },
        "trajectory": [
            {"role": "user", "content": "Fix the write buffer backpressure behavior."},
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "I will locate the buffer watermark code."},
                    {
                        "type": "tool_call",
                        "name": "rg",
                        "arguments": {"pattern": "watermark", "path": "src/main/java"},
                    },
                ],
            },
            {
                "role": "tool",
                "name": "rg",
                "content": "src/main/java/Watermark.java:10: class Watermark",
            },
        ],
    }


def test_normalize_record_preserves_identity_and_separates_tool_output() -> None:
    case = normalize_record(_record())

    assert (case.repo, case.instance_id, case.trajectory_id) == (
        "acme/project",
        "project-1",
        "trace-1",
    )
    assert case.issue_statement == "Fix the write buffer backpressure behavior."
    assert case.base_commit == "abc123"
    assert [event.index for event in case.events] == [0, 1, 2]
    assert case.events[1].tool_name == "rg"
    assert '"pattern": "watermark"' in (case.events[1].tool_input or "")
    assert case.events[1].tool_output is None
    assert case.events[2].tool_output == "src/main/java/Watermark.java:10: class Watermark"


def test_normalize_record_extracts_existing_production_java_patch_gold() -> None:
    case = normalize_record(_record())

    assert case.answer == (
        CodeLocation("src/main/java/example/Navigation.java", ("afterCursor",)),
        CodeLocation("src/main/java/example/Config.java", ()),
    )
    assert case.gold_error is None


def test_normalize_record_marks_missing_or_unusable_patch_without_rejecting_trace() -> None:
    missing = _record()
    missing.pop("metadata")
    assert normalize_record(missing).gold_error == "missing_reference_patch"

    tests_only = _record()
    metadata = tests_only["metadata"]
    assert isinstance(metadata, dict)
    reference = metadata["reference_patch"]
    assert isinstance(reference, dict)
    reference["patch"] = """diff --git a/src/test/java/A.java b/src/test/java/A.java
--- a/src/test/java/A.java
+++ b/src/test/java/A.java
@@ -1 +1 @@ void testA() {
-old();
+newer();
"""
    assert normalize_record(tests_only).gold_error == "no_production_java_gold"


def test_normalize_record_preserves_the_complete_original_issue() -> None:
    record = _record()
    issue = (
        "<issue_description>Fix navigation state.\n"
        "New interfaces introduced: Navigation#afterCursor</issue_description>"
    )
    trajectory = record["trajectory"]
    assert isinstance(trajectory, list)
    first = trajectory[0]
    assert isinstance(first, dict)
    first["content"] = issue

    assert normalize_record(record).issue_statement == issue


def test_iter_jsonl_normalizes_each_nonempty_record(tmp_path: Path) -> None:
    path = tmp_path / "traces.jsonl"
    path.write_text("\n" + json.dumps(_record()) + "\n", encoding="utf-8")

    cases = list(iter_jsonl(path))

    assert len(cases) == 1
    assert cases[0].trajectory_id == "trace-1"


def test_iter_jsonl_skips_records_that_are_not_resolved(tmp_path: Path) -> None:
    records = []
    for index, resolved in enumerate((0, -1, None, True, 1)):
        record = _record()
        record["trajectory_id"] = f"trace-{index}"
        if resolved is None:
            record.pop("resolved")
        else:
            record["resolved"] = resolved
        records.append(record)
    path = tmp_path / "traces.jsonl"
    path.write_text("\n".join(json.dumps(record) for record in records), encoding="utf-8")

    cases = list(iter_jsonl(path))

    assert [case.trajectory_id for case in cases] == ["trace-4"]


@pytest.mark.parametrize("resolved", [0, -1, None, True, "1"])
def test_normalize_record_rejects_non_resolved_trace(resolved: object) -> None:
    record = _record()
    if resolved is None:
        record.pop("resolved")
    else:
        record["resolved"] = resolved

    with pytest.raises(ValueError, match="resolved == 1"):
        normalize_record(record)


@pytest.mark.parametrize(
    "changes",
    [
        {"language": "python"},
        {"trajectory": []},
        {"repo": ""},
        {"trajectory_id": ""},
    ],
)
def test_normalize_record_rejects_invalid_identity_or_trajectory(
    changes: dict[str, object],
) -> None:
    record = _record()
    record.update(changes)

    with pytest.raises(ValueError):
        normalize_record(record)
