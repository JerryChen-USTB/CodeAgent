from __future__ import annotations

from pathlib import Path

from codeagent.adapters.pytest_adapter import PytestResultParser
from codeagent.adapters.unittest_adapter import UnittestResultParser
from codeagent.runtime.commands import ShellResult
from codeagent.tools.pytest_tools import parse_shell_result
from codeagent.tools.pytest_tools import parse_test_result


def test_pytest_parser_extracts_pass_and_skip_counts() -> None:
    stdout = "....s                                                                    [100%]\n5 passed, 1 skipped in 0.12s\n"

    result = PytestResultParser().parse(stdout=stdout, stderr="", exit_code=0)

    assert result.framework == "pytest"
    assert result.success is True
    assert result.passed == 5
    assert result.failed == 0
    assert result.errors == 0
    assert result.skipped == 1
    assert result.confidence == "high"


def test_pytest_parser_extracts_failures_errors_and_summary() -> None:
    stdout = """
tests/test_app.py::test_ok PASSED
tests/test_app.py::test_bad FAILED
tests/test_app.py::test_error ERROR

================================== FAILURES ===================================
FAILED tests/test_app.py::test_bad - AssertionError: expected 2
ERROR tests/test_app.py::test_error - RuntimeError: boom
=========================== short test summary info ===========================
FAILED tests/test_app.py::test_bad - AssertionError: expected 2
ERROR tests/test_app.py::test_error - RuntimeError: boom
==================== 1 failed, 2 passed, 1 error, 1 skipped in 0.20s ====================
"""

    result = PytestResultParser().parse(stdout=stdout, stderr="", exit_code=1)

    assert result.success is False
    assert result.passed == 2
    assert result.failed == 1
    assert result.errors == 1
    assert result.skipped == 1
    assert {item.nodeid for item in result.failing_tests} == {
        "tests/test_app.py::test_bad",
        "tests/test_app.py::test_error",
    }
    assert "AssertionError" in result.error_summary
    assert "RuntimeError" in result.error_summary


def test_pytest_parser_reports_timeout() -> None:
    result = PytestResultParser().parse(
        stdout="",
        stderr="Command timed out after 0.2 seconds.\n",
        exit_code=None,
        timed_out=True,
    )

    assert result.success is False
    assert result.timed_out is True
    assert result.confidence == "high"
    assert "timed out" in result.error_summary.lower()


def test_unittest_parser_extracts_pass_fail_error_skip_counts() -> None:
    output = """
FAIL: test_bad (test_app.AppTest.test_bad)
Traceback (most recent call last):
  AssertionError: false

ERROR: test_error (test_app.AppTest.test_error)
Traceback (most recent call last):
  RuntimeError: boom

----------------------------------------------------------------------
Ran 5 tests in 0.001s

FAILED (failures=1, errors=1, skipped=1)
"""

    result = UnittestResultParser().parse(stdout="", stderr=output, exit_code=1)

    assert result.framework == "unittest"
    assert result.success is False
    assert result.passed == 2
    assert result.failed == 1
    assert result.errors == 1
    assert result.skipped == 1
    assert {item.nodeid for item in result.failing_tests} == {
        "test_app.AppTest.test_bad",
        "test_app.AppTest.test_error",
    }
    assert "AssertionError" in result.error_summary
    assert "RuntimeError" in result.error_summary


def test_unittest_parser_extracts_success() -> None:
    output = """
..
----------------------------------------------------------------------
Ran 2 tests in 0.001s

OK
"""

    result = UnittestResultParser().parse(stdout="", stderr=output, exit_code=0)

    assert result.success is True
    assert result.passed == 2
    assert result.failed == 0
    assert result.errors == 0
    assert result.skipped == 0
    assert result.confidence == "high"


def test_parse_test_result_falls_back_for_malformed_output() -> None:
    result = parse_test_result(
        framework="pytest",
        stdout="unstructured output",
        stderr="unknown failure",
        exit_code=1,
    )

    assert result.success is False
    assert result.confidence == "low"
    assert result.passed == 0
    assert "Unable to parse" in result.error_summary


def test_parse_shell_result_preserves_command_exit_code_and_log_paths(tmp_path: Path) -> None:
    stdout_log = tmp_path / "stdout.log"
    stderr_log = tmp_path / "stderr.log"
    record_path = tmp_path / "command.json"
    shell_result = ShellResult(
        operation_id="op-1",
        command="python -m pytest -q",
        argv=["python", "-m", "pytest", "-q"],
        cwd=tmp_path,
        exit_code=0,
        stdout="1 passed in 0.01s\n",
        stderr="",
        stdout_truncated=False,
        stderr_truncated=False,
        stdout_original_chars=17,
        stderr_original_chars=0,
        duration_seconds=0.01,
        timed_out=False,
        stdout_log=stdout_log,
        stderr_log=stderr_log,
        record_path=record_path,
    )

    result = parse_shell_result(framework="pytest", shell_result=shell_result)

    assert result.success is True
    assert result.command == "python -m pytest -q"
    assert result.exit_code == 0
    assert result.log_paths == (str(stdout_log), str(stderr_log))


def test_parse_shell_result_reads_full_logs_when_preview_is_truncated(
    tmp_path: Path,
) -> None:
    stdout_log = tmp_path / "stdout.log"
    stderr_log = tmp_path / "stderr.log"
    record_path = tmp_path / "command.json"
    stdout_log.write_text(
        "noise\n" * 100 + "9 passed, 1 skipped in 0.03s\n",
        encoding="utf-8",
    )
    stderr_log.write_text("", encoding="utf-8")
    shell_result = ShellResult(
        operation_id="op-2",
        command="python -m pytest -q",
        argv=["python", "-m", "pytest", "-q"],
        cwd=tmp_path,
        exit_code=0,
        stdout="noise\n[truncated: full output saved]\n",
        stderr="",
        stdout_truncated=True,
        stderr_truncated=False,
        stdout_original_chars=640,
        stderr_original_chars=0,
        duration_seconds=0.03,
        timed_out=False,
        stdout_log=stdout_log,
        stderr_log=stderr_log,
        record_path=record_path,
    )

    result = parse_shell_result(framework="pytest", shell_result=shell_result)

    assert result.success is True
    assert result.passed == 9
    assert result.skipped == 1
    assert result.confidence == "high"
