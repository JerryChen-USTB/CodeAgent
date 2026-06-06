from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from codeagent.config.schema import TaskConfig
from codeagent.reports.artifact_store import ArtifactKind, ArtifactRecord, ArtifactStore
from codeagent.reports.jsonl_utils import (
    recover_workflow_events_from_log,
    validate_jsonl,
)
from codeagent.reports.transcript import JsonlRecorder
from codeagent.reports.workflow_trace import WorkflowTraceRecorder
from codeagent.runtime.run_context import create_run_context


def test_artifact_store_record_find_and_write(tmp_path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    store = ArtifactStore.create(run_dir, run_id="demo-run")
    artifact_path = run_dir / "implementation" / "implementation_report.md"
    artifact_path.parent.mkdir()
    artifact_path.write_text("report", encoding="utf-8")

    record = store.record(
        ArtifactRecord(
            artifact_id="implementation_report",
            stage="implementation",
            kind=ArtifactKind.REPORT,
            path=artifact_path,
            summary="Implementation summary",
        )
    )
    store.write()
    reloaded = ArtifactStore.load(run_dir)

    assert record.path == "implementation/implementation_report.md"
    assert reloaded.find("implementation_report") == record
    assert reloaded.find_by_stage("implementation") == [record]
    assert json.loads((run_dir / "artifacts_index.json").read_text(encoding="utf-8"))[
        "artifacts"
    ][0]["artifact_id"] == "implementation_report"


def test_artifact_store_rejects_absolute_path_outside_run_dir(tmp_path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    outside = tmp_path / "outside.log"
    outside.write_text("secret-ish", encoding="utf-8")
    store = ArtifactStore.create(run_dir, run_id="demo-run")

    with pytest.raises(ValueError, match="inside run directory"):
        store.record(
            ArtifactRecord(
                artifact_id="outside",
                stage="testing",
                kind=ArtifactKind.LOG,
                path=outside,
                summary="outside",
            )
        )


def test_artifact_store_rejects_relative_traversal(tmp_path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    store = ArtifactStore.create(run_dir, run_id="demo-run")

    with pytest.raises(ValueError, match="must not traverse"):
        store.record(
            ArtifactRecord(
                artifact_id="traversal",
                stage="testing",
                kind=ArtifactKind.LOG,
                path="../outside.log",
                summary="bad",
            )
        )


def test_artifact_store_accepts_run_dir_absolute_path(tmp_path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    artifact = run_dir / "testing" / "test_report.json"
    artifact.parent.mkdir()
    artifact.write_text("{}", encoding="utf-8")
    store = ArtifactStore.create(run_dir, run_id="demo-run")

    record = store.record(
        ArtifactRecord(
            artifact_id="test_report",
            stage="testing",
            kind=ArtifactKind.JSON,
            path=artifact,
            summary="test report",
        )
    )

    assert record.path == "testing/test_report.json"


def test_jsonl_recorder_appends_timestamped_events(tmp_path) -> None:
    path = tmp_path / "transcript.jsonl"
    recorder = JsonlRecorder(path)

    recorder.append({"type": "tool_call", "stage": "testing", "summary": "pytest"})
    recorder.append({"type": "tool_result", "stage": "testing", "summary": "passed"})

    lines = path.read_text(encoding="utf-8").splitlines()
    events = [json.loads(line) for line in lines]
    assert [event["type"] for event in events] == ["tool_call", "tool_result"]
    assert all("timestamp" in event for event in events)


def test_workflow_trace_recorder_concurrent_records_valid_jsonl(tmp_path) -> None:
    run_dir = tmp_path / "run"
    recorder = WorkflowTraceRecorder(run_dir)

    def record_event(index: int) -> None:
        recorder.record(
            "concurrent_event",
            stage="testing",
            index=index,
            payload="x" * 2_000,
        )

    with ThreadPoolExecutor(max_workers=16) as executor:
        list(executor.map(record_event, range(300)))

    lines = (run_dir / "workflow_events.jsonl").read_text(encoding="utf-8").splitlines()
    events = [json.loads(line) for line in lines]
    assert validate_jsonl(run_dir / "workflow_events.jsonl") == []
    assert len(events) == 300
    assert {event["index"] for event in events} == set(range(300))


def test_jsonl_recorder_concurrent_appends_valid_jsonl(tmp_path) -> None:
    path = tmp_path / "decision_trace.jsonl"

    def append_event(index: int) -> None:
        JsonlRecorder(path).append(
            {
                "type": "decision",
                "index": index,
                "comment": "y" * 2_000,
            }
        )

    with ThreadPoolExecutor(max_workers=16) as executor:
        list(executor.map(append_event, range(300)))

    lines = path.read_text(encoding="utf-8").splitlines()
    events = [json.loads(line) for line in lines]
    assert validate_jsonl(path) == []
    assert len(events) == 300
    assert {event["index"] for event in events} == set(range(300))


def test_validate_jsonl_reports_bad_lines(tmp_path) -> None:
    path = tmp_path / "workflow_events.jsonl"
    path.write_text('{"ok": true}\nnot-json\n{"ok": false}\n', encoding="utf-8")

    issues = validate_jsonl(path)

    assert len(issues) == 1
    assert issues[0].line_number == 2
    assert issues[0].error_type == "JSONDecodeError"
    assert issues[0].preview == "not-json"


def test_recover_workflow_events_from_log_writes_sidecar_jsonl(tmp_path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    original_events = run_dir / "workflow_events.jsonl"
    original_events.write_text("broken\n", encoding="utf-8")
    workflow_log = run_dir / "workflow.log"
    workflow_log.write_text(
        """# CodeAgent Workflow Trace

## event one

```json
{
  "event_type": "workflow_event",
  "message": "started"
}
```

```json
not-json
```

## event two

```json
{
  "event_type": "tool_started",
  "tool_name": "run_shell"
}
```
""",
        encoding="utf-8",
    )

    repaired = recover_workflow_events_from_log(workflow_log)

    assert repaired == run_dir / "workflow_events.jsonl.repaired"
    assert original_events.read_text(encoding="utf-8") == "broken\n"
    events = [
        json.loads(line)
        for line in repaired.read_text(encoding="utf-8").splitlines()
    ]
    assert [event["event_type"] for event in events] == [
        "workflow_event",
        "tool_started",
    ]


def test_run_context_exposes_artifact_store_and_recorders(tmp_path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    config = TaskConfig.model_validate({"stages": ["implement"], "project_path": project})

    context = create_run_context(config, output_root=tmp_path / "runs")
    context.transcript.append({"type": "user_input", "summary": "demo"})
    context.decision_trace.append({"type": "route_decision", "reason": "start"})

    assert (context.run_dir / "transcript.jsonl").read_text(encoding="utf-8")
    assert (context.run_dir / "decision_trace.jsonl").read_text(encoding="utf-8")
