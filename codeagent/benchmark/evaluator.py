"""Artifact-based benchmark evaluation."""

from __future__ import annotations

import os
import json
import re
from dataclasses import dataclass
from pathlib import Path

from codeagent import filesystem as fs
from codeagent.benchmark.schemas import CaseEvaluation, CaseExecutionContext
from codeagent.runtime.commands import CommandApproval
from codeagent.tools.pytest_tools import parse_shell_result
from codeagent.tools.shell_tools import CommandDeniedError, ShellRunner


@dataclass(frozen=True)
class _OracleOutcome:
    success: bool | None
    failure_reason: str = ""


@dataclass(frozen=True)
class _AgentSelfTestOutcome:
    success: bool | None
    total: int | None
    command: str | None
    report_path: Path | None
    failure_reason: str = ""


class CaseEvaluator:
    """Evaluate public run artifacts and runner-only oracle commands."""

    def evaluate(
        self,
        *,
        context: CaseExecutionContext,
        run_dir: Path | None,
        final_status: str,
    ) -> CaseEvaluation:
        oracle = self._run_oracle(context)
        if run_dir is None:
            return CaseEvaluation(
                case_id=context.case_id,
                success=False,
                score=0.0,
                final_status=final_status,
                run_dir=None,
                run_case_dir=context.run_case_dir,
                failure_reason=_join_reasons(
                    "workflow did not produce a run directory",
                    oracle.failure_reason,
                ),
                oracle_success=oracle.success,
                oracle_command=context.oracle_command,
                oracle_logs_dir=context.oracle_logs_dir,
            )
        self_test = _read_agent_self_test(run_dir)
        final_report = run_dir / "final_report.md"
        artifacts = run_dir / "artifacts_index.json"
        missing = [
            path.name
            for path in (final_report, artifacts)
            if not path.exists()
        ]
        if missing:
            return CaseEvaluation(
                case_id=context.case_id,
                success=False,
                score=0.0,
                final_status=final_status,
                run_dir=run_dir,
                run_case_dir=context.run_case_dir,
                failure_reason=_join_reasons(
                    f"missing required artifact(s): {', '.join(missing)}",
                    self_test.failure_reason,
                    oracle.failure_reason,
                ),
                oracle_success=oracle.success,
                oracle_command=context.oracle_command,
                oracle_logs_dir=context.oracle_logs_dir,
                agent_test_success=self_test.success,
                agent_test_total=self_test.total,
                agent_test_command=self_test.command,
                agent_test_report=self_test.report_path,
            )
        oracle_passed = oracle.success is not False
        self_test_passed = self_test.success is True and (self_test.total or 0) > 0
        success = final_status == "succeeded" and oracle_passed and self_test_passed
        return CaseEvaluation(
            case_id=context.case_id,
            success=success,
            score=1.0 if success else 0.0,
            final_status=final_status,
            run_dir=run_dir,
            run_case_dir=context.run_case_dir,
            failure_reason=(
                ""
                if success
                else _join_reasons(
                    f"final_status={final_status}",
                    self_test.failure_reason,
                    oracle.failure_reason,
                )
            ),
            oracle_success=oracle.success,
            oracle_command=context.oracle_command,
            oracle_logs_dir=context.oracle_logs_dir,
            agent_test_success=self_test.success,
            agent_test_total=self_test.total,
            agent_test_command=self_test.command,
            agent_test_report=self_test.report_path,
        )

    def _run_oracle(self, context: CaseExecutionContext) -> _OracleOutcome:
        if not context.oracle_command:
            return _OracleOutcome(success=None)
        logs_dir = context.oracle_logs_dir or (context.run_case_dir / "oracle_logs")
        try:
            shell = ShellRunner(
                logs_dir=logs_dir,
                max_output_chars=context.task_config.runtime.log_truncation_chars,
            ).run(
                context.oracle_command,
                cwd=context.run_case_dir,
                timeout_seconds=(
                    context.oracle_timeout_seconds
                    or context.task_config.test_command.timeout_seconds
                ),
                approval=CommandApproval.benchmark_auto_approve(
                    operation_id=f"oracle_{context.case_id}",
                    reason="Runner-only benchmark oracle evaluation.",
                ),
                env=_oracle_env(context),
            )
        except (CommandDeniedError, ValueError, RuntimeError) as exc:
            return _OracleOutcome(
                success=False,
                failure_reason=f"oracle command failed before completion: {exc}",
            )
        parsed = parse_shell_result(
            framework=context.oracle_framework or context.task_config.test_framework,
            shell_result=shell,
        )
        if parsed.success:
            return _OracleOutcome(success=True)
        return _OracleOutcome(
            success=False,
            failure_reason=(
                "oracle tests failed: "
                f"passed={parsed.passed}, failed={parsed.failed}, "
                f"errors={parsed.errors}, exit_code={parsed.exit_code}"
            ),
        )


def _join_reasons(*reasons: str) -> str:
    return "; ".join(reason for reason in reasons if reason)


def _read_agent_self_test(run_dir: Path) -> _AgentSelfTestOutcome:
    candidates = [
        run_dir / "testing" / "test_result.json",
        run_dir / "testing" / "test_report.json",
    ]
    result_path = next((path for path in candidates if path.exists()), None)
    markdown_path = run_dir / "testing" / "test_report.md"
    report_path = markdown_path if markdown_path.exists() else result_path
    if result_path is None:
        return _AgentSelfTestOutcome(
            success=False,
            total=None,
            command=None,
            report_path=report_path,
            failure_reason="agent self-test result is missing",
        )
    try:
        data = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return _AgentSelfTestOutcome(
            success=False,
            total=None,
            command=None,
            report_path=report_path,
            failure_reason=f"agent self-test result is unreadable: {exc}",
        )
    total = _int_or_none(data.get("total"))
    if total is None:
        counts = [
            _int_or_zero(data.get("passed")),
            _int_or_zero(data.get("failed")),
            _int_or_zero(data.get("errors")),
            _int_or_zero(data.get("skipped")),
        ]
        total = sum(counts)
    success = bool(data.get("success"))
    command = data.get("command")
    failure_reason = ""
    timed_out = bool(data.get("timed_out"))
    if timed_out:
        collected = _collected_count_from_logs(data)
        if total <= 0 and collected is not None:
            total = collected
        success = False
        failure_reason = "agent self-test timed out"
    elif total <= 0:
        success = False
        failure_reason = "agent self-test collected zero tests"
    elif not success:
        failure_reason = "agent self-test failed"
    return _AgentSelfTestOutcome(
        success=success,
        total=total,
        command=str(command) if command else None,
        report_path=report_path,
        failure_reason=failure_reason,
    )


def _int_or_none(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _int_or_zero(value: object) -> int:
    parsed = _int_or_none(value)
    return parsed if parsed is not None else 0


def _collected_count_from_logs(data: dict[str, object]) -> int | None:
    log_paths = data.get("log_paths")
    if not isinstance(log_paths, list):
        return None
    best: int | None = None
    for raw_path in log_paths:
        if not isinstance(raw_path, str):
            continue
        try:
            text = fs.read_text(Path(raw_path))
        except OSError:
            continue
        for match in re.finditer(r"collected\s+(\d+)\s+items", text, re.IGNORECASE):
            best = int(match.group(1))
    return best


def _oracle_env(context: CaseExecutionContext) -> dict[str, str]:
    project_path = str(context.task_config.project_path)
    existing = os.environ.get("PYTHONPATH")
    return {
        "PYTHONPATH": (
            project_path if not existing else f"{project_path}{os.pathsep}{existing}"
        )
    }
