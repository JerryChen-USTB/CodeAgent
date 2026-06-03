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
    implemented_commands = [
        (["run", "--help"], "Run a task from config or CLI options."),
        (["implement", "--help"], "Run the implementation stage."),
        (["test", "--help"], "Run the testing stage."),
        (["debug", "--help"], "Run the debugging stage."),
        (["repair", "--help"], "Run the repair stage."),
        (["benchmark", "--help"], "Run benchmark cases and aggregate results."),
    ]
    for command, description in implemented_commands:
        result = runner.invoke(app, command)
        assert result.exit_code == 0
        assert "Usage:" in result.output
        assert description in result.output
        assert "Example:" in result.output

    for command in [["resume", "--help"]]:
        result = runner.invoke(app, command)
        assert result.exit_code == 0
        assert "Usage:" in result.output
        assert "Planned skeleton" in result.output
        assert "Example:" in result.output


def test_run_requires_config_or_project() -> None:
    result = runner.invoke(app, ["run"])
    assert result.exit_code != 0
    assert "Provide --config or --project" in result.output


def test_benchmark_command_prints_summary_for_temp_config(tmp_path) -> None:
    config_path = tmp_path / "benchmark.yaml"
    config_path.write_text(
        f"name: cli_contract\noutput_dir: {(tmp_path / 'runs').as_posix()}\ncases: []\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["benchmark", "--config", str(config_path)])

    assert result.exit_code == 0
    assert "Benchmark completed" in result.output
    assert "success_rate=0.00" in result.output
