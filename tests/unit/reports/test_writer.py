from __future__ import annotations

import json
from pathlib import Path

import pytest

from codeagent.errors import ErrorRecord
from codeagent.reports import ArtifactKind, ArtifactRecord, ArtifactStore
from codeagent.reports.schemas import HumanDecision, StageResult
from codeagent.reports.writer import ReportReferenceError, ReportWriter


def _artifact_store(run_dir: Path) -> ArtifactStore:
    store = ArtifactStore.create(run_dir, run_id="run-report")
    store.write()
    return store


def _register_log(store: ArtifactStore, run_dir: Path) -> None:
    log_path = run_dir / "testing" / "pytest.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("pytest output", encoding="utf-8")
    store.record(
        ArtifactRecord(
            artifact_id="testing_log",
            stage="testing",
            kind=ArtifactKind.LOG,
            path=log_path,
            summary="pytest log",
        )
    )
    store.write()


def test_write_stage_report_writes_json_markdown_and_registers_artifacts(tmp_path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    store = _artifact_store(run_dir)
    _register_log(store, run_dir)
    writer = ReportWriter(run_dir=run_dir, artifact_store=store)

    result = StageResult(
        stage="testing",
        status="succeeded",
        started_at="2026-06-03T05:00:00Z",
        ended_at="2026-06-03T05:01:00Z",
        summary="Regression tests passed.",
        artifact_ids=["testing_log"],
    )

    written = writer.write_stage_report(result)

    assert written.stage_result_path == run_dir / "testing" / "stage_result.json"
    assert written.stage_report_path == run_dir / "testing" / "stage_report.md"
    payload = json.loads(written.stage_result_path.read_text(encoding="utf-8"))
    markdown = written.stage_report_path.read_text(encoding="utf-8")
    reloaded = ArtifactStore.load(run_dir)

    assert payload["report_path"] == "testing/stage_report.md"
    assert payload["artifact_ids"] == ["testing_log"]
    assert "Regression tests passed." in markdown
    assert "testing_log" in markdown
    assert reloaded.find("testing_stage_result") is not None
    assert reloaded.find("testing_stage_report") is not None


def test_write_stage_report_rejects_unregistered_artifact_reference(tmp_path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    writer = ReportWriter(run_dir=run_dir, artifact_store=_artifact_store(run_dir))
    result = StageResult(
        stage="testing",
        status="succeeded",
        started_at="2026-06-03T05:00:00Z",
        summary="Cannot cite a missing artifact.",
        artifact_ids=["missing_log"],
    )

    with pytest.raises(ReportReferenceError, match="missing_log"):
        writer.write_stage_report(result)


def test_failed_stage_report_requires_and_renders_reason_and_next_suggestion(tmp_path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    store = _artifact_store(run_dir)
    _register_log(store, run_dir)
    writer = ReportWriter(run_dir=run_dir, artifact_store=store)
    result = StageResult(
        stage="testing",
        status="failed",
        started_at="2026-06-03T05:00:00Z",
        ended_at="2026-06-03T05:01:00Z",
        summary="Regression command failed.",
        artifact_ids=["testing_log"],
        error=ErrorRecord(
            error_id="err-pytest",
            stage="testing",
            node="run_tests",
            category="pytest_failure",
            message="pytest returned exit code 1",
            artifact_ids=["testing_log"],
        ),
        next_suggestion="Enter debugging stage with the saved pytest log.",
    )

    written = writer.write_stage_report(result)
    markdown = written.stage_report_path.read_text(encoding="utf-8")

    assert "pytest_failure" in markdown
    assert "pytest returned exit code 1" in markdown
    assert "Enter debugging stage" in markdown


def test_failed_stage_report_rejects_missing_next_suggestion(tmp_path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    store = _artifact_store(run_dir)
    _register_log(store, run_dir)
    writer = ReportWriter(run_dir=run_dir, artifact_store=store)
    result = StageResult(
        stage="testing",
        status="failed",
        started_at="2026-06-03T05:00:00Z",
        summary="Regression command failed.",
        artifact_ids=["testing_log"],
        error=ErrorRecord(
            error_id="err-pytest",
            stage="testing",
            node="run_tests",
            category="pytest_failure",
            message="pytest returned exit code 1",
        ),
    )

    with pytest.raises(ReportReferenceError, match="next suggestion"):
        writer.write_stage_report(result)


def test_decision_trace_appends_human_and_route_decisions(tmp_path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    writer = ReportWriter(run_dir=run_dir, artifact_store=_artifact_store(run_dir))

    writer.record_human_decision(
        HumanDecision(
            interrupt_id="interrupt-1",
            action="approve_command",
            decision_type="edit",
            edited_payload={"command": "python -m pytest tests/unit/reports -q"},
            comment="Narrowed command.",
            timestamp="2026-06-03T05:00:00Z",
        )
    )
    writer.record_route_decision(
        from_stage="testing",
        to_stage="debugging",
        reason="tests failed",
    )

    events = [
        json.loads(line)
        for line in (run_dir / "decision_trace.jsonl").read_text(encoding="utf-8").splitlines()
    ]

    assert events[0]["type"] == "human_decision"
    assert events[0]["edited_payload"]["command"].endswith("tests/unit/reports -q")
    assert events[1]["type"] == "route_decision"
    assert events[1]["to_stage"] == "debugging"


def test_final_report_uses_stage_results_and_registered_artifact_index(tmp_path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    store = _artifact_store(run_dir)
    _register_log(store, run_dir)
    writer = ReportWriter(run_dir=run_dir, artifact_store=store)
    stage_result = StageResult(
        stage="testing",
        status="succeeded",
        started_at="2026-06-03T05:00:00Z",
        ended_at="2026-06-03T05:01:00Z",
        summary="Regression tests passed.",
        artifact_ids=["testing_log"],
    )

    writer.write_stage_report(stage_result)
    final_path = writer.write_final_report([stage_result])
    markdown = final_path.read_text(encoding="utf-8")
    reloaded = ArtifactStore.load(run_dir)

    assert "# 智能体运行总结报告" in markdown
    assert "| testing | succeeded | testing_log | Regression tests passed. |" in markdown
    assert "testing/pytest.log" in markdown
    assert reloaded.find("final_report") is not None


def test_final_report_rejects_unregistered_stage_artifact(tmp_path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    writer = ReportWriter(run_dir=run_dir, artifact_store=_artifact_store(run_dir))
    result = StageResult(
        stage="testing",
        status="succeeded",
        started_at="2026-06-03T05:00:00Z",
        summary="This should not be accepted.",
        artifact_ids=["missing_artifact"],
    )

    with pytest.raises(ReportReferenceError, match="missing_artifact"):
        writer.write_final_report([result])


def test_final_report_rejects_failed_stage_without_next_suggestion(tmp_path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    store = _artifact_store(run_dir)
    _register_log(store, run_dir)
    writer = ReportWriter(run_dir=run_dir, artifact_store=store)
    result = StageResult(
        stage="testing",
        status="failed",
        started_at="2026-06-03T05:00:00Z",
        summary="Regression command failed.",
        artifact_ids=["testing_log"],
        error=ErrorRecord(
            error_id="err-pytest",
            stage="testing",
            node="run_tests",
            category="pytest_failure",
            message="pytest returned exit code 1",
        ),
    )

    with pytest.raises(ReportReferenceError, match="next suggestion"):
        writer.write_final_report([result])


def test_final_report_renders_failed_stage_reason_and_next_suggestion(tmp_path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    store = _artifact_store(run_dir)
    _register_log(store, run_dir)
    writer = ReportWriter(run_dir=run_dir, artifact_store=store)
    result = StageResult(
        stage="testing",
        status="failed",
        started_at="2026-06-03T05:00:00Z",
        summary="Regression command failed.",
        artifact_ids=["testing_log"],
        error=ErrorRecord(
            error_id="err-pytest",
            stage="testing",
            node="run_tests",
            category="pytest_failure",
            message="pytest returned exit code 1",
            artifact_ids=["testing_log"],
        ),
        next_suggestion="Enter debugging stage with the saved pytest log.",
    )

    final_path = writer.write_final_report([result])
    markdown = final_path.read_text(encoding="utf-8")

    assert "## 失败与取消详情" in markdown
    assert "err-pytest" in markdown
    assert "pytest_failure" in markdown
    assert "pytest returned exit code 1" in markdown
    assert "Enter debugging stage" in markdown


def test_reports_escape_markdown_table_cells(tmp_path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    store = _artifact_store(run_dir)
    log_path = run_dir / "testing" / "pytest.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("pytest output", encoding="utf-8")
    store.record(
        ArtifactRecord(
            artifact_id="testing_log",
            stage="testing",
            kind=ArtifactKind.LOG,
            path=log_path,
            summary="pytest | log\nsummary",
        )
    )
    store.write()
    writer = ReportWriter(run_dir=run_dir, artifact_store=store)
    result = StageResult(
        stage="testing",
        status="failed",
        started_at="2026-06-03T05:00:00Z",
        summary="Regression | command\nfailed.",
        artifact_ids=["testing_log"],
        error=ErrorRecord(
            error_id="err-pytest",
            stage="testing",
            node="run_tests",
            category="pytest_failure",
            message="pytest | returned\nexit code 1",
            artifact_ids=["testing_log"],
        ),
        next_suggestion="Enter debugging | stage\nwith the saved pytest log.",
    )

    stage_paths = writer.write_stage_report(result)
    final_path = writer.write_final_report([result])
    stage_markdown = stage_paths.stage_report_path.read_text(encoding="utf-8")
    final_markdown = final_path.read_text(encoding="utf-8")

    assert "pytest \\| log summary" in stage_markdown
    assert "Regression \\| command failed." in final_markdown
    assert "pytest \\| returned exit code 1" in final_markdown
    assert "Enter debugging \\| stage with the saved pytest log." in final_markdown
