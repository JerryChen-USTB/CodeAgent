"""Typer application bootstrap."""

from __future__ import annotations

import typer
from rich.console import Console

from codeagent import __version__

console = Console()

ROOT_HELP = """CLI-based LangGraph and LangChain software-engineering agent.

Examples:

  codeagent wizard
  codeagent run --config task.yaml
  codeagent benchmark --config benchmark/benchmark.yaml
  codeagent resume --run-id <run_id>
"""


app = typer.Typer(
    name="codeagent",
    help=ROOT_HELP,
    no_args_is_help=True,
    invoke_without_command=True,
)


@app.callback()
def root(
    version: bool = typer.Option(
        False,
        "--version",
        help="Show CodeAgent version and exit.",
    ),
) -> None:
    """Run CodeAgent commands."""
    if version:
        console.print(f"codeagent {__version__}")
        raise typer.Exit()


@app.command()
def wizard() -> None:
    """Launch the guided task setup flow."""
    from codeagent.cli.wizard import wizard_command

    wizard_command()


@app.command()
def run(
    config: str | None = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to a YAML or JSON task configuration file.",
    ),
    project: str | None = typer.Option(
        None,
        "--project",
        "-p",
        help="Project directory to operate on when no config file is supplied.",
    ),
    stages: str | None = typer.Option(
        None,
        "--stages",
        help="Comma-separated stage list, for example implement,test,debug,repair.",
    ),
    output_dir: str | None = typer.Option(
        None,
        "--output-dir",
        help="Directory under which a unique run directory will be created.",
    ),
    test_cmd: str | None = typer.Option(
        None,
        "--test-cmd",
        help="Test command used by testing/debugging/repair stages.",
    ),
) -> None:
    """Run a task from config or CLI options.

    Example: codeagent run --config task.yaml
    """
    from codeagent.config.cli_mapping import task_config_from_run_options

    task_config = _build_or_exit(
        lambda: task_config_from_run_options(
            config_path=config,
            project=project,
            stages=stages,
            output_dir=output_dir,
            test_cmd=test_cmd,
        )
    )
    _execute_or_exit(task_config)


@app.command()
def implement(
    project: str = typer.Option(
        ...,
        "--project",
        "-p",
        help="Project directory to implement changes in.",
    ),
    requirements: str = typer.Option(
        ...,
        "--requirements",
        "-r",
        help="Requirements document path.",
    ),
    output_dir: str | None = typer.Option(
        None,
        "--output-dir",
        help="Directory under which a unique run directory will be created.",
    ),
) -> None:
    """Run the implementation stage.

    Example: codeagent implement --project ./repo --requirements requirements.md
    """
    from codeagent.config.cli_mapping import task_config_for_stage_command

    task_config = _build_or_exit(
        lambda: task_config_for_stage_command(
            stage="implement",
            project=project,
            requirements=requirements,
            output_dir=output_dir,
        )
    )
    _execute_or_exit(task_config)


@app.command(name="test")
def test_command(
    project: str = typer.Option(
        ...,
        "--project",
        "-p",
        help="Project directory to test.",
    ),
    test_cmd: str = typer.Option(
        "pytest -q",
        "--test-cmd",
        help="Test command to run after approval.",
    ),
    output_dir: str | None = typer.Option(
        None,
        "--output-dir",
        help="Directory under which a unique run directory will be created.",
    ),
) -> None:
    """Run the testing stage.

    Example: codeagent test --project ./repo --test-cmd "pytest -q"
    """
    from codeagent.config.cli_mapping import task_config_for_stage_command

    task_config = _build_or_exit(
        lambda: task_config_for_stage_command(
            stage="test",
            project=project,
            test_cmd=test_cmd,
            output_dir=output_dir,
        )
    )
    _execute_or_exit(task_config)


@app.command()
def debug(
    project: str = typer.Option(
        ...,
        "--project",
        "-p",
        help="Project directory to inspect.",
    ),
    test_cmd: str = typer.Option(
        "pytest -q",
        "--test-cmd",
        help="Failing test command.",
    ),
    log: str | None = typer.Option(
        None,
        "--log",
        help="Path to a failing test log.",
    ),
    output_dir: str | None = typer.Option(
        None,
        "--output-dir",
        help="Directory under which a unique run directory will be created.",
    ),
) -> None:
    """Run the debugging stage.

    Example: codeagent debug --project ./repo --test-cmd "pytest -q" --log failing.log
    """
    from codeagent.config.cli_mapping import task_config_for_stage_command

    task_config = _build_or_exit(
        lambda: task_config_for_stage_command(
            stage="debug",
            project=project,
            test_cmd=test_cmd,
            log=log,
            output_dir=output_dir,
        )
    )
    _execute_or_exit(task_config)


@app.command()
def repair(
    project: str = typer.Option(
        ...,
        "--project",
        "-p",
        help="Project directory to repair.",
    ),
    test_cmd: str = typer.Option(
        "pytest -q",
        "--test-cmd",
        help="Regression test command.",
    ),
    output_dir: str | None = typer.Option(
        None,
        "--output-dir",
        help="Directory under which a unique run directory will be created.",
    ),
) -> None:
    """Run the repair stage.

    Example: codeagent repair --project ./repo --test-cmd "pytest -q"
    """
    from codeagent.config.cli_mapping import task_config_for_stage_command

    task_config = _build_or_exit(
        lambda: task_config_for_stage_command(
            stage="repair",
            project=project,
            test_cmd=test_cmd,
            output_dir=output_dir,
        )
    )
    _execute_or_exit(task_config)


@app.command()
def benchmark(
    config: str = typer.Option(
        ...,
        "--config",
        "-c",
        help="Benchmark YAML configuration file.",
    ),
) -> None:
    """Run benchmark cases and aggregate results.

    Example: codeagent benchmark --config benchmark/benchmark.yaml
    """
    from codeagent.benchmark.runner import BenchmarkRunner
    from codeagent.cli.progress import ProgressReporter
    from codeagent.config.loader import ConfigLoadError

    try:
        result = BenchmarkRunner(reporter=ProgressReporter(console)).run_config(config)
    except (ConfigLoadError, ValueError, OSError) as exc:
        console.print(f"Invalid benchmark configuration: {exc}")
        raise typer.Exit(1) from exc
    console.print(
        "Benchmark completed: "
        f"success_rate={result.success_rate:.2f} "
        f"({result.success_cases}/{result.total_cases}) "
        f"blocked={result.blocked_cases}"
    )
    console.print(f"Benchmark directory: {result.benchmark_run_dir}")


@app.command()
def resume(
    run_id: str = typer.Option(
        ...,
        "--run-id",
        help="Existing run identifier under codeagent_runs/.",
    ),
    output_root: str = typer.Option(
        "codeagent_runs",
        "--output-root",
        help="Directory containing CodeAgent run directories.",
    ),
    decision_json: str | None = typer.Option(
        None,
        "--decision-json",
        help="JSON value used to resume a pending interrupt.",
    ),
) -> None:
    """Planned skeleton: resume or inspect a previous run.

    Example: codeagent resume --run-id <run_id>
    """
    from codeagent.cli.resume import (
        inspect_run_for_resume,
        parse_resume_value,
        render_resume_summary,
        resume_run_from_checkpoint,
    )

    summary = inspect_run_for_resume(output_root, run_id)
    if summary.status == "not_found":
        console.print(render_resume_summary(summary))
        raise typer.Exit(1)
    if decision_json is not None:
        try:
            resume_value = parse_resume_value(decision_json)
        except ValueError as exc:
            console.print(f"Invalid --decision-json: {exc}")
            raise typer.Exit(1) from exc
        result = resume_run_from_checkpoint(
            output_root,
            run_id,
            resume_value=resume_value,
        )
        console.print(f"Resumed run {run_id}.")
        if isinstance(result, dict) and result.get("final_status"):
            console.print(f"Final status: {result['final_status']}")
        return
    console.print(render_resume_summary(summary))


def _build_or_exit(builder):
    try:
        return builder()
    except ValueError as exc:
        console.print(f"Invalid task configuration: {exc}")
        raise typer.Exit(1) from exc
    except Exception as exc:
        from codeagent.config.loader import ConfigLoadError

        if not isinstance(exc, ConfigLoadError):
            raise
        console.print(f"Invalid task configuration: {exc}")
        raise typer.Exit(1) from exc


def _execute_or_exit(task_config) -> None:
    from codeagent.cli.executor import execute_task_config
    from codeagent.cli.progress import ProgressReporter

    result = execute_task_config(task_config, reporter=ProgressReporter(console))
    console.print(f"Final status: {result.final_status}")
    if result.final_status != "succeeded":
        raise typer.Exit(1)


def main() -> None:
    """Console-script entry point."""
    app()
