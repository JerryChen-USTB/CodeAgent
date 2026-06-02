from __future__ import annotations

from typer.testing import CliRunner

from codeagent.cli.app import app


runner = CliRunner()


def test_root_help_lists_required_commands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in [
        "wizard",
        "run",
        "implement",
        "test",
        "debug",
        "repair",
        "benchmark",
        "resume",
    ]:
        assert command in result.output
    assert "Examples:" in result.output
    assert "codeagent run --config task.yaml" in result.output


def test_core_command_help_contracts() -> None:
    commands = [
        ["run", "--help"],
        ["implement", "--help"],
        ["test", "--help"],
        ["debug", "--help"],
        ["repair", "--help"],
        ["benchmark", "--help"],
        ["resume", "--help"],
    ]
    for command in commands:
        result = runner.invoke(app, command)
        assert result.exit_code == 0
        assert "Usage:" in result.output
        assert "Planned skeleton" in result.output
        assert "Example:" in result.output


def test_run_requires_config_or_project() -> None:
    result = runner.invoke(app, ["run"])
    assert result.exit_code != 0
    assert "Provide --config or --project" in result.output


def test_benchmark_dry_run_reports_config() -> None:
    result = runner.invoke(app, ["benchmark", "--config", "benchmark/benchmark.yaml"])
    assert result.exit_code == 0
    assert "benchmark/benchmark.yaml" in result.output
    assert "not implemented yet" in result.output
    assert "clean per-run directories" in result.output
