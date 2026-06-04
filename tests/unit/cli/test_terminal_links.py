from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from codeagent.cli.executor import (
    _approval_context_refs,
    _display_path_ref,
    _terminal_link,
)
from codeagent.tools.hitl import ApprovalRequest


def test_terminal_link_uses_absolute_file_uri_for_vscode(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TERM_PROGRAM", "vscode")
    monkeypatch.delenv("CODEAGENT_DISABLE_TERMINAL_LINKS", raising=False)
    ref = _display_path_ref("implementation/implementation_plan.md", base=tmp_path)

    rendered = _terminal_link(ref)

    assert ref.display == "implementation_plan.md (implementation/implementation_plan.md)"
    assert ref.absolute_path == (tmp_path / "implementation" / "implementation_plan.md").resolve()
    assert "\033]8;;" in rendered
    assert ref.absolute_path.as_uri() in rendered
    assert ref.display in rendered


def test_terminal_link_can_be_disabled(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TERM_PROGRAM", "vscode")
    monkeypatch.setenv("CODEAGENT_DISABLE_TERMINAL_LINKS", "1")
    ref = _display_path_ref("todo_manager/models.py", base=tmp_path)

    rendered = _terminal_link(ref)

    assert rendered == "models.py (todo_manager/models.py)"
    assert "\033]8;;" not in rendered


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
