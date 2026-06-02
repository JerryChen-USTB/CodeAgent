from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from codeagent.cli.app import app
from codeagent.config.schema import TaskConfig
from codeagent.reports import ArtifactKind, ArtifactRecord, ArtifactStore
from codeagent.reports.schemas import StageResult
from codeagent.runtime.run_context import create_run_context
from codeagent.workflow.checkpoint import CheckpointManager
from codeagent.workflow.factory import WorkflowFactory
from codeagent.workflow.state import AgentState, create_initial_state
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from codeagent.cli.resume import inspect_run_for_resume, resume_run_from_checkpoint


def _task_config(project_path: Path) -> TaskConfig:
    return TaskConfig.model_validate(
        {
            "task_id": "resume-demo",
            "stages": ["implement"],
            "project_path": project_path,
        }
    )


def _stage_handler(stage: str) -> Callable[[AgentState], dict[str, Any]]:
    def run(state: AgentState) -> dict[str, Any]:
        stage_results = dict(state.get("stage_results", {}))
        stage_results[stage] = StageResult(
            stage=stage,
            status="succeeded",
            started_at="2026-06-03T07:00:00Z",
            summary=f"{stage} completed",
        ).model_dump(mode="json", exclude_none=True)
        return {"stage_results": stage_results}

    return run


def _minimal_run_dir(
    output_root: Path,
    run_id: str = "run-resume",
    *,
    completed: bool = False,
) -> Path:
    run_dir = output_root / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "task_config.yaml").write_text(
        "stages:\n- implement\nproject_path: .\n",
        encoding="utf-8",
    )
    (run_dir / "final_report.md").write_text(
        "# Final Report\n\nRun completed.\n",
        encoding="utf-8",
    )
    store = ArtifactStore.create(run_dir, run_id=run_id)
    report = run_dir / "implementation" / "stage_result.json"
    report.parent.mkdir()
    report.write_text("{}", encoding="utf-8")
    store.record(
        ArtifactRecord(
            artifact_id="implementation_stage_result",
            stage="implementation",
            kind=ArtifactKind.JSON,
            path=report,
            summary="implementation stage result",
        )
    )
    if completed:
        store.record(
            ArtifactRecord(
                artifact_id="final_report",
                stage="final",
                kind=ArtifactKind.REPORT,
                path=run_dir / "final_report.md",
                summary="final report",
            )
        )
    store.write()
    return run_dir


def _interrupt_graph(saver):
    def ask_for_decision(state: AgentState) -> dict[str, Any]:
        decision = interrupt(
            {
                "interrupt_id": "approve-command-1",
                "action": "approve_command",
            }
        )
        messages = list(state.get("messages", []))
        messages.append(
            {
                "type": "resumed",
                "decision_type": decision["decision_type"],
            }
        )
        return {"messages": messages}

    builder = StateGraph(AgentState)
    builder.add_node("ask_for_decision", ask_for_decision)
    builder.add_edge(START, "ask_for_decision")
    builder.add_edge("ask_for_decision", END)
    return builder.compile(checkpointer=saver)


def test_checkpoint_manager_persists_langgraph_state_with_run_id_thread(tmp_path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    context = create_run_context(_task_config(project), output_root=tmp_path / "runs")
    manager = CheckpointManager(context.run_dir, run_id=context.run_id)

    with manager.create_sqlite_saver() as saver:
        graph = WorkflowFactory(
            stage_handlers={"implementation": _stage_handler("implementation")}
        ).build(checkpointer=saver)
        result = graph.invoke(
            create_initial_state(
                run_id=context.run_id,
                mode="run",
                selected_stages=["implement"],
            ),
            config=manager.get_thread_config(),
        )
        snapshot = graph.get_state(config=manager.get_thread_config())

    assert manager.checkpoint_path.exists()
    assert manager.get_thread_config() == {"configurable": {"thread_id": context.run_id}}
    assert result["final_status"] == "succeeded"
    assert snapshot.values["final_status"] == "succeeded"


def test_pending_interrupt_payload_roundtrip(tmp_path) -> None:
    run_dir = _minimal_run_dir(tmp_path / "runs")
    manager = CheckpointManager(run_dir, run_id="run-resume")

    manager.save_pending_interrupt(
        {
            "interrupt_id": "approve-command-1",
            "action": "approve_command",
            "payload": {"command": "python -m pytest -q"},
        }
    )

    loaded = manager.load_pending_interrupt()
    summary = inspect_run_for_resume(tmp_path / "runs", "run-resume")

    assert loaded is not None
    assert loaded["interrupt_id"] == "approve-command-1"
    assert summary.status == "pending_interrupt"
    assert summary.pending_interrupt == loaded


def test_initialized_run_with_placeholder_final_report_is_not_completed(tmp_path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    context = create_run_context(_task_config(project), output_root=tmp_path / "runs")
    manager = CheckpointManager(context.run_dir, run_id=context.run_id)
    manager.initialize_sqlite()

    summary = inspect_run_for_resume(tmp_path / "runs", context.run_id)

    assert summary.status == "checkpoint_available"
    assert summary.checkpoint_status == "available"


def test_pending_interrupt_can_resume_with_command_resume(tmp_path) -> None:
    run_dir = _minimal_run_dir(tmp_path / "runs")
    manager = CheckpointManager(run_dir, run_id="run-resume")
    with manager.create_sqlite_saver() as saver:
        graph = _interrupt_graph(saver)
        result = graph.invoke(
            create_initial_state(
                run_id="run-resume",
                mode="run",
                selected_stages=["implement"],
            ),
            config=manager.get_thread_config(),
        )
        manager.save_pending_interrupt(result["__interrupt__"][0].value)

    resumed = resume_run_from_checkpoint(
        tmp_path / "runs",
        "run-resume",
        resume_value={"decision_type": "approve"},
        graph_builder=_interrupt_graph,
    )

    assert resumed["messages"] == [
        {"type": "resumed", "decision_type": "approve"}
    ]
    assert manager.load_pending_interrupt() is None


def test_resume_inspection_falls_back_to_artifacts_without_checkpoint(tmp_path) -> None:
    _minimal_run_dir(tmp_path / "runs")

    summary = inspect_run_for_resume(tmp_path / "runs", "run-resume")

    assert summary.status == "read_only_artifacts"
    assert summary.checkpoint_status == "missing"
    assert summary.final_report_excerpt.startswith("# Final Report")
    assert summary.artifacts[0]["artifact_id"] == "implementation_stage_result"


def test_resume_inspection_falls_back_to_artifacts_for_corrupt_checkpoint(tmp_path) -> None:
    run_dir = _minimal_run_dir(tmp_path / "runs")
    (run_dir / "checkpoints.sqlite").write_text("not sqlite", encoding="utf-8")

    summary = inspect_run_for_resume(tmp_path / "runs", "run-resume")

    assert summary.status == "read_only_artifacts"
    assert summary.checkpoint_status == "corrupt"
    assert "Run completed" in summary.final_report_excerpt


def test_corrupt_pending_interrupt_does_not_crash_resume_inspection(tmp_path) -> None:
    run_dir = _minimal_run_dir(tmp_path / "runs")
    manager = CheckpointManager(run_dir, run_id="run-resume")
    manager.initialize_sqlite()
    manager.pending_interrupt_path.write_text("{not json", encoding="utf-8")

    summary = inspect_run_for_resume(tmp_path / "runs", "run-resume")

    assert summary.status == "checkpoint_available"
    assert summary.pending_interrupt is None


def test_cli_resume_displays_completed_run_summary(tmp_path) -> None:
    run_dir = _minimal_run_dir(tmp_path / "runs", completed=True)
    manager = CheckpointManager(run_dir, run_id="run-resume")
    manager.initialize_sqlite()
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "resume",
            "--run-id",
            "run-resume",
            "--output-root",
            str(tmp_path / "runs"),
        ],
    )

    assert result.exit_code == 0
    assert "run-resume" in result.output
    assert "completed" in result.output
    assert "Final Report" in result.output


def test_cli_resume_invalid_decision_json_fails_cleanly(tmp_path) -> None:
    run_dir = _minimal_run_dir(tmp_path / "runs")
    manager = CheckpointManager(run_dir, run_id="run-resume")
    manager.initialize_sqlite()
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "resume",
            "--run-id",
            "run-resume",
            "--output-root",
            str(tmp_path / "runs"),
            "--decision-json",
            "{not json",
        ],
    )

    assert result.exit_code != 0
    assert "Invalid --decision-json" in result.output
    assert "Traceback" not in result.output
