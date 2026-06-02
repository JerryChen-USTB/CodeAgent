from __future__ import annotations

import json

import pytest

from codeagent.config.schema import TaskConfig
from codeagent.reports.artifact_store import ArtifactKind, ArtifactRecord, ArtifactStore
from codeagent.reports.transcript import JsonlRecorder
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


def test_run_context_exposes_artifact_store_and_recorders(tmp_path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    config = TaskConfig.model_validate({"stages": ["implement"], "project_path": project})

    context = create_run_context(config, output_root=tmp_path / "runs")
    context.transcript.append({"type": "user_input", "summary": "demo"})
    context.decision_trace.append({"type": "route_decision", "reason": "start"})

    assert (context.run_dir / "transcript.jsonl").read_text(encoding="utf-8")
    assert (context.run_dir / "decision_trace.jsonl").read_text(encoding="utf-8")
