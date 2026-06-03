from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from codeagent.runtime.commands import CommandApproval
from codeagent.tools.shell_tools import CommandDeniedError, ShellRunner


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _approval(operation_id: str = "cmd-1") -> CommandApproval:
    return CommandApproval.approve(
        operation_id=operation_id,
        approved_by="unit-test",
        reason="unit test approved",
    )


def test_shell_runner_executes_approved_pytest_and_saves_logs(tmp_path) -> None:
    project = tmp_path / "project"
    _write(project / "test_ok.py", "def test_ok():\n    assert True\n")
    runner = ShellRunner(logs_dir=tmp_path / "logs")

    result = runner.run(
        f"{sys.executable} -m pytest -q",
        cwd=project,
        timeout_seconds=10,
        approval=_approval("pytest-ok"),
    )

    assert result.exit_code == 0
    assert result.timed_out is False
    assert "passed" in result.stdout
    assert result.stderr == ""
    assert result.stdout_log.read_text(encoding="utf-8") == result.stdout
    assert result.stderr_log.read_text(encoding="utf-8") == result.stderr
    record = json.loads(result.record_path.read_text(encoding="utf-8"))
    assert record["operation_id"] == "pytest-ok"
    assert record["approval"]["approved"] is True
    assert record["exit_code"] == 0


def test_shell_runner_captures_failure_exit_code(tmp_path) -> None:
    project = tmp_path / "project"
    _write(project / "test_fail.py", "def test_fail():\n    assert False\n")
    runner = ShellRunner(logs_dir=tmp_path / "logs")

    result = runner.run(
        f"{sys.executable} -m pytest -q",
        cwd=project,
        timeout_seconds=30,
        approval=_approval("pytest-fail"),
    )

    assert result.exit_code != 0
    assert result.timed_out is False
    assert "FAILED" in result.stdout


def test_shell_runner_captures_stderr_from_unittest(tmp_path) -> None:
    project = tmp_path / "project"
    _write(
        project / "test_stderr.py",
        "import sys\n"
        "import unittest\n\n"
        "class StderrTest(unittest.TestCase):\n"
        "    def test_stderr(self):\n"
        "        sys.stderr.write('visible stderr\\n')\n"
        "        self.assertTrue(True)\n",
    )
    runner = ShellRunner(logs_dir=tmp_path / "logs")

    result = runner.run(
        f"{sys.executable} -m unittest test_stderr",
        cwd=project,
        timeout_seconds=10,
        approval=_approval("unittest-stderr"),
    )

    assert result.exit_code == 0
    assert "visible stderr" in result.stderr
    assert result.stderr_log.read_text(encoding="utf-8") == result.stderr


def test_shell_runner_reports_timeout_and_saves_partial_logs(tmp_path) -> None:
    project = tmp_path / "project"
    _write(
        project / "test_sleep.py",
        "import time\n"
        "import unittest\n\n"
        "class SleepTest(unittest.TestCase):\n"
        "    def test_sleep(self):\n"
        "        print('before sleep')\n"
        "        time.sleep(5)\n",
    )
    runner = ShellRunner(logs_dir=tmp_path / "logs")

    result = runner.run(
        f"{sys.executable} -m unittest test_sleep",
        cwd=project,
        timeout_seconds=0.2,
        approval=_approval("timeout"),
    )

    assert result.exit_code is None
    assert result.timed_out is True
    assert "timed out" in result.stderr.lower()
    assert result.stdout_log.exists()
    assert result.stderr_log.exists()


def test_shell_runner_returns_truncated_stdout_preview_but_saves_full_log(
    tmp_path,
) -> None:
    project = tmp_path / "project"
    long_text = "x" * 5000
    _write(
        project / "test_long.py",
        "def test_long_output():\n"
        f"    print({long_text!r})\n"
        "    assert True\n",
    )
    runner = ShellRunner(logs_dir=tmp_path / "logs", max_output_chars=120)

    result = runner.run(
        f"{sys.executable} -m pytest -q -s",
        cwd=project,
        timeout_seconds=30,
        approval=_approval("long-output"),
    )

    record = json.loads(result.record_path.read_text(encoding="utf-8"))
    assert result.exit_code == 0
    assert result.stdout_truncated is True
    assert result.stdout_original_chars > 120
    assert len(result.stdout) < result.stdout_original_chars
    assert "truncated" in result.stdout
    assert long_text in result.stdout_log.read_text(encoding="utf-8")
    assert record["stdout_truncated"] is True
    assert record["stdout_original_chars"] == result.stdout_original_chars


def test_shell_runner_denies_unapproved_or_disallowed_commands(tmp_path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    runner = ShellRunner(logs_dir=tmp_path / "logs")

    with pytest.raises(CommandDeniedError, match="not approved"):
        runner.run(
            f"{sys.executable} -m pytest -q",
            cwd=project,
            timeout_seconds=10,
            approval=CommandApproval.reject(
                operation_id="reject",
                rejected_by="unit-test",
                reason="not allowed yet",
            ),
        )
    with pytest.raises(CommandDeniedError, match="not allowed"):
        runner.run(
            f"{sys.executable} -c \"print('nope')\"",
            cwd=project,
            timeout_seconds=10,
            approval=_approval("disallowed"),
        )


def test_shell_runner_denies_path_arguments_outside_cwd(tmp_path) -> None:
    project = tmp_path / "project"
    outside = tmp_path / "outside.py"
    project.mkdir()
    outside.write_text("x = 1\n", encoding="utf-8")
    runner = ShellRunner(logs_dir=tmp_path / "logs")

    with pytest.raises(CommandDeniedError, match="outside cwd"):
        runner.run(
            f"{sys.executable} -m py_compile {outside}",
            cwd=project,
            timeout_seconds=10,
            approval=_approval("outside-absolute"),
        )
    with pytest.raises(CommandDeniedError, match="outside cwd"):
        runner.run(
            f"{sys.executable} -m pytest ..",
            cwd=project,
            timeout_seconds=10,
            approval=_approval("outside-parent"),
        )


def test_shell_runner_denies_high_risk_pytest_options(tmp_path) -> None:
    project = tmp_path / "project"
    _write(project / "evil.py", "def test_evil():\n    assert True\n")
    runner = ShellRunner(logs_dir=tmp_path / "logs")

    with pytest.raises(CommandDeniedError, match="high-risk pytest option"):
        runner.run(
            f"{sys.executable} -m pytest --override-ini=python_files=evil.py evil.py",
            cwd=project,
            timeout_seconds=10,
            approval=_approval("override-ini"),
        )
    with pytest.raises(CommandDeniedError, match="high-risk pytest option"):
        runner.run(
            f"{sys.executable} -m pytest -o python_files=evil.py evil.py",
            cwd=project,
            timeout_seconds=10,
            approval=_approval("override-ini-short"),
        )
    with pytest.raises(CommandDeniedError, match="high-risk pytest option"):
        runner.run(
            f"{sys.executable} -m pytest -o=python_files=evil.py evil.py",
            cwd=project,
            timeout_seconds=10,
            approval=_approval("override-ini-short-equals"),
        )
    with pytest.raises(CommandDeniedError, match="high-risk pytest option"):
        runner.run(
            f"{sys.executable} -m pytest -pno:warnings",
            cwd=project,
            timeout_seconds=10,
            approval=_approval("pytest-plugin-combined"),
        )


def test_shell_runner_allows_py_compile_inside_cwd(tmp_path) -> None:
    project = tmp_path / "project"
    _write(project / "src" / "app.py", "x = 1\n")
    runner = ShellRunner(logs_dir=tmp_path / "logs")

    result = runner.run(
        f"{sys.executable} -m py_compile src/app.py",
        cwd=project,
        timeout_seconds=10,
        approval=_approval("py-compile"),
    )

    assert result.exit_code == 0


def test_shell_runner_records_benchmark_auto_approval(tmp_path) -> None:
    project = tmp_path / "project"
    _write(project / "test_ok.py", "def test_ok():\n    assert True\n")
    approval = CommandApproval.benchmark_auto_approve(
        operation_id="benchmark-pytest",
        reason="benchmark mode auto approval enabled",
    )
    runner = ShellRunner(logs_dir=tmp_path / "logs")

    result = runner.run(
        f"{sys.executable} -m pytest -q",
        cwd=project,
        timeout_seconds=10,
        approval=approval,
    )

    record = json.loads(result.record_path.read_text(encoding="utf-8"))
    assert record["approval"]["auto"] is True
    assert record["approval"]["reason"] == "benchmark mode auto approval enabled"
