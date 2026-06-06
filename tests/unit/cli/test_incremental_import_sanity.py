from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from codeagent.cli.executor import _single_file_local_import_error


def test_single_file_patch_rejects_missing_local_import(tmp_path) -> None:
    package = tmp_path / "todo_manager"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "service.py").write_text("class TodoService: ...\n", encoding="utf-8")

    draft = SimpleNamespace(
        changes=[
            SimpleNamespace(
                path=Path("todo_manager/tui.py"),
                new_content=(
                    "from todo_manager.service import TodoService\n"
                    "from todo_manager.validators import InputValidator\n"
                ),
            )
        ]
    )
    context = SimpleNamespace(task_config=SimpleNamespace(project_path=tmp_path))

    error = _single_file_local_import_error(context, draft)

    assert "todo_manager.validators" in error


def test_single_file_patch_accepts_existing_local_import(tmp_path) -> None:
    package = tmp_path / "todo_manager"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "service.py").write_text("class InputValidator: ...\n", encoding="utf-8")

    draft = SimpleNamespace(
        changes=[
            SimpleNamespace(
                path=Path("todo_manager/tui.py"),
                new_content="from todo_manager.service import InputValidator\n",
            )
        ]
    )
    context = SimpleNamespace(task_config=SimpleNamespace(project_path=tmp_path))

    assert _single_file_local_import_error(context, draft) == ""


def test_single_file_patch_accepts_import_from_approved_future_plan_file(tmp_path) -> None:
    package = tmp_path / "todo_manager"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")

    draft = SimpleNamespace(
        changes=[
            SimpleNamespace(
                path=Path("todo_manager/__main__.py"),
                new_content="from todo_manager.app import main\n",
            )
        ]
    )
    plan = SimpleNamespace(changes=[SimpleNamespace(path=Path("todo_manager/app.py"))])
    context = SimpleNamespace(task_config=SimpleNamespace(project_path=tmp_path))

    assert _single_file_local_import_error(context, draft, plan=plan) == ""

