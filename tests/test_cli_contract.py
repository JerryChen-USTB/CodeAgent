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
        "vscode-run",
        "inspect-run",
        "resume",
    ]:
        assert command in result.output
    assert "示例:" in result.output
    assert "codeagent run --config task.yaml" in result.output


def test_core_command_help_contracts() -> None:
    implemented_commands = [
        (["run", "--help"], "从配置文件或 CLI 参数运行任务。"),
        (["implement", "--help"], "运行实现阶段。"),
        (["test", "--help"], "运行测试阶段。"),
        (["debug", "--help"], "运行调试阶段。"),
        (["repair", "--help"], "运行修复阶段。"),
        (["benchmark", "--help"], "运行 benchmark case 并汇总结果。"),
        (["inspect-run", "--help"], "检查运行目录并生成可观测性健康摘要。"),
    ]
    for command, description in implemented_commands:
        result = runner.invoke(app, command)
        assert result.exit_code == 0
        assert "Usage:" in result.output
        assert description in result.output
        assert "示例:" in result.output

    for command in [["resume", "--help"]]:
        result = runner.invoke(app, command)
        assert result.exit_code == 0
        assert "Usage:" in result.output
        assert "检查或恢复已有运行。" in result.output
        assert "示例:" in result.output


def test_run_requires_config_or_project() -> None:
    result = runner.invoke(app, ["run"])
    assert result.exit_code != 0
    assert "请提供 --config 或 --project" in result.output


def test_benchmark_command_prints_summary_for_temp_config(tmp_path) -> None:
    config_path = tmp_path / "benchmark.yaml"
    config_path.write_text(
        f"name: cli_contract\noutput_dir: {(tmp_path / 'runs').as_posix()}\ncases: []\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["benchmark", "--config", str(config_path)])

    assert result.exit_code == 0
    assert "Benchmark 已完成" in result.output
    assert "success_rate=0.00" in result.output
