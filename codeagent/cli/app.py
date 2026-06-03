"""Typer application bootstrap."""

from __future__ import annotations

import typer
from rich.console import Console

from codeagent import __version__

console = Console()

ROOT_HELP = """基于 LangGraph 和 LangChain 的本地软件工程智能体。

示例:

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
    add_completion=False,
)


@app.callback()
def root(
    version: bool = typer.Option(
        False,
        "--version",
        help="显示 CodeAgent 版本并退出。",
    ),
) -> None:
    """运行 CodeAgent 命令。"""
    if version:
        console.print(f"codeagent {__version__}")
        raise typer.Exit()


@app.command()
def wizard() -> None:
    """启动中文任务表单并在确认后直接运行 Agent。"""
    from codeagent.cli.wizard import wizard_command

    wizard_command()


@app.command()
def run(
    config: str | None = typer.Option(
        None,
        "--config",
        "-c",
        help="YAML 或 JSON 任务配置文件路径。",
    ),
    project: str | None = typer.Option(
        None,
        "--project",
        "-p",
        help="未提供配置文件时要操作的项目目录。",
    ),
    stages: str | None = typer.Option(
        None,
        "--stages",
        help="用逗号分隔的阶段列表，例如 implement,test,debug,repair。",
    ),
    output_dir: str | None = typer.Option(
        None,
        "--output-dir",
        help="用于创建唯一运行目录的输出根目录。",
    ),
    test_cmd: str | None = typer.Option(
        None,
        "--test-cmd",
        help="testing/debugging/repair 阶段使用的测试命令。",
    ),
) -> None:
    """从配置文件或 CLI 参数运行任务。

    示例: codeagent run --config task.yaml
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
        help="要写入实现变更的项目目录。",
    ),
    requirements: str = typer.Option(
        ...,
        "--requirements",
        "-r",
        help="需求文档路径。",
    ),
    output_dir: str | None = typer.Option(
        None,
        "--output-dir",
        help="用于创建唯一运行目录的输出根目录。",
    ),
) -> None:
    """运行实现阶段。

    示例: codeagent implement --project ./repo --requirements requirements.md
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
        help="要测试的项目目录。",
    ),
    test_cmd: str = typer.Option(
        "pytest -q",
        "--test-cmd",
        help="审批后运行的测试命令。",
    ),
    output_dir: str | None = typer.Option(
        None,
        "--output-dir",
        help="用于创建唯一运行目录的输出根目录。",
    ),
) -> None:
    """运行测试阶段。

    示例: codeagent test --project ./repo --test-cmd "pytest -q"
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
        help="要分析的项目目录。",
    ),
    test_cmd: str = typer.Option(
        "pytest -q",
        "--test-cmd",
        help="失败复现用测试命令。",
    ),
    log: str | None = typer.Option(
        None,
        "--log",
        help="失败测试日志路径。",
    ),
    output_dir: str | None = typer.Option(
        None,
        "--output-dir",
        help="用于创建唯一运行目录的输出根目录。",
    ),
) -> None:
    """运行调试阶段。

    示例: codeagent debug --project ./repo --test-cmd "pytest -q" --log failing.log
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
        help="要修复的项目目录。",
    ),
    test_cmd: str = typer.Option(
        "pytest -q",
        "--test-cmd",
        help="回归验证测试命令。",
    ),
    output_dir: str | None = typer.Option(
        None,
        "--output-dir",
        help="用于创建唯一运行目录的输出根目录。",
    ),
) -> None:
    """运行修复阶段。

    示例: codeagent repair --project ./repo --test-cmd "pytest -q"
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
        help="Benchmark YAML 配置文件。",
    ),
) -> None:
    """运行 benchmark case 并汇总结果。

    示例: codeagent benchmark --config benchmark/benchmark.yaml
    """
    from codeagent.benchmark.runner import BenchmarkRunner
    from codeagent.cli.progress import ProgressReporter
    from codeagent.config.loader import ConfigLoadError

    try:
        result = BenchmarkRunner(reporter=ProgressReporter(console)).run_config(config)
    except (ConfigLoadError, ValueError, OSError) as exc:
        console.print(f"Benchmark 配置无效：{exc}")
        raise typer.Exit(1) from exc
    console.print(
        "Benchmark 已完成："
        f"success_rate={result.success_rate:.2f} "
        f"({result.success_cases}/{result.total_cases}) "
        f"blocked={result.blocked_cases}"
    )
    console.print(f"Benchmark 目录：{result.benchmark_run_dir}")


@app.command()
def resume(
    run_id: str = typer.Option(
        ...,
        "--run-id",
        help="codeagent_runs/ 下已有的运行 ID。",
    ),
    output_root: str = typer.Option(
        "codeagent_runs",
        "--output-root",
        help="包含 CodeAgent 运行目录的输出根目录。",
    ),
    decision_json: str | None = typer.Option(
        None,
        "--decision-json",
        help="用于恢复 pending interrupt 的 JSON 决策值。",
    ),
) -> None:
    """检查或恢复已有运行。

    示例: codeagent resume --run-id <run_id>
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
            console.print(f"--decision-json 无效：{exc}")
            raise typer.Exit(1) from exc
        result = resume_run_from_checkpoint(
            output_root,
            run_id,
            resume_value=resume_value,
        )
        console.print(f"已恢复运行 {run_id}。")
        if isinstance(result, dict) and result.get("final_status"):
            console.print(f"最终状态：{result['final_status']}")
        return
    console.print(render_resume_summary(summary))


def _build_or_exit(builder):
    try:
        return builder()
    except ValueError as exc:
        console.print(f"任务配置无效：{exc}")
        raise typer.Exit(1) from exc
    except Exception as exc:
        from codeagent.config.loader import ConfigLoadError

        if not isinstance(exc, ConfigLoadError):
            raise
        console.print(f"任务配置无效：{exc}")
        raise typer.Exit(1) from exc


def _execute_or_exit(task_config) -> None:
    from codeagent.cli.executor import execute_task_config
    from codeagent.cli.progress import ProgressReporter

    result = execute_task_config(task_config, reporter=ProgressReporter(console))
    console.print(f"最终状态：{result.final_status}")
    if result.final_status != "succeeded":
        raise typer.Exit(1)


def main() -> None:
    """Console-script entry point."""
    app()
