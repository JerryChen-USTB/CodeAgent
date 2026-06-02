"""Typer application bootstrap."""

from __future__ import annotations

import typer
from rich.console import Console

from codeagent import __version__
from codeagent.cli.progress import ProgressReporter
from codeagent.cli.wizard import wizard_command

console = Console()
reporter = ProgressReporter(console)

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
    """Planned skeleton: launch the guided task setup flow."""
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
) -> None:
    """Planned skeleton: run a task from config or CLI options.

    Example: codeagent run --config task.yaml
    """
    if config is None and project is None:
        raise typer.BadParameter("Provide --config or --project.")
    target = config or project or "<unknown>"
    stage_text = stages or "implement,test,debug,repair"
    reporter.planned("run", f"Target: {target}\nStages: {stage_text}")


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
) -> None:
    """Planned skeleton: run the implementation stage.

    Example: codeagent implement --project ./repo --requirements requirements.md
    """
    reporter.planned(
        "implement",
        f"Project: {project}\nRequirements: {requirements}",
    )


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
) -> None:
    """Planned skeleton: run the testing stage.

    Example: codeagent test --project ./repo --test-cmd "pytest -q"
    """
    reporter.planned("test", f"Project: {project}\nTest command: {test_cmd}")


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
) -> None:
    """Planned skeleton: run the debugging stage.

    Example: codeagent debug --project ./repo --test-cmd "pytest -q" --log failing.log
    """
    reporter.planned(
        "debug",
        f"Project: {project}\nTest command: {test_cmd}\nLog: {log or '<none>'}",
    )


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
) -> None:
    """Planned skeleton: run the repair stage.

    Example: codeagent repair --project ./repo --test-cmd "pytest -q"
    """
    reporter.planned("repair", f"Project: {project}\nTest command: {test_cmd}")


@app.command()
def benchmark(
    config: str = typer.Option(
        ...,
        "--config",
        "-c",
        help="Benchmark YAML configuration file.",
    ),
) -> None:
    """Planned skeleton: run benchmark cases and aggregate results.

    Example: codeagent benchmark --config benchmark/benchmark.yaml
    """
    reporter.planned(
        "benchmark",
        "Config: "
        f"{config}\nCases will be copied to clean per-run directories before execution.",
    )


@app.command()
def resume(
    run_id: str = typer.Option(
        ...,
        "--run-id",
        help="Existing run identifier under codeagent_runs/.",
    ),
) -> None:
    """Planned skeleton: resume or inspect a previous run.

    Example: codeagent resume --run-id <run_id>
    """
    reporter.planned("resume", f"Run id: {run_id}")


def main() -> None:
    """Console-script entry point."""
    app()
