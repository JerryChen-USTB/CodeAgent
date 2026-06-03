"""Interactive wizard command and testable controller helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import typer

from codeagent.config.schema import CommandConfig, TaskConfig
from codeagent.reports.schemas import StageResult
from codeagent.reports.writer import ReportWriter
from codeagent.runtime.run_context import create_run_context


@dataclass(frozen=True)
class WizardPromptAnswers:
    """Raw answers collected by the semi-interactive CLI wizard."""

    stages: str
    project_path: str
    input_material_paths: list[str] = field(default_factory=list)
    output_dir: str = "codeagent_runs"
    test_command: str = "pytest -q"


def wizard_command() -> None:
    """Start the guided configuration wizard."""
    try:
        answers = _collect_answers()
        config = build_task_config_from_answers(answers)
    except ValueError as exc:
        typer.echo(f"Invalid wizard input: {exc}")
        raise typer.Exit(1) from exc

    typer.echo(render_task_summary(config))
    if not typer.confirm("Initialize run with this task configuration?", default=True):
        run_dir = write_wizard_cancellation_report(
            config,
            reason="User cancelled at task summary.",
        )
        typer.echo(f"Run cancelled. Final report: {run_dir / 'final_report.md'}")
        raise typer.Exit()

    context = create_run_context(config, output_root=config.output_dir)
    typer.echo(f"Run initialized: {context.run_id}")
    typer.echo(f"Run directory: {context.run_dir}")
    raise typer.Exit()


def build_task_config_from_answers(answers: WizardPromptAnswers) -> TaskConfig:
    """Build a normalized TaskConfig from raw wizard answers."""
    stages = _parse_stages(answers.stages)
    project_path = _resolve_existing_path(
        answers.project_path,
        label="project path",
        must_be_dir=True,
    )
    output_dir = Path(answers.output_dir).expanduser().resolve()
    input_materials = []
    for raw_path in answers.input_material_paths:
        if not raw_path.strip():
            continue
        path = _resolve_existing_path(raw_path, label="input material path")
        input_materials.append(
            {
                "type": "requirements",
                "path": path,
                "required": True,
                "multi": True,
                "description": "Collected by codeagent wizard.",
            }
        )

    test_command = answers.test_command.strip() or "pytest -q"
    return TaskConfig(
        stages=stages,
        project_path=project_path,
        output_dir=output_dir,
        input_materials=input_materials,
        test_command=CommandConfig(command=test_command),
        mode="wizard",
    )


def render_task_summary(config: TaskConfig) -> str:
    """Render the pre-execution summary required by the CLI SRS."""
    material_lines = [
        f"- {material.material_type}: {material.path}"
        for material in config.input_materials
    ]
    if not material_lines:
        material_lines = ["- <none>"]
    return "\n".join(
        [
            "Task Summary",
            "============",
            f"Stages: {', '.join(stage.value for stage in config.stages)}",
            f"Project: {config.project_path}",
            f"Output directory: {config.output_dir}",
            f"Test command: {config.test_command.command}",
            "Input materials:",
            *material_lines,
        ]
    )


def write_wizard_cancellation_report(config: TaskConfig, *, reason: str) -> Path:
    """Initialize a run directory and write a final report for a cancelled wizard run."""
    context = create_run_context(config, output_root=config.output_dir)
    writer = ReportWriter(
        run_dir=context.run_dir,
        artifact_store=context.artifact_store,
        transcript=context.transcript,
        decision_trace=context.decision_trace,
    )
    now = datetime.now(timezone.utc).isoformat()
    result = StageResult(
        stage="wizard",
        status="cancelled",
        started_at=now,
        ended_at=now,
        summary=reason,
        next_suggestion="Run codeagent wizard again or use codeagent run --config.",
    )
    writer.write_stage_report(result)
    writer.write_final_report([result])
    return context.run_dir


def _collect_answers() -> WizardPromptAnswers:
    typer.echo("CodeAgent wizard")
    typer.echo("Fill in the task fields below. Press Enter to use a shown default.")
    stages = _prompt_value(
        "Stages (comma-separated, contiguous)",
        default="implement,test,debug,repair",
    )
    project_path = _prompt_value("Project path", default=".")
    input_material = _prompt_value(
        "Input material path (blank to skip)",
        default="",
        show_default=False,
    )
    output_dir = _prompt_value("Output directory", default="codeagent_runs")
    test_command = _prompt_value("Test command", default="pytest -q")
    return WizardPromptAnswers(
        stages=stages,
        project_path=project_path,
        input_material_paths=[input_material],
        output_dir=output_dir,
        test_command=test_command,
    )


def _prompt_value(
    label: str,
    *,
    default: str,
    show_default: bool = True,
) -> str:
    suffix = f" [{default}]" if show_default and default else ""
    typer.echo(f"{label}{suffix}")
    typer.echo("> ", nl=False)
    value = input()
    if value == "":
        return default
    return value


def _parse_stages(raw_stages: str) -> list[str]:
    normalized = raw_stages.replace(";", ",")
    if "," in normalized:
        parts = normalized.split(",")
    else:
        parts = normalized.split()
    stages = [part.strip() for part in parts if part.strip()]
    if not stages:
        raise ValueError("at least one stage is required")
    return stages


def _resolve_existing_path(
    raw_path: str,
    *,
    label: str,
    must_be_dir: bool = False,
) -> Path:
    path = Path(raw_path).expanduser().resolve()
    if not path.exists():
        raise ValueError(f"{label} does not exist: {path}")
    if must_be_dir and not path.is_dir():
        raise ValueError(f"{label} must be a directory: {path}")
    return path
