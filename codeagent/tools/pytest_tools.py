"""Tool helpers for normalizing test command results."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from codeagent.adapters.pytest_adapter import PytestResultParser
from codeagent.adapters.test_result import TestResult, make_fallback_result
from codeagent.adapters.unittest_adapter import UnittestResultParser
from codeagent.runtime.commands import ShellResult


def parse_test_result(
    *,
    framework: str,
    stdout: str,
    stderr: str = "",
    exit_code: int | None = None,
    timed_out: bool = False,
) -> TestResult:
    normalized = framework.strip().lower()
    if normalized in {"pytest", "py.test"}:
        return PytestResultParser().parse(
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            timed_out=timed_out,
        )
    if normalized in {"unittest", "python-unittest"}:
        return UnittestResultParser().parse(
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            timed_out=timed_out,
        )
    return make_fallback_result(
        framework=normalized or "unknown",
        stdout=stdout,
        stderr=stderr,
        exit_code=exit_code,
        timed_out=timed_out,
    )


def parse_shell_result(*, framework: str, shell_result: ShellResult) -> TestResult:
    stdout = _output_for_parsing(
        preview=shell_result.stdout,
        truncated=shell_result.stdout_truncated,
        log_path=shell_result.stdout_log,
    )
    stderr = _output_for_parsing(
        preview=shell_result.stderr,
        truncated=shell_result.stderr_truncated,
        log_path=shell_result.stderr_log,
    )
    result = parse_test_result(
        framework=framework,
        stdout=stdout,
        stderr=stderr,
        exit_code=shell_result.exit_code,
        timed_out=shell_result.timed_out,
    )
    return replace(
        result,
        command=shell_result.command,
        exit_code=shell_result.exit_code,
        log_paths=(str(shell_result.stdout_log), str(shell_result.stderr_log)),
    )


def _output_for_parsing(*, preview: str, truncated: bool, log_path: Path) -> str:
    if not truncated:
        return preview
    try:
        return log_path.read_text(encoding="utf-8")
    except OSError:
        return preview
