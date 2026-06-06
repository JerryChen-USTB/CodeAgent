"""Parser for unittest command output."""

from __future__ import annotations

import re

from codeagent.adapters.test_result import (
    TestFailure,
    TestResult,
    make_fallback_result,
    make_timeout_result,
)


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_RAN_RE = re.compile(r"\bRan\s+(?P<total>\d+)\s+tests?\s+in\s+", re.IGNORECASE)
_FAILED_RE = re.compile(r"FAILED\s+\((?P<body>[^)]*)\)", re.IGNORECASE)
_OK_RE = re.compile(r"^OK(?:\s+\((?P<body>[^)]*)\))?$", re.IGNORECASE | re.MULTILINE)
_COUNT_PAIR_RE = re.compile(r"(?P<kind>failures|errors|skipped)=(?P<count>\d+)")
_BLOCK_RE = re.compile(
    r"^(?P<label>FAIL|ERROR):\s+(?P<title>[^\n]+)\n(?P<body>.*?)(?=^(?:FAIL|ERROR):\s+|\n-+\nRan\s+|\Z)",
    re.MULTILINE | re.DOTALL,
)


class UnittestResultParser:
    framework = "unittest"

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
        ran_match = _RAN_RE.search(text)
        failed_match = _FAILED_RE.search(text)
        ok_match = _OK_RE.search(text)
        if ran_match is None or (failed_match is None and ok_match is None):
            return make_fallback_result(
                framework=self.framework,
                stdout=stdout,
                stderr=stderr,
                exit_code=exit_code,
                timed_out=False,
            )

        total = int(ran_match.group("total"))
        counts = _parse_result_counts(failed_match or ok_match)
        failed = counts.get("failures", 0)
        errors = counts.get("errors", 0)
        skipped = counts.get("skipped", 0)
        passed = max(total - failed - errors - skipped, 0)
        failures = _extract_failures(text)
        success = failed == 0 and errors == 0 and (exit_code in (0, None))
        raw_summary = (failed_match or ok_match).group(0).strip()
        return TestResult(
            framework=self.framework,
            success=success,
            passed=passed,
            failed=failed,
            errors=errors,
            skipped=skipped,
            total=total,
            failing_tests=failures,
            error_summary=_build_error_summary(
                failures=failures,
                raw_summary=raw_summary,
                stderr=stderr,
                exit_code=exit_code,
                success=success,
            ),
            timed_out=False,
            confidence="high",
            raw_summary=raw_summary,
            exit_code=exit_code,
        )


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _parse_result_counts(match: re.Match[str]) -> dict[str, int]:
    body = match.groupdict().get("body") or ""
    return {
        pair.group("kind").lower(): int(pair.group("count"))
        for pair in _COUNT_PAIR_RE.finditer(body)
    }


def _extract_failures(text: str) -> tuple[TestFailure, ...]:
    failures: list[TestFailure] = []
    for match in _BLOCK_RE.finditer(text):
        label = match.group("label").lower()
        title = match.group("title").strip()
        body = match.group("body").strip()
        failures.append(
            TestFailure(
                nodeid=_nodeid_from_title(title),
                outcome="error" if label == "error" else "failed",
                message=_compact_body(body),
            )
        )
    return tuple(failures)


def _nodeid_from_title(title: str) -> str:
    match = re.search(r"\((?P<nodeid>[^()]*)\)\s*$", title)
    if match:
        return match.group("nodeid").strip()
    return title.strip()


def _compact_body(body: str) -> str:
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    if not lines:
        return ""
    return "\n".join(lines[-3:])


def _build_error_summary(
    *,
    failures: tuple[TestFailure, ...],
    raw_summary: str,
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
    if raw_summary:
        lines.append(raw_summary)
    if stderr.strip() and not failures:
        lines.append(stderr.strip())
    if exit_code not in (None, 0):
        lines.append(f"exit_code={exit_code}")
    return "\n".join(line for line in lines if line)
