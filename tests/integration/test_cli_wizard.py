from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from rich.console import Console
from typer.testing import CliRunner

from codeagent.cli.app import app
from codeagent.cli.approval_console import (
    ApprovalConsole,
    ApprovalInputError,
    parse_approval_decision,
)
from codeagent.cli.progress import ProgressEventFormatter, ProgressReporter
from codeagent.cli.wizard import (
    _MANUAL_MATERIAL_SENTINEL,
    _discover_input_material_candidates,
    WizardPromptAnswers,
    build_task_config_from_answers,
    render_task_summary,
    write_wizard_cancellation_report,
)
from codeagent.runtime.run_context import create_run_context
from codeagent.tools.hitl import ApprovalRequest


runner = CliRunner()


def _project(tmp_path: Path) -> tuple[Path, Path]:
    project = tmp_path / "project"
    project.mkdir()
    requirements = tmp_path / "requirements.md"
    requirements.write_text("# Requirement\n\nAdd a calculator helper.\n", encoding="utf-8")
    return project, requirements


def _approval_request() -> ApprovalRequest:
    return ApprovalRequest(
        interrupt_id="approve-command-1",
        action="approve_command",
        title="Approve test command",
        payload={"command": "pytest -q", "cwd": "."},
        risk_level="medium",
        allowed_decisions=("approve", "edit", "reject", "cancel"),
    )


def test_wizard_answers_build_normalized_task_config_and_summary(tmp_path) -> None:
    project, requirements = _project(tmp_path)
    output_dir = tmp_path / "runs"

    config = build_task_config_from_answers(
        WizardPromptAnswers(
            stages="implement,test",
            project_path=str(project),
            input_material_paths=[str(requirements)],
            output_dir=str(output_dir),
            test_command="python -m pytest tests -q",
        )
    )
    summary = render_task_summary(config)

    assert config.mode == "wizard"
    assert [stage.value for stage in config.stages] == ["implement", "test"]
    assert config.project_path == project.resolve()
    assert config.output_dir == output_dir.resolve()
    assert config.input_materials[0].material_type == "requirements"
    assert config.input_materials[0].path == requirements.resolve()
    assert config.test_command.command == "python -m pytest tests -q"
    assert "执行阶段：implement, test" in summary
    assert str(project.resolve()) in summary
    assert "python -m pytest tests -q" in summary


def test_wizard_rejects_file_as_project_path(tmp_path) -> None:
    project_file = tmp_path / "not_a_project.py"
    project_file.write_text("print('not a directory')\n", encoding="utf-8")

    with pytest.raises(ValueError, match="项目目录 必须是目录"):
        build_task_config_from_answers(
            WizardPromptAnswers(
                stages="implement",
                project_path=str(project_file),
                output_dir=str(tmp_path / "runs"),
            )
        )


def test_wizard_discovers_requirements_next_to_clean_workspace_and_filters_secrets(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    demo_root = tmp_path / "codeagent_runs" / "interactive_demo" / "todo_manager"
    workspace = demo_root / "workspace"
    workspace.mkdir(parents=True)
    requirements = demo_root / "requirements.md"
    requirements.write_text("# Todo requirements\n", encoding="utf-8")
    (demo_root / "Software Engineering Project.txt").write_text(
        "contains a provider key and must not be offered\n",
        encoding="utf-8",
    )
    (demo_root / "openrouter_token.txt").write_text("sensitive\n", encoding="utf-8")

    choices = _discover_input_material_candidates(str(workspace))
    titles = [title for title, _value in choices]
    values = [value for _title, value in choices]

    assert str(requirements.resolve()) in values
    assert any(title.startswith("requirements.md") for title in titles)
    assert _MANUAL_MATERIAL_SENTINEL in values
    assert all("Software Engineering Project.txt" not in value for value in values)
    assert all("openrouter_token.txt" not in value for value in values)


def test_wizard_command_accepts_scripted_input_and_runs_agent(tmp_path, monkeypatch) -> None:
    project, requirements = _project(tmp_path)
    output_dir = tmp_path / "runs"
    scripted_input = "\n".join(
        [
            "implement,test",
            str(project),
            str(requirements),
            "",
            str(output_dir),
            "pytest -q",
            "y",
            "",
        ]
    )

    def fake_run(config):
        context = create_run_context(config, output_root=config.output_dir)
        return SimpleNamespace(final_status="succeeded", run_dir=context.run_dir)

    monkeypatch.setattr("codeagent.cli.wizard._run_agent_from_wizard", fake_run)

    result = runner.invoke(app, ["wizard"], input=scripted_input)

    assert result.exit_code == 0
    assert "CodeAgent 中文任务表单" in result.output
    assert "选择要执行的阶段组合" in result.output
    assert "项目目录" in result.output
    assert "选择输入材料" in result.output
    assert "requirements.md" in result.output
    assert "手动添加输入材料路径" in result.output
    assert "输出目录" in result.output
    assert "测试命令" in result.output
    assert "任务摘要" in result.output
    assert "正在启动 CodeAgent" in result.output
    run_dirs = [path for path in output_dir.iterdir() if path.is_dir()]
    assert len(run_dirs) == 1
    task_config = (run_dirs[0] / "task_config.yaml").read_text(encoding="utf-8")
    assert "mode: wizard" in task_config
    assert "test" in task_config
    assert (run_dirs[0] / "final_report.md").exists()


def test_wizard_cancellation_writes_final_report_without_touching_project(tmp_path) -> None:
    project, requirements = _project(tmp_path)
    source_file = project / "calculator.py"
    source_file.write_text("VALUE = 1\n", encoding="utf-8")
    config = build_task_config_from_answers(
        WizardPromptAnswers(
            stages="implement,test",
            project_path=str(project),
            input_material_paths=[str(requirements)],
            output_dir=str(tmp_path / "runs"),
            test_command="pytest -q",
        )
    )

    run_dir = write_wizard_cancellation_report(
        config,
        reason="User cancelled at task summary.",
    )

    assert source_file.read_text(encoding="utf-8") == "VALUE = 1\n"
    report = (run_dir / "final_report.md").read_text(encoding="utf-8")
    stage_result = json.loads(
        (run_dir / "wizard" / "stage_result.json").read_text(encoding="utf-8")
    )
    assert stage_result["status"] == "cancelled"
    assert "cancelled" in report
    assert "User cancelled at task summary." in report


@pytest.mark.parametrize(
    ("raw", "decision_type"),
    [
        ("a", "approve"),
        ("approve", "approve"),
        ("批准", "approve"),
        ("r", "reject"),
        ("拒绝", "reject"),
        ("cancel", "cancel"),
        ("取消", "cancel"),
    ],
)
def test_approval_console_parses_basic_decisions(raw: str, decision_type: str) -> None:
    decision = parse_approval_decision(raw, request=_approval_request())

    assert decision.interrupt_id == "approve-command-1"
    assert decision.decision_type == decision_type


def test_approval_console_parses_edit_payload_and_rejects_disallowed_choice() -> None:
    request = _approval_request()

    decision = parse_approval_decision(
        "edit",
        request=request,
        edited_payload_text='{"command": "python -m pytest tests/unit -q"}',
        comment="narrow the command",
    )

    assert decision.decision_type == "edit"
    assert decision.edited_payload == {"command": "python -m pytest tests/unit -q"}
    assert decision.comment == "narrow the command"
    with pytest.raises(ApprovalInputError, match="不适用于"):
        parse_approval_decision("respond", request=request, comment="explain first")


def test_approval_console_prompt_can_be_scripted() -> None:
    console = ApprovalConsole(input_func=lambda _: "c")

    decision = console.prompt(_approval_request())

    assert decision.decision_type == "cancel"


def test_progress_formatter_and_reporter_render_stream_events() -> None:
    formatter = ProgressEventFormatter()
    console = Console(record=True, force_terminal=False)
    reporter = ProgressReporter(console=console, formatter=formatter)
    events = [
        {"type": "node_completed", "node": "testing"},
        {
            "type": "route_decision",
            "from_node": "testing",
            "to_node": "debugging",
            "reason": "tests failed",
        },
        {
            "type": "stage_result",
            "stage": "testing",
            "status": "failed",
            "summary": "2 tests failed",
        },
        {"type": "tool_call", "tool_name": "run_shell", "status": "succeeded"},
        {"type": "final_status", "status": "failed"},
    ]

    lines = [formatter.format_event(event) for event in events]
    reporter.render_events(events)
    output = console.export_text()

    assert lines[0] == "[节点] 测试阶段 已完成"
    assert "[路由] 测试阶段 -> 调试阶段: tests failed" in lines
    assert "[结果] 测试阶段 失败: 2 tests failed" in lines
    assert "[工具] run_shell 成功" in lines
    assert "[最终结果] 失败" in lines
    assert "[结果] 测试阶段 失败: 2 tests failed" in output
