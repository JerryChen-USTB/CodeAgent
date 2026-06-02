"""Normalized test result objects shared by test framework adapters."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal


TestOutcome = Literal["failed", "error"]
ParserConfidence = Literal["high", "medium", "low"]


@dataclass(frozen=True)
class TestFailure:
    nodeid: str
    outcome: TestOutcome
    message: str = ""

    def to_json_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class TestResult:
    framework: str
    success: bool
    passed: int
    failed: int
    errors: int
    skipped: int
    total: int
    failing_tests: tuple[TestFailure, ...]
    error_summary: str
    timed_out: bool
    confidence: ParserConfidence
    raw_summary: str = ""
    command: str = ""
    exit_code: int | None = None
    log_paths: tuple[str, ...] = ()

    def to_json_dict(self) -> dict:
        data = asdict(self)
        data["failing_tests"] = [item.to_json_dict() for item in self.failing_tests]
        data["log_paths"] = list(self.log_paths)
        return data


def make_timeout_result(
    *,
    framework: str,
    stdout: str,
    stderr: str,
    exit_code: int | None = None,
    raw_summary: str = "",
) -> TestResult:
    summary = _first_non_empty(stderr, stdout, "Command timed out before tests completed.")
    return TestResult(
        framework=framework,
        success=False,
        passed=0,
        failed=0,
        errors=0,
        skipped=0,
        total=0,
        failing_tests=(),
        error_summary=summary,
        timed_out=True,
        confidence="high",
        raw_summary=raw_summary,
        exit_code=exit_code,
    )


def make_fallback_result(
    *,
    framework: str,
    stdout: str,
    stderr: str,
    exit_code: int | None,
    timed_out: bool,
) -> TestResult:
    if timed_out:
        return make_timeout_result(
            framework=framework,
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
        )
    details = _first_non_empty(stderr, stdout, f"exit_code={exit_code}")
    return TestResult(
        framework=framework,
        success=exit_code == 0,
        passed=0,
        failed=0,
        errors=0,
        skipped=0,
        total=0,
        failing_tests=(),
        error_summary=f"Unable to parse test output for {framework}. {details}",
        timed_out=False,
        confidence="low",
        raw_summary="",
        exit_code=exit_code,
    )


def _first_non_empty(*values: str) -> str:
    for value in values:
        stripped = value.strip()
        if stripped:
            return stripped
    return ""
