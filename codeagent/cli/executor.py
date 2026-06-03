"""Execute normalized TaskConfig objects from CLI commands."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from codeagent.adapters.test_result import TestResult
from codeagent.config.schema import Stage, TaskConfig
from codeagent.errors.exceptions import ErrorRecord, utc_timestamp
from codeagent.reports import ArtifactKind, ArtifactRecord, ReportWriter, StageResult
from codeagent.reports.schemas import HumanDecision
from codeagent.runtime.commands import CommandApproval
from codeagent.runtime.run_context import RunContext, create_run_context
from codeagent.stages.debugging_service import (
    DEBUGGING_STAGE,
    REPRODUCTION_COMMAND_INTERRUPT_ID,
    DebuggingRequest,
    DebuggingService,
)
from codeagent.tools.hitl import ApprovalDecision
from codeagent.tools.pytest_tools import parse_shell_result
from codeagent.tools.shell_tools import CommandDeniedError, ShellRunner
from codeagent.workflow.checkpoint import CheckpointManager
from codeagent.workflow.events import stream_workflow_events
from codeagent.workflow.factory import WorkflowFactory
from codeagent.workflow.main_graph import StageHandler
from codeagent.workflow.state import AgentState, create_initial_state

from codeagent.cli.progress import ProgressReporter


@dataclass(frozen=True)
class CliRunResult:
    run_id: str
    run_dir: Path
    final_status: str
    stage_results: dict[str, StageResult]


def execute_task_config(
    task_config: TaskConfig,
    *,
    reporter: ProgressReporter | None = None,
) -> CliRunResult:
    """Run a normalized task config through the LangGraph main workflow."""
    progress = reporter or ProgressReporter()
    context = create_run_context(task_config, output_root=task_config.output_dir)
    manager = CheckpointManager(context.run_dir, run_id=context.run_id)
    initial = create_initial_state(
        run_id=context.run_id,
        mode=task_config.mode,
        selected_stages=[stage.value for stage in task_config.stages],
    )
    initial["max_repair_attempts"] = task_config.runtime.max_repair_attempts

    with manager.create_sqlite_saver() as saver:
        graph = WorkflowFactory(
            stage_handlers=_stage_handlers_for_cli(context),
        ).build(checkpointer=saver)
        thread_config = manager.get_thread_config()
        for event in stream_workflow_events(
            graph.stream(initial, config=thread_config)
        ):
            progress.render_event(event)
        final_state = dict(graph.get_state(config=thread_config).values)

    stage_results = _stage_results_from_state(final_state)
    _write_final_report(context, list(stage_results.values()))
    final_status = str(final_state.get("final_status") or "failed")
    progress.render_event({"type": "run_directory", "path": context.run_dir.as_posix()})
    return CliRunResult(
        run_id=context.run_id,
        run_dir=context.run_dir,
        final_status=final_status,
        stage_results=stage_results,
    )


def _stage_handlers_for_cli(context: RunContext) -> dict[str, StageHandler]:
    return {
        "implementation": _unsupported_stage_handler(
            context,
            stage="implementation",
            summary="Implementation stage requires a structured implementation plan before execution.",
            next_suggestion="Run after the LLM planner/coder stage supplies an ImplementationPlan.",
        ),
        "testing": _testing_command_handler(context),
        "debugging": _debugging_handler(context),
        "repair": _unsupported_stage_handler(
            context,
            stage="repair",
            summary="Repair stage requires a structured repair plan before execution.",
            next_suggestion="Run debugging first and provide a RepairPlan from the repair agent.",
        ),
    }


def _testing_command_handler(context: RunContext) -> StageHandler:
    def run(state: AgentState) -> dict[str, Any]:
        started_at = utc_timestamp()
        stage_dir = context.stage_dirs[Stage.TEST]
        stage_dir.mkdir(parents=True, exist_ok=True)
        writer = _writer(context)
        command = context.task_config.test_command.command
        approval = CommandApproval(
            operation_id="testing_cli_command",
            approved=True,
            decision_type="approve",
            decided_by="cli",
            reason="User supplied non-interactive test command.",
            auto=True,
        )
        writer.record_human_decision(
            HumanDecision(
                interrupt_id="testing_cli_command",
                action="approve_test_command",
                decision_type="approve",
                timestamp=utc_timestamp(),
                auto=True,
            )
        )
        try:
            shell = ShellRunner(
                logs_dir=stage_dir / "logs",
                max_output_chars=context.task_config.runtime.log_truncation_chars,
            ).run(
                command,
                cwd=context.task_config.project_path,
                timeout_seconds=context.task_config.test_command.timeout_seconds,
                approval=approval,
            )
            parsed = parse_shell_result(
                framework=context.task_config.test_framework,
                shell_result=shell,
            )
            result = _testing_result_from_parsed(
                context,
                parsed,
                started_at=started_at,
            )
        except (CommandDeniedError, ValueError, RuntimeError) as exc:
            result = _failed_stage_result(
                stage="testing",
                started_at=started_at,
                summary="Testing command failed before completion.",
                category="shell",
                message=str(exc),
                next_suggestion="Use an allowed pytest, unittest, or py_compile command.",
            )
        writer.write_stage_report(result)
        return _state_update_from_result(state, result)

    return run


def _debugging_handler(context: RunContext) -> StageHandler:
    service = DebuggingService(run_context=context)

    def run(state: AgentState) -> dict[str, Any]:
        request = _debugging_request_from_config(context, state)
        result = service.run(request)
        return _state_update_from_result(state, result)

    return run


def _unsupported_stage_handler(
    context: RunContext,
    *,
    stage: str,
    summary: str,
    next_suggestion: str,
) -> StageHandler:
    def run(state: AgentState) -> dict[str, Any]:
        result = _failed_stage_result(
            stage=stage,
            started_at=utc_timestamp(),
            summary=summary,
            category="validation",
            message=summary,
            next_suggestion=next_suggestion,
        )
        _writer(context).write_stage_report(result)
        return _state_update_from_result(state, result)

    return run


def _debugging_request_from_config(
    context: RunContext,
    state: AgentState,
) -> DebuggingRequest:
    failure_logs = _failure_logs_from_config(context, state)
    test_report_path = _testing_report_path(context, state)
    has_static_evidence = bool(failure_logs or test_report_path)
    return DebuggingRequest(
        test_command=None if has_static_evidence else context.task_config.test_command.command,
        command_approval=ApprovalDecision(
            interrupt_id=REPRODUCTION_COMMAND_INTERRUPT_ID,
            decision_type="reject" if has_static_evidence else "approve",
            auto=True,
            comment=(
                "Static failure evidence supplied by CLI."
                if has_static_evidence
                else "Non-interactive CLI reproduction command."
            ),
        ),
        failure_logs=failure_logs,
        test_report_path=test_report_path,
        framework=context.task_config.test_framework,
        command_timeout_seconds=context.task_config.test_command.timeout_seconds,
    )


def _failure_logs_from_config(context: RunContext, state: AgentState) -> list[Path]:
    paths = [
        material.path
        for material in context.task_config.input_materials
        if "log" in material.material_type.lower()
        or "failure" in material.material_type.lower()
    ]
    if paths:
        return paths
    if "testing" in state.get("stage_results", {}):
        logs_dir = context.stage_dirs[Stage.TEST] / "logs"
        return [
            path
            for path in (
                logs_dir / "testing_cli_command.stdout.log",
                logs_dir / "testing_cli_command.stderr.log",
            )
            if path.exists()
        ]
    return []


def _testing_report_path(context: RunContext, state: AgentState) -> Path | None:
    explicit = [
        material.path
        for material in context.task_config.input_materials
        if "test_report" in material.material_type.lower()
    ]
    if explicit:
        return explicit[0]
    if "testing" in state.get("stage_results", {}):
        path = context.stage_dirs[Stage.TEST] / "test_report.json"
        if path.exists():
            return path
    return None


def _testing_result_from_parsed(
    context: RunContext,
    parsed: TestResult,
    *,
    started_at: str,
) -> StageResult:
    stage_dir = context.stage_dirs[Stage.TEST]
    json_path = stage_dir / "test_report.json"
    md_path = stage_dir / "test_report.md"
    json_path.write_text(
        json.dumps(parsed.to_json_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    md_path.write_text(_render_test_report(parsed), encoding="utf-8")
    artifacts = [
        _record_artifact(
            context,
            "testing_test_report_json",
            "testing",
            ArtifactKind.JSON,
            json_path,
            "Parsed test result",
        ),
        _record_artifact(
            context,
            "testing_test_report",
            "testing",
            ArtifactKind.REPORT,
            md_path,
            "Testing report",
        ),
    ]
    for artifact_id, path, summary in (
        ("testing_stdout_log", stage_dir / "logs" / "testing_cli_command.stdout.log", "Testing stdout log"),
        ("testing_stderr_log", stage_dir / "logs" / "testing_cli_command.stderr.log", "Testing stderr log"),
        ("testing_command_record", stage_dir / "logs" / "testing_cli_command.command.json", "Testing command record"),
    ):
        if path.exists():
            artifacts.append(
                _record_artifact(
                    context,
                    artifact_id,
                    "testing",
                    ArtifactKind.LOG if path.suffix == ".log" else ArtifactKind.JSON,
                    path,
                    summary,
                )
            )
    if parsed.success:
        return StageResult(
            stage="testing",
            status="succeeded",
            started_at=started_at,
            ended_at=utc_timestamp(),
            summary=_test_summary(parsed),
            artifact_ids=artifacts,
            next_suggestion="Testing succeeded.",
        )
    return StageResult(
        stage="testing",
        status="failed",
        started_at=started_at,
        ended_at=utc_timestamp(),
        summary=_test_summary(parsed),
        artifact_ids=artifacts,
        error=ErrorRecord(
            error_id="testing_pytest_failure",
            stage="testing",
            node="testing",
            category="pytest_failure",
            message=parsed.error_summary or _test_summary(parsed),
            artifact_ids=artifacts,
            retryable=True,
        ),
        next_suggestion="Continue to debugging with saved test logs.",
    )


def _render_test_report(parsed: TestResult) -> str:
    lines = [
        "# Testing Report",
        "",
        f"- Framework: {parsed.framework}",
        f"- Success: {parsed.success}",
        f"- Command: {parsed.command}",
        f"- Exit code: {parsed.exit_code}",
        f"- Summary: {_test_summary(parsed)}",
        "",
        "## Failing Tests",
        "",
    ]
    if not parsed.failing_tests:
        lines.append("- <none>")
    for failure in parsed.failing_tests:
        lines.append(f"- {failure.nodeid}: {failure.outcome} {failure.message}")
    if parsed.error_summary:
        lines.extend(["", "## Error Summary", "", parsed.error_summary])
    return "\n".join(lines) + "\n"


def _test_summary(parsed: TestResult) -> str:
    return (
        f"{parsed.passed} passed, {parsed.failed} failed, "
        f"{parsed.errors} errors, {parsed.skipped} skipped"
    )


def _failed_stage_result(
    *,
    stage: str,
    started_at: str,
    summary: str,
    category: str,
    message: str,
    next_suggestion: str,
) -> StageResult:
    return StageResult(
        stage=stage,
        status="failed",
        started_at=started_at,
        ended_at=utc_timestamp(),
        summary=summary,
        error=ErrorRecord(
            error_id=f"{stage}_{category}",
            stage=stage,
            node=stage,
            category=category,  # type: ignore[arg-type]
            message=message,
            retryable=True,
        ),
        next_suggestion=next_suggestion,
    )


def _state_update_from_result(state: AgentState, result: StageResult) -> dict[str, Any]:
    stage_results = dict(state.get("stage_results", {}))
    stage_results[result.stage] = result.model_dump(mode="json", exclude_none=True)
    artifact_refs = list(state.get("artifact_refs", []))
    for artifact_id in result.artifact_ids:
        if artifact_id not in artifact_refs:
            artifact_refs.append(artifact_id)
    messages = list(state.get("messages", []))
    messages.append(
        {
            "type": "stage_completed",
            "stage": result.stage,
            "status": result.status,
            "summary": result.summary,
        }
    )
    return {
        "stage_results": stage_results,
        "artifact_refs": artifact_refs,
        "messages": messages,
    }


def _stage_results_from_state(state: dict[str, Any]) -> dict[str, StageResult]:
    results: dict[str, StageResult] = {}
    raw_results = state.get("stage_results") or {}
    if not isinstance(raw_results, dict):
        return results
    for stage, raw in raw_results.items():
        if isinstance(raw, dict):
            results[str(stage)] = StageResult.model_validate(raw)
    return results


def _write_final_report(context: RunContext, results: list[StageResult]) -> None:
    if results:
        _writer(context).write_final_report(results)


def _writer(context: RunContext) -> ReportWriter:
    return ReportWriter(
        run_dir=context.run_dir,
        artifact_store=context.artifact_store,
        transcript=context.transcript,
        decision_trace=context.decision_trace,
    )


def _record_artifact(
    context: RunContext,
    artifact_id: str,
    stage: str,
    kind: ArtifactKind,
    path: Path,
    summary: str,
) -> str:
    context.artifact_store.record(
        ArtifactRecord(
            artifact_id=artifact_id,
            stage=stage,
            kind=kind,
            path=path,
            summary=summary,
        )
    )
    context.artifact_store.write()
    return artifact_id
