"""Parser for pytest command output."""

from __future__ import annotations

import re

from codeagent.adapters.test_result import (
    TestFailure,
    TestResult,
    make_fallback_result,
    make_timeout_result,
)


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_COUNT_RE = re.compile(
    r"(?P<count>\d+)\s+"
    r"(?P<kind>passed|failed|errors?|skipped|deselected|xfailed|xpassed|warnings?)\b",
    re.IGNORECASE,
)
_PYTEST_FAILURE_RE = re.compile(
    r"^(?P<label>FAILED|ERROR)\s+(?P<nodeid>\S[^\n]*?)(?:\s+-\s+(?P<message>.*))?$",
    re.MULTILINE,
)


class PytestResultParser:
    framework = "pytest"

    def parse(
        self,
        *,
        stdout: str,
        stderr: str = "",
        exit_code: int | None = None,
        timed_out: bool = False,
    ) -> TestResult:
        if timed_out:
            return make_timeout_result(
                framework=self.framework,
                stdout=stdout,
                stderr=stderr,
                exit_code=exit_code,
            )

        text = _strip_ansi("\n".join(part for part in (stdout, stderr) if part))
        summary_line, counts = _parse_counts(text)
        if not counts:
            return make_fallback_result(
                framework=self.framework,
                stdout=stdout,
                stderr=stderr,
                exit_code=exit_code,
                timed_out=False,
            )

        passed = counts.get("passed", 0)
        failed = counts.get("failed", 0)
        errors = counts.get("error", 0)
        skipped = counts.get("skipped", 0)
        failures = _extract_failures(text)
        success = failed == 0 and errors == 0 and (exit_code in (0, None))
        error_summary = _build_error_summary(
            failures=failures,
            summary_line=summary_line,
            stderr=stderr,
            exit_code=exit_code,
            success=success,
        )
        return TestResult(
            framework=self.framework,
            success=success,
            passed=passed,
            failed=failed,
            errors=errors,
            skipped=skipped,
            total=passed + failed + errors + skipped,
            failing_tests=failures,
            error_summary=error_summary,
            timed_out=False,
            confidence="high",
            raw_summary=summary_line,
            exit_code=exit_code,
        )


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _parse_counts(text: str) -> tuple[str, dict[str, int]]:
    summary_line = ""
    summary_counts: dict[str, int] = {}
    for line in text.splitlines():
        matches = list(_COUNT_RE.finditer(line))
        if not matches:
            continue
        normalized = {
            _normalize_kind(match.group("kind")): int(match.group("count"))
            for match in matches
        }
        if any(kind in normalized for kind in ("passed", "failed", "error", "skipped")):
            summary_line = line.strip("= ").strip()
            summary_counts = normalized
    return summary_line, summary_counts


def _normalize_kind(kind: str) -> str:
    lowered = kind.lower()
    if lowered.startswith("error"):
        return "error"
    if lowered.endswith("s"):
        return lowered[:-1]
    return lowered


def _extract_failures(text: str) -> tuple[TestFailure, ...]:
    seen: set[tuple[str, str]] = set()
    failures: list[TestFailure] = []
    for match in _PYTEST_FAILURE_RE.finditer(text):
        label = match.group("label").lower()
        outcome = "error" if label == "error" else "failed"
        nodeid = match.group("nodeid").strip()
        message = (match.group("message") or "").strip()
        key = (outcome, nodeid)
        if key in seen:
            continue
        seen.add(key)
        failures.append(TestFailure(nodeid=nodeid, outcome=outcome, message=message))
    return tuple(failures)


def _build_error_summary(
    *,
    failures: tuple[TestFailure, ...],
    summary_line: str,
    stderr: str,
    exit_code: int | None,
    success: bool,
) -> str:
    if success:
        return ""
    lines = [
        f"{failure.outcome.upper()} {failure.nodeid}"
        + (f": {failure.message}" if failure.message else "")
        for failure in failures
    ]
    if summary_line:
        lines.append(summary_line)
    if stderr.strip():
        lines.append(stderr.strip())
    if exit_code not in (None, 0):
        lines.append(f"exit_code={exit_code}")
    return "\n".join(line for line in lines if line)
