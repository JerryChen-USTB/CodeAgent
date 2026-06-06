from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from codeagent.cli.executor import (
    _approval_context_refs,
    _display_path_ref,
    _print_approval_context,
    _terminal_link,
)
from codeagent.cli.progress import ProgressEventFormatter
from codeagent.tools.hitl import ApprovalRequest


def test_terminal_link_uses_absolute_file_uri_for_vscode(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TERM_PROGRAM", "vscode")
    monkeypatch.delenv("CODEAGENT_DISABLE_TERMINAL_LINKS", raising=False)
    monkeypatch.setattr(
        "codeagent.cli.executor.sys.stdout",
        SimpleNamespace(isatty=lambda: True),
    )
    ref = _display_path_ref("implementation/implementation_plan.md", base=tmp_path)

    rendered = _terminal_link(ref)

    assert ref.display == "implementation_plan.md (implementation/implementation_plan.md)"
    assert ref.absolute_path == (tmp_path / "implementation" / "implementation_plan.md").resolve()
    assert "\033]8;;" in rendered
    assert ref.absolute_path.as_uri() in rendered
    assert ref.display in rendered


def test_terminal_link_is_plain_text_when_stdout_is_not_tty(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("TERM_PROGRAM", "vscode")
    monkeypatch.delenv("CODEAGENT_DISABLE_TERMINAL_LINKS", raising=False)
    monkeypatch.setattr(
        "codeagent.cli.executor.sys.stdout",
        SimpleNamespace(isatty=lambda: False),
    )
    ref = _display_path_ref("implementation/implementation_plan.md", base=tmp_path)

    rendered = _terminal_link(ref)

    assert rendered == "implementation_plan.md (implementation/implementation_plan.md)"
    assert "\033]8;;" not in rendered


def test_terminal_link_can_be_disabled(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TERM_PROGRAM", "vscode")
    monkeypatch.setenv("CODEAGENT_DISABLE_TERMINAL_LINKS", "1")
    monkeypatch.setattr(
        "codeagent.cli.executor.sys.stdout",
        SimpleNamespace(isatty=lambda: True),
    )
    ref = _display_path_ref("todo_manager/models.py", base=tmp_path)

    rendered = _terminal_link(ref)

    assert rendered == "models.py (todo_manager/models.py)"
    assert "\033]8;;" not in rendered


def test_progress_formatter_keeps_link_uri_out_of_plain_text() -> None:
    formatter = ProgressEventFormatter()
    event = {
        "type": "agent_status",
        "stage": "implementation",
        "message": "auto-approved 自动通过补丁，目标文件：",
        "message_link": {
            "label": "models.py (todo_manager/models.py)",
            "uri": "file:///D:/Projects/CodeAgent/workspace/todo_manager/models.py",
        },
    }

    rendered = formatter.format_event(event)
    renderable = formatter.renderable_for_event(event)

    assert "models.py (todo_manager/models.py)" in rendered
    assert "file:///" not in rendered
    assert "]8;;" not in rendered
    assert "\033" not in rendered
    assert "models.py (todo_manager/models.py)" in renderable.plain
    assert "file:///" not in renderable.plain
    assert "]8;;" not in renderable.plain


def test_plan_approval_context_omits_planned_project_files(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    project_dir = tmp_path / "workspace"
    (run_dir / "implementation").mkdir(parents=True)
    (project_dir / "todo_manager").mkdir(parents=True)
    (run_dir / "implementation" / "implementation_plan.md").write_text(
        "# Plan\n",
        encoding="utf-8",
    )
    (project_dir / "todo_manager" / "existing.py").write_text(
        "VALUE = 1\n",
        encoding="utf-8",
    )
    context = SimpleNamespace(
        run_dir=run_dir,
        task_config=SimpleNamespace(project_path=project_dir),
    )
    request = ApprovalRequest(
        interrupt_id="implementation_plan",
        action="review_implementation_plan",
        title="Review plan",
        payload={
            "plan_path": "implementation/implementation_plan.md",
            "changed_files": [
                "todo_manager/existing.py",
                "todo_manager/new_file.py",
            ],
        },
        risk_level="medium",
        allowed_decisions=("approve", "respond"),
        default_decision="approve",
    )

    refs = _approval_context_refs(context, request)

    assert [ref.display for ref in refs] == [
        "implementation_plan.md (implementation/implementation_plan.md)"
    ]


def test_patch_approval_context_lists_only_existing_project_files(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    project_dir = tmp_path / "workspace"
    (run_dir / "implementation").mkdir(parents=True)
    (project_dir / "todo_manager").mkdir(parents=True)
    (run_dir / "implementation" / "implementation.patch.diff").write_text(
        "diff --git a/todo_manager/existing.py b/todo_manager/existing.py\n",
        encoding="utf-8",
    )
    (project_dir / "todo_manager" / "existing.py").write_text(
        "VALUE = 1\n",
        encoding="utf-8",
    )
    context = SimpleNamespace(
        run_dir=run_dir,
        task_config=SimpleNamespace(project_path=project_dir),
    )
    request = ApprovalRequest(
        interrupt_id="implementation_patch",
        action="approve_implementation_patch",
        title="Approve patch",
        payload={
            "patch_path": "implementation/implementation.patch.diff",
            "changed_files": [
                "todo_manager/existing.py",
                "todo_manager/new_file.py",
            ],
        },
        risk_level="medium",
        allowed_decisions=("approve", "reject"),
        default_decision="reject",
    )

    refs = _approval_context_refs(context, request)

    assert [ref.display for ref in refs] == [
        "implementation.patch.diff (implementation/implementation.patch.diff)",
        "existing.py (todo_manager/existing.py)",
    ]


def test_command_approval_context_shows_command_and_cwd(
    capsys,
    tmp_path: Path,
) -> None:
    class Recorder:
        def __init__(self) -> None:
            self.events: list[dict] = []

        def record(self, event_type: str, **payload) -> None:
            self.events.append({"event_type": event_type, **payload})

    run_dir = tmp_path / "run"
    project_dir = tmp_path / "workspace"
    project_dir.mkdir(parents=True)
    recorder = Recorder()
    context = SimpleNamespace(
        run_dir=run_dir,
        task_config=SimpleNamespace(project_path=project_dir),
        workflow_trace=recorder,
    )
    request = ApprovalRequest(
        interrupt_id="testing_command",
        action="approve_test_command",
        title="Run tests?",
        payload={
            "command": "python -m pytest tests -q",
            "changed_files": ["tests/test_app.py"],
        },
        risk_level="medium",
        allowed_decisions=("approve", "edit", "reject", "cancel"),
        default_decision="approve",
    )

    _print_approval_context(context, request)

    output = capsys.readouterr().out
    assert "将执行命令：" in output
    assert "python -m pytest tests -q" in output
    assert "工作目录：" in output
    assert project_dir.as_posix() in output
    assert recorder.events == [
        {
            "event_type": "approval_context_presented",
            "stage": "testing",
            "action": "approve_test_command",
            "files": [],
            "hint": "当前动作：同意后会在项目目录中执行命令。",
            "command": "python -m pytest tests -q",
            "cwd": project_dir.resolve().as_posix(),
        }
    ]
