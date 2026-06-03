"""Artifact-based benchmark evaluation."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from codeagent.benchmark.schemas import CaseEvaluation, CaseExecutionContext
from codeagent.runtime.commands import CommandApproval
from codeagent.tools.pytest_tools import parse_shell_result
from codeagent.tools.shell_tools import CommandDeniedError, ShellRunner


@dataclass(frozen=True)
class _OracleOutcome:
    success: bool | None
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
                    oracle.failure_reason,
                ),
                oracle_success=oracle.success,
                oracle_command=context.oracle_command,
                oracle_logs_dir=context.oracle_logs_dir,
            )
        oracle_passed = oracle.success is not False
        success = final_status == "succeeded" and oracle_passed
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
                else _join_reasons(f"final_status={final_status}", oracle.failure_reason)
            ),
            oracle_success=oracle.success,
            oracle_command=context.oracle_command,
            oracle_logs_dir=context.oracle_logs_dir,
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
                timeout_seconds=context.task_config.test_command.timeout_seconds,
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


def _oracle_env(context: CaseExecutionContext) -> dict[str, str]:
    project_path = str(context.task_config.project_path)
    existing = os.environ.get("PYTHONPATH")
    return {
        "PYTHONPATH": (
            project_path if not existing else f"{project_path}{os.pathsep}{existing}"
        )
    }
