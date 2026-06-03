from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from codeagent.cli.app import app


runner = CliRunner()


def _debug_fixture(tmp_path: Path) -> tuple[Path, Path]:
    project = tmp_path / "project"
    project.mkdir()
    (project / "calculator.py").write_text(
        "def add(left, right):\n    return left - right\n",
        encoding="utf-8",
    )
    log = project / "failing.log"
    log.write_text(
        "\n".join(
            [
                "FAILED tests/test_calculator.py::test_add - AssertionError",
                "E assert 0 == 2",
                "1 failed, 3 passed in 0.05s",
            ]
        ),
        encoding="utf-8",
    )
    return project, log


def test_run_config_executes_debug_stage_and_writes_reports(tmp_path) -> None:
    project, log = _debug_fixture(tmp_path)
    output_dir = tmp_path / "runs"
    config_path = tmp_path / "task.yaml"
    config_path.write_text(
        f"""
task_id: debug-demo
stages: [debug]
project_path: {project.as_posix()}
output_dir: {output_dir.as_posix()}
input_materials:
  - material_type: error_log
    path: {log.as_posix()}
    required: true
test_command:
  command: "pytest -q"
""".strip(),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["run", "--config", str(config_path)])

    assert result.exit_code == 0
    assert "[stage] debugging completed" in result.output
    assert "[final] succeeded" in result.output
    run_dirs = [path for path in output_dir.iterdir() if path.is_dir()]
    assert len(run_dirs) == 1
    run_dir = run_dirs[0]
    metadata = json.loads((run_dir / "metadata.json").read_text(encoding="utf-8"))
    stage_result = json.loads(
        (run_dir / "debugging" / "stage_result.json").read_text(encoding="utf-8")
    )
    assert metadata["stages"] == ["debug"]
    assert stage_result["status"] == "succeeded"
    assert (run_dir / "debugging" / "debug_report.md").exists()
    assert (run_dir / "final_report.md").exists()


def test_debug_subcommand_maps_to_task_config_and_executes_static_log(tmp_path) -> None:
    project, log = _debug_fixture(tmp_path)
    output_dir = tmp_path / "runs"

    result = runner.invoke(
        app,
        [
            "debug",
            "--project",
            str(project),
            "--log",
            str(log),
            "--output-dir",
            str(output_dir),
        ],
    )

    assert result.exit_code == 0
    assert "[final] succeeded" in result.output
    run_dir = next(path for path in output_dir.iterdir() if path.is_dir())
    task_config = (run_dir / "task_config.yaml").read_text(encoding="utf-8")
    assert "stages:" in task_config
    assert "- debug" in task_config
    assert "mode: run" in task_config


def test_run_project_options_reject_invalid_stage_order(tmp_path) -> None:
    project, _log = _debug_fixture(tmp_path)

    result = runner.invoke(
        app,
        ["run", "--project", str(project), "--stages", "test,repair"],
    )

    assert result.exit_code != 0
    assert "Selected stages must be contiguous" in result.output


def test_run_config_rejects_file_as_project_path(tmp_path) -> None:
    project_file = tmp_path / "not_a_project.py"
    project_file.write_text("print('not a project directory')\n", encoding="utf-8")
    config_path = tmp_path / "task.yaml"
    config_path.write_text(
        f"""
stages: [debug]
project_path: {project_file.as_posix()}
""".strip(),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["run", "--config", str(config_path)])

    assert result.exit_code != 0
    assert "project_path must be a directory" in result.output


def test_implement_subcommand_creates_failed_run_report_when_plan_is_missing(tmp_path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    requirements = tmp_path / "requirements.md"
    requirements.write_text("Add a tiny feature.\n", encoding="utf-8")
    output_dir = tmp_path / "runs"

    result = runner.invoke(
        app,
        [
            "implement",
            "--project",
            str(project),
            "--requirements",
            str(requirements),
            "--output-dir",
            str(output_dir),
        ],
    )

    assert result.exit_code == 1
    assert "[final] failed" in result.output
    run_dir = next(path for path in output_dir.iterdir() if path.is_dir())
    stage_result = json.loads(
        (run_dir / "implementation" / "stage_result.json").read_text(encoding="utf-8")
    )
    report = (run_dir / "final_report.md").read_text(encoding="utf-8")
    assert stage_result["status"] == "failed"
    assert "requires a structured implementation plan" in stage_result["summary"]
    assert "requires a structured implementation plan" in report
