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
  codeagent vscode-run --config task.yaml
  codeagent benchmark --config benchmark/benchmark.yaml
  codeagent inspect-run --run-dir codeagent_runs/<run_id>
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
    requirements: list[str] | None = typer.Option(
        None,
        "--requirements",
        "-r",
        help="可重复传入的需求/设计/验收材料路径；未提供配置文件时用于构造输入材料。",
    ),
    model_name: str | None = typer.Option(
        None,
        "--model",
        "--model-name",
        help="本次 run 使用的模型名称，例如 google/gemini-3.5-flash。",
    ),
    auto_approve: bool = typer.Option(
        False,
        "--auto-approve",
        help="自动通过计划、补丁和命令审批，用于无人值守 run。",
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
            requirements=requirements,
            model_name=model_name,
            auto_approve=auto_approve,
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


@app.command(name="vscode-run")
def vscode_run(
    config: str = typer.Option(
        ...,
        "--config",
        "-c",
        help="VS Code 插件生成的 YAML 或 JSON 任务配置文件路径。",
    ),
) -> None:
    """以 JSONL 桥接协议运行任务，供 VS Code Webview 插件调用。"""
    from codeagent.cli.plugin_bridge import run_vscode_bridge

    exit_code = run_vscode_bridge(config)
    raise typer.Exit(exit_code)


@app.command(name="inspect-run")
def inspect_run(
    run_dir: str = typer.Option(
        ...,
        "--run-dir",
        "-r",
        help="要诊断的 CodeAgent 运行目录。",
    ),
    write: bool = typer.Option(
        True,
        "--write/--no-write",
        help="是否写入 run_health.json 和 run_health.md。",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="以 JSON 输出诊断结果。",
    ),
) -> None:
    """检查运行目录并生成可观测性健康摘要。

    示例: codeagent inspect-run --run-dir codeagent_runs/<run_id>
    """
    import json

    from codeagent.cli.inspect_run import (
        inspect_run_health,
        render_run_health_console,
        write_run_health_summary,
    )

    try:
        if write:
            artifacts = write_run_health_summary(run_dir)
            payload = artifacts.payload
        else:
            payload = inspect_run_health(run_dir)
    except OSError as exc:
        console.print(f"运行目录无法读取：{exc}")
        raise typer.Exit(1) from exc
    if json_output:
        console.print(json.dumps(payload, ensure_ascii=False, indent=2), markup=False)
    else:
        console.print(render_run_health_console(payload), markup=False)
        if write:
            console.print("已写入：run_health.json, run_health.md")
    if payload.get("final_status") != "succeeded":
        raise typer.Exit(1)


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
