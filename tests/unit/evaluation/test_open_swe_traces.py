import json
from pathlib import Path

import pytest

from evaluation.trace_adapters.open_swe_traces import iter_jsonl, normalize_record


def _record() -> dict[str, object]:
    return {
        "repo": "acme/project",
        "language": "java",
        "instance_id": "project-1",
        "trajectory_id": "trace-1",
        "base_commit": "abc123",
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


def test_iter_jsonl_normalizes_each_nonempty_record(tmp_path: Path) -> None:
    path = tmp_path / "traces.jsonl"
    path.write_text("\n" + json.dumps(_record()) + "\n", encoding="utf-8")

    cases = list(iter_jsonl(path))

    assert len(cases) == 1
    assert cases[0].trajectory_id == "trace-1"


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
